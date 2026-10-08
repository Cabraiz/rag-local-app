"""RAG step of the pipeline: an MCP server over SSE (port 8002, path /sse).

Retrieval-augmented lookup of exam codes in the catalog (data/exams.json), scored as
catalogo.py describes: shared words or character similarity, whichever is higher. The
agent receives ranked codes from the catalog and never has to invent one.
"""
import functools
import itertools
import re
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from catalogo import CLASSES, CONNECTIVES, LIST_MARKER, MIN_SCORE, QUALIFIERS, matcher, normalize
from mcp_servers.arguments import or_default, quiet_logs

MAX_TOP_K = 10
MAX_QUERY_LENGTH = 200
MATCHER = matcher()  # the catalog, read when this module is imported


def search(query: str, top_k: int = 3) -> list[dict]:
    """Return up to top_k catalog exams ranked by score (ties broken by code), each with the 'term' (its
    name or the synonym) the score came from."""
    if not isinstance(query, str) or not normalize(query):
        raise ToolError('Informe o nome de um exame para buscar.')
    if len(query) > MAX_QUERY_LENGTH:
        raise ToolError(f'Consulta longa demais (máximo {MAX_QUERY_LENGTH} caracteres).')
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= MAX_TOP_K:
        raise ToolError(f'top_k deve ser um inteiro entre 1 e {MAX_TOP_K}.')
    scored = [{'code': exam['code'], 'name': exam['name'], 'score': round(score, 2), 'term': written}
              for score, written, exam in MATCHER.closest(normalize(query)) if score >= MIN_SCORE]
    scored.sort(key=lambda hit: (-hit['score'], hit['code']))
    return scored[:top_k]


# A line of an order may hold several exams: "Colesterol total e Triglicerideos", "TSH, T4 livre".
SEPARATOR = re.compile(r'\s+e\s+|\s*[,+;/:]\s*', re.IGNORECASE)  # "E" too: "COLESTEROL TOTAL E TGO"


def split_exams(query: str, short: bool = False) -> list[str]:
    """The exam names of one line, split on " e ", ",", "+", ";", "/" and a glued "e" (unglue: "TSHe T4 livre"); pieces
    whose join is a catalog name stay together ("HIV antigeno e anticorpos"), and one of under 2 letters goes ("5;")."""
    parts, separators, written = [], [], SEPARATOR.findall(query)
    for index, part in enumerate(SEPARATOR.split(query)):
        cut, between = unglue(part)
        separators += [*written[index - 1:index], *between]  # the separator before the part, then its cuts
        parts += cut

    def joined(start, stop):  # parts start..stop with the separators written between them
        return ''.join(part + sep for part, sep in zip(parts[start:stop], separators[start:stop], strict=True)) + parts[stop]

    pieces, start = [], 0
    while start < len(parts):
        end = start
        for stop in range(start + 1, len(parts)):  # the longest run of parts that is one catalog name
            if normalize(joined(start, stop)) in MATCHER.searched:
                end = stop
        piece = joined(start, end)
        if len(normalize(piece).replace(' ', '')) >= (1 if short else 2):  # short: "Vitamina B12 e D"
            pieces.append(piece.strip())
        start = end + 1
    return pieces


# A piece is often part of the exam next to it: "Toxoplasmose IgG e IgM", "PSA total e livre", "creatinina, urina 24h".
OWN_EXAM, COMPLETED = 0.80, 0.90  # a piece below OWN_EXAM is no exam alone; a completion from COMPLETED is one
# Last words a catalog name takes after the same first words ("Toxoplasmose" IgG/IgM, "PSA" total/livre):
# a piece of one of them after a name ending in another is an ellipsis of that name.
ENDINGS: dict = {}
for _term in MATCHER.searched:
    if len(_term.split()) >= 2:
        ENDINGS.setdefault(' '.join(_term.split()[:-1]), set()).add(_term.split()[-1])
# The generic dosages of an antibody class: booked alone only from a line of complete exams, never
# from a line that names a disease ("Anti HAV IgG e IgM", "IgG e IgM para toxoplasmose").
GENERIC = {exam['code'] for exam in MATCHER.exams if all(set(term.split()) <= CLASSES | {'total'} for term in exam['terms'])}


