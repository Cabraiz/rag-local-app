"""OCR step of the pipeline: an MCP server over SSE (port 8001, path /sse).

Reads a fictional request image from /data/samples (read-only volume), runs
Tesseract in Portuguese and masks PII on every line with guardrails.pii.mask_page
BEFORE returning. The raw text never leaves this process, so names, documents
and contacts never reach the LLM. Lines that read as orders to the model
(prompt injection) are taken out first and counted in instructions_removed;
what is left of them does not look like an exam, so it leaves as [TEXTO_REMOVIDO].

check_image runs every check of the reading but Tesseract, so `cli run` refuses a missing or
unreadable file before the first model turn. No spec declares it, so no agent sees it (tool_filter).
"""
import asyncio
import os
import re
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from typing import Annotated, Any

import pytesseract
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from PIL import Image, UnidentifiedImageError
from starlette.responses import JSONResponse

from catalogo import EXAM_MODIFIERS, LIST_MARKER, words
from guardrails import intent
from guardrails.injection import MARKER, join_split_orders, neutralize_joined
from guardrails.pii import exams_on, mask_page
from mcp_servers.arguments import or_default, quiet_logs
from mcp_servers.preprocessamento import ImagemGirada, confianca_por_linha, ler_linhas, sobre_branco
from mcp_servers.qualidade import quality_problem

# One thread per Tesseract run: requests already run in parallel, and with OpenMP's
# default (all cores per run) 8 parallel requests took 88 s instead of 2 s.
os.environ.setdefault('OMP_THREAD_LIMIT', '1')
SAMPLES_DIR = Path(os.environ.get('SAMPLES_DIR', '/data/samples'))
FORMATS = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG'}  # allowed extension -> Pillow format
ALLOWED_SUFFIXES = set(FORMATS)
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILENAME_LENGTH = 100
MAX_PIXELS = 25_000_000  # checked from the header, before decoding
OCR_TIMEOUT_SECONDS = 30


def resolve_sample(filename: str) -> Path:
    """Map a bare file name to /data/samples, or raise ToolError with a clear message."""
    if not isinstance(filename, str) or not filename.strip():
        raise ToolError('Informe o nome do arquivo, por exemplo "pedido.png".')
    if len(filename) > MAX_FILENAME_LENGTH:
        raise ToolError(f'Nome de arquivo longo demais (máximo {MAX_FILENAME_LENGTH} caracteres).')
    if Path(filename).name != filename or PureWindowsPath(filename).name != filename:
        raise ToolError('Informe só o nome do arquivo, sem pastas nem caminho.')
    path = SAMPLES_DIR / filename
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ToolError('Extensão inválida; use .png, .jpg ou .jpeg.')
    # resolve(): a symlink inside samples/ could point elsewhere
    if not path.is_file() or path.resolve().parent != SAMPLES_DIR.resolve():
        raise ToolError(f'Arquivo "{filename}" não encontrado em {SAMPLES_DIR}.')
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ToolError(f'Arquivo grande demais (máximo {MAX_FILE_BYTES // (1024 * 1024)} MB).')
    return path


def read_lines(path: Path) -> list[str]:
    """Run Tesseract (Portuguese) and return the non-empty text lines, each a str with .confianca (0-100)."""
    return checked_image(path, lambda image: ler_linhas(image, OCR_TIMEOUT_SECONDS))


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
            image = sobre_branco(image)  # a transparent background would read as a black page
            if problem := quality_problem(image):  # a photo the OCR would barely read
                raise ToolError(problem)
            return then(image)
    except ImagemGirada as error:
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


def mask_lines(lines: list[str], joined: list[str] | None = None) -> dict:
    """Neutralize instructions to the model, read what each line asks for, then mask PII (line by
    line, plus a CPF split in two lines).

    line_intent holds one kind per returned line (guardrails/intent.py), read from the whole page as
    written, before the guard and the mask. A list item whose text the safety net removed whole, or
    whose exam name it removed leaving only a modifier ("[TEXTO_REMOVIDO] total"), is 'unrecognized'
    (unrecognized_request). contested_exams: intent.contested(), also of each order to the model removed
    here (codes and names only). pii_masked counts personal data by type. Kept apart, as they are not PII:
    instructions_removed, the lines where an order to the model was replaced, and text_removed, the pieces
    that did not look like an exam ([TEXTO_REMOVIDO]). `joined` is join_split_orders(lines)[0] if known.
    """
    if joined is None:
        joined = join_split_orders(lines)[0]
    lines, removed = neutralize_joined(joined)  # prompt injection: the text goes to the LLM
    masked, counts = mask_page(lines)
    kinds, contest = intent.read_page(joined)
    kinds = ['unrecognized' if kind in ('request', 'uncertain', 'table') and unrecognized_request(line, safe) else kind
             for kind, line, safe in zip(kinds, lines, masked, strict=True)]
    contest = [why or ('instruction' if MARKER in line else None) for why, line in zip(contest, lines, strict=True)]
    text_removed = counts.pop('TEXTO_REMOVIDO', 0)
    return {'lines': masked, 'line_intent': kinds, 'pii_masked': counts, 'instructions_removed': removed,
            'text_removed': text_removed, 'contested_exams': intent.contested(joined, contest)}


