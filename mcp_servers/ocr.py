"""OCR step of the pipeline: an MCP server over SSE (port 8001, path /sse).

Reads a fictional order image from /data/samples (read-only), runs Tesseract in Portuguese, takes out the orders to
the model and masks the PII on every line BEFORE returning: the raw text never leaves this process. check_image runs
every check but Tesseract, so `cli run` refuses a bad file before the first model turn; no agent has it (tool_filter).
"""
import asyncio
import os
import re
import threading
from collections.abc import Callable, Sequence
from pathlib import Path, PureWindowsPath
from statistics import median
from typing import Annotated, Any

import pytesseract
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from PIL import Image, UnidentifiedImageError
from starlette.responses import JSONResponse

from catalogo import EXAM_MODIFIERS, LIST_MARKER, MASK_TAG, MIN_SCORE, QUALIFIERS, matcher, words
from guardrails import intent
from guardrails.injection import MARKER, join_split_orders, neutralize_joined
from guardrails.pii import exam_like, exams_on, mask_page
from guardrails.pii_rules import NAME_TAG, REMOVED_TAG, STRUCTURE
from leitura import NOT_ANCHORS, VERSION, OcrReading
from mcp_servers.arguments import or_default, serve, transport_security
from mcp_servers.preprocessamento import OcrLine, SidewaysImage, on_white, read_ocr_lines
from mcp_servers.qualidade import quality_problem

# One thread per Tesseract run: requests already run in parallel, and with OpenMP's
# default (all cores per run) 8 parallel requests took 88 s instead of 2 s.
os.environ.setdefault('OMP_THREAD_LIMIT', '1')
SAMPLES_DIR = Path(os.environ.get('SAMPLES_DIR', '/data/samples'))
FORMATS = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG'}  # allowed extension -> Pillow format
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILENAME_LENGTH = 100
MAX_PIXELS = 25_000_000  # checked from the header, before decoding
OCR_TIMEOUT_SECONDS = 30
# reading_marks: a gap of GAP letter heights ends a block; letters under SMALL of the page's, or LIGHT tones lighter.
GAP, SMALL, LIGHT = 1.5, 0.6, 100
NAMES = {term: exam['name'] for term, exam in matcher().written.items()}  # exam_terms
ASKED_KINDS = ('request', 'uncertain', 'table', 'form')  # a line whose exams are booked or asked (leitura.Intent)
NOT_SUSPECT = 100.0  # the reading of a line without one of its own, for clean_page


def resolve_sample(filename: str) -> Path:
    """Map a bare file name to /data/samples, or raise ToolError with a clear message."""
    if not isinstance(filename, str) or not filename.strip():
        raise ToolError('filename deve ser o nome de um arquivo, ex.: pedido.png.')  # empty, or not text (None, 123)
    if len(filename) > MAX_FILENAME_LENGTH:
        raise ToolError(f'Nome de arquivo longo demais (máximo {MAX_FILENAME_LENGTH} caracteres).')
    if Path(filename).name != filename or PureWindowsPath(filename).name != filename:
        raise ToolError('Informe só o nome do arquivo, sem pastas nem caminho.')
    path = SAMPLES_DIR / filename
    if path.suffix.lower() not in FORMATS:
        raise ToolError('Extensão inválida; use .png, .jpg ou .jpeg.')
    # resolve(): a symlink inside samples/ could point elsewhere
    if not path.is_file() or path.resolve().parent != SAMPLES_DIR.resolve():
        raise ToolError(f'Arquivo "{filename}" não encontrado em {SAMPLES_DIR}.')
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ToolError(f'Arquivo grande demais (máximo {MAX_FILE_BYTES // (1024 * 1024)} MB).')
    return path


def read_lines(path: Path) -> list[OcrLine]:
    """Run Tesseract (Portuguese) and return the non-empty text lines, each with its confidence (0-100) and box."""
    return checked_image(path, lambda image: read_ocr_lines(image, OCR_TIMEOUT_SECONDS))