def qualifier(piece: str) -> bool:
    """A piece of only words that qualify the exam before it (a sample, a time): not searched on its own."""
    return all(word in QUALIFIERS or word in ('fezes', 'sem') or re.fullmatch(r'\d+h?', word)
               for word in normalize(piece).split())


@functools.lru_cache(maxsize=4096)
def best_score(text: str) -> float:
    found = search(text, 1)
    return found[0]['score'] if found else 0.0


def glued(word: str, without: str, with_e: str, other: str) -> bool:
    """Whether `word`, read with a connective "e" glued to it ("TSHe T4 livre", "Ureiae Creatinina"), is a cut between
    `with_e` (its side of the line, `without` once the "e" is gone) and `other` (the rest): when it is no catalog word
    ("Lipase", "Sangue"), its side without the "e" is a name from COMPLETED and better than with it ("TSH" 1,00,
    "TSHe" 0,86) and the other side is an exam alone (OWN_EXAM). The piece keeps the "e": "TSHe" is TSH at 0,86."""
    without, with_e, other = (LIST_MARKER.sub('', text) for text in (without, with_e, other))  # "1) TSHe T4"
    if normalize(word) in MATCHER.vocabulary or len(normalize(word).replace(' ', '')) < 3 \
            or not normalize(without) or not normalize(other):
        return False
    score = best_score(without)
    return score >= COMPLETED and score > best_score(with_e) and best_score(other) >= OWN_EXAM


def unglue(part: str) -> tuple[list[str], list[str]]:
    """(pieces, the spaces between them) of a part of a line without separators, cut where the OCR
    glued an "e" to the end of a word ("TSHe | T4 livre") or to the start of the next one ("TSH |
    eT4 livre"). A part with no such word is one piece."""
    pieces, between, start = [], [], 0
    tokens = list(re.finditer(r'\S+', part))
    for before, after in itertools.pairwise(tokens):
        left, right = part[start:before.end()], part[after.start():]
        if (before.group()[-1] in 'eE' and glued(before.group(), left[:-1], left, right)) or \
                (after.group()[0] in 'eE' and glued(after.group(), right[1:], right, left)):
            pieces.append(left)
            between.append(part[before.end():after.start()])
            start = after.start()
    return [*pieces, part[start:]], between


def ahead(previous: str, piece: str, weak: bool) -> str | None:
    """The piece completed with the first words of the piece before it ("PSA total" + "livre" -> "PSA
    livre"), when that is a catalog name, or, for a piece that is no exam alone, an exam of COMPLETED."""
    head = previous.split()
    for size in range(len(head) - 1, 0, -1):
        text = ' '.join([*head[:size], piece])
        if normalize(text) in MATCHER.searched or (weak and best_score(text) >= COMPLETED):
            return text
    return None


def behind(piece: str, following: str) -> str | None:
    """The piece completed with the last words of the piece after it ("IgG" + "para toxoplasmose" ->
    "IgG toxoplasmose"), when its words are exactly those of a catalog name."""
    tail = following.split()
    for size in range(1, len(tail)):
        text = ' '.join([piece, *tail[-size:]])
        if frozenset(normalize(text).split()) in MATCHER.word_sets:
            return text
    return None


def exact(piece: str) -> str | None:
    """The catalog name whose words are the piece's words without its connectives ("IgM para
    toxoplasmose" -> "toxoplasmose igm"), or None."""
    words = frozenset(normalize(piece).split()) - CONNECTIVES
    return next((term for term in MATCHER.searched if frozenset(term.split()) == words), None) if words else None


def partial(text: str, previous: str | None, code: str, line: set, complete: set, serology: bool) -> bool:
    """Whether a piece is only part of an exam the line names, so its hit must not book alone: with other words of the
    line it is another exam's name; it repeats the ending of the name before it ("Chagas IgG e IgM"); or it is a generic
    class on a line with words that are no complete exam ("Anti HAV IgG e IgM") or a disease's (`serology`)."""
    words = set(normalize(text).split())
    if any(words < other <= line and other_code != code for other, other_code in MATCHER.word_sets.items()):
        return True
    if previous and len(words) == 1 and len(previous.split()) >= 2:
        last = normalize(previous).split()[-1]
        if any({last, *words} <= endings for endings in ENDINGS.values()):
            return True
    return code in GENERIC and (serology or bool(line - CLASSES - CONNECTIVES - complete - words))  # 'total' of IgE total