def unrecognized_request(line: str, masked: str) -> bool:
    """Whether a list item of the order ("4) Ressonancia magnetica de cranio") left the OCR as nothing
    but [TEXTO_REMOVIDO]: a request the catalog does not know, which the CLI must not drop in silence.
    Only the line's number is reported, never its text. An order to the model already removed
    (instructions_removed), a name or other personal data masked on the line, or a few letters of
    junk are not one. Nor is it silent when only the exam's name went and its modifier stayed
    ("[TEXTO_REMOVIDO] total", "- [TEXTO_REMOVIDO] livre"): that is a request too."""
    left = words(LIST_MARKER.sub('', masked).replace('[TEXTO_REMOVIDO]', ' '))
    if MARKER not in line and '[TEXTO_REMOVIDO]' in masked and not re.search(r'\[[A-Z_]+\]', line) and left \
            and all(word in EXAM_MODIFIERS for word in left.split()) and not exams_on(left):
        return True
    item = LIST_MARKER.match(line)
    letters = re.findall(r'[^\W\d_]', re.sub(r'\[[A-Z_]+\]', ' ', line[item.end():])) if item else []
    if not item or MARKER in line or len(letters) < 6:
        return False
    rest = LIST_MARKER.sub('', masked, count=1)
    return bool(re.fullmatch(r'(?:\[TEXTO_REMOVIDO\]|[^\w\[\]])+', rest))


server = MCPServer('ocr-exams', instructions='Extrai o texto de um pedido médico fictício, com PII mascarada.')


@server.tool()
async def extract_exam_text(filename: Annotated[str, or_default('')]) -> dict:
    """Read /data/samples/<filename> with OCR; returns {lines, line_confidence, line_intent, contested_exams,
    pii_masked, instructions_removed, text_removed}.

    PII already masked; line_confidence holds one 0-100 value per returned line, and line_intent one
    kind (request, negated, history, uncertain, prep, unrecognized, table), in the same order.
    """
    if not filename.strip():  # empty or not text (None, 123, a list)
        raise ToolError('filename deve ser o nome de um arquivo, ex.: pedido.png.')
    path = resolve_sample(filename)
    lines = await asyncio.to_thread(read_lines, path)
    joined, sources = join_split_orders(lines)  # once: the guard and the confidence share it
    return {**mask_lines(lines, joined), 'line_confidence': confianca_por_linha(lines, origens=sources)}


@server.tool()
async def check_image(filename: Annotated[str, or_default('')]) -> dict:
    """Check /data/samples/<filename> as extract_exam_text would (name, size, real format, resolution,
    integrity, photo quality), without the OCR; returns {format, width, height}, or the same error.

    For `cli run`, before the first model turn; no agent has it.
    """
    if not filename.strip():
        raise ToolError('filename deve ser o nome de um arquivo, ex.: pedido.png.')
    path = resolve_sample(filename)
    width, height = await asyncio.to_thread(checked_image, path, lambda image: image.size)
    return {'format': FORMATS[path.suffix.lower()], 'width': width, 'height': height}


@server.custom_route('/health', methods=['GET'])
async def health(request):
    return JSONResponse({'status': 'ok', 'tesseract': str(pytesseract.get_tesseract_version())})


SECURITY = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=['ocr:8001', 'localhost:*', '127.0.0.1:*'],
    allowed_origins=[],
)

# uvicorn closes idle connections after 5 s, the same 5 s after which the MCP client's pool
# drops them: a POST sent at that moment was lost and its call never returned (python-sdk #906).
KEEP_ALIVE_SECONDS = 75

if __name__ == '__main__':
    import uvicorn
    quiet_logs('extract_exam_text', 'check_image')
    uvicorn.run(server.sse_app(transport_security=SECURITY, host='0.0.0.0'), host='0.0.0.0', port=8001,
                timeout_keep_alive=KEEP_ALIVE_SECONDS, log_level=server.settings.log_level.lower())