def checked_image(path: Path, then: Callable[[Image.Image], Any]) -> Any:
    """then(image) on the decoded image, once it passed every check before Tesseract; each refusal is a
    ToolError with the reason. The reading and check_image share it, so they refuse the same files."""
    try:
        with Image.open(path) as image:
            # The real format must match the name: a GIF or BMP renamed to .png is refused.
            if image.format != FORMATS[path.suffix.lower()]:
                raise ToolError('O conteúdo do arquivo não corresponde à extensão (use PNG ou JPEG).')
            if image.width * image.height > MAX_PIXELS:
                raise ToolError('Imagem com resolução grande demais.')
            image = on_white(image)  # a transparent background would read as a black page
            if problem := quality_problem(image):  # a photo the OCR would barely read
                raise ToolError(problem)
            return then(image)
    except SidewaysImage as error:
        raise ToolError(str(error)) from None
    except Image.DecompressionBombError:
        raise ToolError('Imagem com resolução grande demais.') from None
    except UnidentifiedImageError:
        if path.read_bytes()[:5] == b'%PDF-':
            raise ToolError('O arquivo é um PDF, não uma imagem: exporte a página como PNG ou JPEG.') from None
        raise ToolError('O arquivo não é uma imagem válida (use PNG ou JPEG).') from None
    except RuntimeError:  # pytesseract: engine failure or timeout
        raise ToolError('O OCR falhou ou demorou demais para esta imagem.') from None
    except (OSError, ValueError, SyntaxError, EOFError):  # a truncated or hostile file, while Pillow decodes it
        raise ToolError('Imagem corrompida ou incompleta.') from None


def mask_lines(lines: Sequence[OcrLine | str]) -> dict:
    """The reply but its version (leitura.OcrReading): join an order to the model split over lines and neutralize the
    orders, read what each line asks for as written (guardrails/intent.py), mask the PII (guardrails/pii.py). A list item
    masked away is 'unrecognized'; an order removed, or a masked name on an exam line, leaves the page not clean.
    exam_lines, the only lines the model reads: names_an_exam, not negated, history or prep, and a masked name only after
    the exam of a list item (exam_before_name)."""
    read = [line if isinstance(line, OcrLine) else OcrLine(line) for line in lines]
    joined, sources = join_split_orders([line.text for line in read])
    breaks, odd = reading_marks(read) if len(joined) == len(read) else (frozenset(), [False] * len(joined))
    safe, removed = neutralize_joined(joined)  # prompt injection: the text goes to the LLM
    masked, counts = mask_page(safe)
    kinds, contest, unlinked = intent.read_page(joined, breaks)
    kinds = ['unrecognized' if kind in ASKED_KINDS and unrecognized_request(line, out) else kind
             for kind, line, out in zip(kinds, safe, masked, strict=True)]
    confidence, readings = joined_readings(read, sources)
    off = intent.clean_page(joined, masked, kinds, odd, readings)
    exam_lines = [at for at, line in enumerate(masked) if names_an_exam(line)]
    named = any(NAME_TAG in masked[at] for at in exam_lines)  # a name's line never books alone
    text_removed = counts.pop('TEXTO_REMOVIDO', 0)
    return {'lines': masked, 'line_confidence': confidence, 'line_intent': kinds, 'pii_masked': counts,
            'instructions_removed': removed, 'text_removed': text_removed, 'contested_exams': intent.contested(joined, contest),
            'cancel_unlinked': unlinked, 'off_list': off, 'page_clean': not (removed or unlinked or named or off),
            'exam_terms': [[[term, NAMES[term]] for term in sorted(exams_on(line))] for line in masked],
            'exam_lines': [at for at in exam_lines if kinds[at] not in NOT_ANCHORS and exam_before_name(masked[at])]}


def joined_readings(read: list[OcrLine], sources: list[range]) -> tuple[list[float] | None, list[float]]:
    """(line_confidence, the readings clean_page weighs) of each joined line: the lowest of the lines it joins; for
    clean_page, a joined line has no reading of its own (NOT_SUSPECT). Lines given as text: no line_confidence."""
    own = [read[lines.start].confidence if len(lines) == 1 else None for lines in sources]
    readings = [NOT_SUSPECT if reading is None else reading for reading in own]
    known = [line.confidence for line in read if line.confidence is not None]
    return ([min(known[i] for i in lines) for lines in sources] if len(known) == len(read) else None), readings


def read_reply(lines: list[OcrLine]) -> dict:
    """extract_exam_text's reply for the lines read: leitura.OcrReading, as a plain object."""
    return OcrReading(version=VERSION, **mask_lines(lines)).model_dump(mode='json')


def reading_marks(read: list[OcrLine]) -> tuple[frozenset[int], list[bool]]:
    """(lines with a gap above them: Tesseract's blocks of text, lines in letters far smaller or lighter than the
    page's) from where Tesseract put each line (OcrLine.box); nothing when a line has no box."""
    boxes = [line.box for line in read if line.box]
    if not boxes or len(boxes) < len(read):
        return frozenset(), [False] * len(read)
    height, ink = median(box[2] for box in boxes), median(box[3] for box in boxes)
    return (frozenset(i for i in range(1, len(boxes)) if boxes[i][0] - boxes[i - 1][1] > GAP * height),
            [box[2] < SMALL * height or box[3] > ink + LIGHT for box in boxes])