def search_line(query: str, top_k: int = 3) -> list[dict]:
    """search() for every exam of the line: a line of one piece, or of only separators, is searched whole; otherwise each
    piece gets its own top_k hits with its 'piece' as written, completed by its neighbour when it is part of that exam
    ("IgM" after "Toxoplasmose IgG"), a sample or a time ("urina 24h") is not searched, and the hits of a piece that is
    only part of an exam the line names are marked 'partial': only reported, never booked nor asked."""
    if isinstance(query, str) and len(query) > MAX_QUERY_LENGTH:
        raise ToolError(f'Consulta longa demais (máximo {MAX_QUERY_LENGTH} caracteres).')
    pieces = split_exams(query, short=True) if isinstance(query, str) else []
    if not pieces or pieces == [query.strip()] or split_exams(query) == [query.strip()]:
        return search(query, top_k)
    searched = []  # (piece as written, text searched, hits)
    for index, piece in enumerate(pieces):
        shortened = len(normalize(piece).replace(' ', '')) < 2  # only as the ending of the name before it
        found = [] if shortened else search(piece, top_k)
        weak = not found or found[0]['score'] < OWN_EXAM
        whole = (ahead(pieces[index - 1], piece, weak) if index else None) or \
            (None if shortened or index + 1 == len(pieces) else behind(piece, pieces[index + 1])) or \
            (None if shortened or not found or found[0]['score'] == 1.0 else exact(piece))
        if whole:
            found = search(whole, top_k)
        elif shortened or (weak and qualifier(piece) and len(pieces) > 1):  # "Urina 24h: proteinuria e ..."
            continue
        searched.append((piece, whole or piece, found))
    line = set(normalize(query).split())
    named = [set(normalize(text).split()) for _, text, found in searched
             if found and found[0]['score'] >= COMPLETED and found[0]['code'] not in GENERIC]
    complete = set().union(*named)
    serology = any(words & CLASSES and words - CLASSES - CONNECTIVES for words in named)  # "Chagas IgG" on the line
    hits = []
    for index, (piece, text, found) in enumerate(searched):
        previous = searched[index - 1][1] if index else None
        cut = bool(found) and partial(text, previous, found[0]['code'], line, complete, serology)
        hits += [{**hit, 'piece': piece, **({'partial': True} if cut else {})} for hit in found]
    return hits


server = MCPServer('rag-exams', instructions='Busca códigos de exames fictícios no catálogo (RAG).')


@server.tool()
def search_exams(query: Annotated[str, or_default('')], top_k: Annotated[int, or_default(0)] = 3) -> list[dict]:
    """Find catalog exams for the exam names of one line; returns [{code, name, score}] best first.
    A line with several exams ("TSH, T4 livre", "Colesterol total e Triglicerideos") is split on
    " e ", ",", "+", ";" and "/" (a catalog name such as "HIV antigeno e anticorpos" stays whole):
    each piece gets up to top_k hits, best first, and each hit carries its "piece"."""
    if not query.strip():  # empty or not text (None, 123, a list)
        raise ToolError('query deve ser um texto com o nome de um exame, ex.: Glicose.')
    return search_line(query, top_k)  # a top_k that is not a whole number arrives as 0 and is refused there


@server.custom_route('/health', methods=['GET'])
async def health(request):
    return JSONResponse({'status': 'ok', 'exams': len(MATCHER.exams)})


SECURITY = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_origins=[],
                                     allowed_hosts=['rag:8002', 'localhost:*', '127.0.0.1:*'])

# uvicorn and the MCP client's pool both drop idle connections after 5 s: a POST sent then never returned (python-sdk #906).
KEEP_ALIVE_SECONDS = 75

if __name__ == '__main__':
    import uvicorn
    quiet_logs('search_exams')
    uvicorn.run(server.sse_app(transport_security=SECURITY, host='0.0.0.0'), host='0.0.0.0', port=8002,
                timeout_keep_alive=KEEP_ALIVE_SECONDS, log_level=server.settings.log_level.lower())