def names_an_exam(line: str) -> bool:
    """Whether a masked line resolves to the catalog, even misread: a whole exam name or, structure ("Obs:") and masked
    values aside, a word one OCR error from an exam word (not a qualifier) or text the search finds (MIN_SCORE)."""
    text = LIST_MARKER.sub(' ', MASK_TAG.sub(' ', line))
    rest = [word for word in words(text).split() if word not in STRUCTURE]
    return bool(exams_on(text)) or matcher().search_score(' '.join(rest)) >= MIN_SCORE or any(
        word not in QUALIFIERS and exam_like(word) for word in rest)


def exam_before_name(line: str) -> bool:
    """Whether the model may read a masked line that has a name: only a list item whose exam is written before the
    name ("- Hemograma completo [NOME]"), with [NOME] in place. A name line ("[NOME] ferro", "Ferro, [NOME]": a surname
    that is a catalog word) stays hidden; either way a name on an exam line keeps the page asked (page_clean)."""
    head = line.split(NAME_TAG, 1)[0]
    return head == line or bool(LIST_MARKER.match(head)) and names_an_exam(head)


def unrecognized_request(line: str, masked: str) -> bool:
    """Whether a list item ("4) Ressonancia magnetica de cranio") left the OCR as nothing but [TEXTO_REMOVIDO], or
    as a modifier ("[TEXTO_REMOVIDO] total"): a request the catalog does not know, reported by its line's number
    only. An order to the model already removed, personal data masked on the line, or a few letters of junk are not."""
    return only_a_modifier_left(line, masked) or removed_whole(line, masked)


def only_a_modifier_left(line: str, masked: str) -> bool:
    """[TEXTO_REMOVIDO] and only modifiers of an exam left ("[TEXTO_REMOVIDO] total"), on a line written without tags."""
    left = words(LIST_MARKER.sub('', masked).replace(REMOVED_TAG, ' '))
    return (MARKER not in line and REMOVED_TAG in masked and not MASK_TAG.search(line) and bool(left)
            and all(word in EXAM_MODIFIERS for word in left.split()) and not exams_on(left))


def removed_whole(line: str, masked: str) -> bool:
    """A list item of 6 letters or more, and no order to the model, that left as nothing but [TEXTO_REMOVIDO] and marks."""
    item = LIST_MARKER.match(line)
    if not item or MARKER in line or len(re.findall(r'[^\W\d_]', MASK_TAG.sub(' ', line[item.end():]))) < 6:
        return False
    return bool(re.fullmatch(r'(?:\[TEXTO_REMOVIDO\]|[^\w\[\]])+', LIST_MARKER.sub('', masked, count=1)))


server = MCPServer('ocr-exams', instructions='Extrai o texto de um pedido médico fictício, com PII mascarada.')
SLOTS, SLOT_WAIT_SECONDS = threading.BoundedSemaphore(3), 20  # images decoded at once (~330 MB at 25 MP), wait for one


def in_slot(work: Callable[..., Any], *args: Any) -> Any:  # in the thread: a cancelled call keeps its slot until done
    if not SLOTS.acquire(timeout=SLOT_WAIT_SECONDS):
        raise ToolError('OCR ocupado com outras imagens; tente de novo em instantes.')
    try:
        return work(*args)
    finally:
        SLOTS.release()


@server.tool()
async def extract_exam_text(filename: Annotated[str, or_default('')]) -> dict:
    """Read /data/samples/<filename> with OCR; returns {lines, line_confidence, line_intent, contested_exams,
    cancel_unlinked, page_clean, off_list, exam_lines, pii_masked, instructions_removed, text_removed}.

    PII already masked; line_confidence holds one 0-100 value per returned line, and line_intent one
    kind (request, negated, history, uncertain, prep, unrecognized, table), in the same order.
    """
    path = resolve_sample(filename)
    return read_reply(await asyncio.to_thread(in_slot, read_lines, path))


@server.tool()
async def check_image(filename: Annotated[str, or_default('')]) -> dict:
    """Check /data/samples/<filename> as extract_exam_text would (name, size, real format, resolution,
    integrity, photo quality), without the OCR; returns {format, width, height}, or the same error.

    For `cli run`, before the first model turn; no agent has it.
    """
    path = resolve_sample(filename)
    width, height = await asyncio.to_thread(in_slot, checked_image, path, lambda image: image.size)
    return {'format': FORMATS[path.suffix.lower()], 'width': width, 'height': height}


@server.custom_route('/health', methods=['GET'])
async def health(request):
    return JSONResponse({'status': 'ok', 'tesseract': str(pytesseract.get_tesseract_version())})


SECURITY = transport_security('ocr:8001')

if __name__ == '__main__':
    serve(server, SECURITY, 8001, 'extract_exam_text', 'check_image')
