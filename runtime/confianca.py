"""Booking policy: how confident the agent is in each exam, and which ones it books (docs/regras.md).

An exam's confidence is min(RAG score, how well the search matches a line the OCR read, the OCR's reading of that
line), on its own piece of the order (one piece of text, one exam). From `min_confidence` it books alone, from
`ask_from` only with a yes, below that it is only reported. Only a 'request' line of a clean page books alone; none
books from a 'negated', 'history' or 'prep' line or where the page contests it. A reply without them fails closed.
"""
import difflib
import re
from collections.abc import Container, Iterable
from dataclasses import dataclass
from typing import NamedTuple

from pydantic import ValidationError

from catalogo import CLASSES, CONNECTIVES, GLUED_LETTERS, words
from leitura import BLOCKING, NOT_ANCHORS, OcrReading

from .pedido import Accounted, Candidate, Find, Item, OrderRecord, Piece

# A search "finds" an exam of the order when its untied best hit scores at least the catalog's floor and its query
# is a piece of a line read; one the model leaves out of the booking call is reported, never booked.
FIND_FLOOR = 0.60
# Below this, a match that shares no word with the exam's name, connectives and the "anti" of every serology aside,
# is only a resemblance of letters ("Anti HAV" is not "HIV antigeno e anticorpos", 0,70): reported, not asked.
RESEMBLANCE = 0.80
NOT_SHARED = CONNECTIVES | {'anti'}


def shares_a_word(query: str, name: str) -> bool:
    """Whether the words read and an exam's name have a word in common (connectives and "anti" aside)."""
    return bool((set(query.split()) & set(name.split())) - NOT_SHARED)


def resemblance(query: str, name: str, score: float) -> bool:
    """Whether a match is only a resemblance of letters: below RESEMBLANCE, no word in common."""
    return score < RESEMBLANCE and not shares_a_word(query, name)


FREE_TEXT = '[linha de texto livre omitida]'  # the model's copy of a line not in the OCR's exam_lines (all, without)
# A free piece of a line for an exam, and why it is asked although written clearly (doubt); a piece an exam booked or
# asked holds; why an exam is never booked from the order (a line's kind or the page's contest), and that line.
Spot = NamedTuple('Spot', [('confidence', float), ('line', int | None), ('start', int), ('end', int), ('doubt', str | None)])
Taken = NamedTuple('Taken', [('line', int | None), ('start', int), ('end', int), ('name', str)])
Refused = NamedTuple('Refused', [('reason', str), ('line', int)])


def intent_of(order: OrderRecord, line: int | None) -> str:
    """The kind of a line read; 'unknown' without a usable line_intent (fail closed: asked at most)."""
    intents = order.ocr_intent
    return intents[line] if intents is not None and line is not None and line < len(intents) else 'unknown'


@dataclass(frozen=True)
class BookingPolicy:
    """The values a spec can change (the BookingPlugin's kwargs); the defaults are the measured ones (docs/medicoes.md).
    min_confidence: at 0.90 none of the 10 wrong matches of the 631 calibration queries passes. The OCR floors (0-100):
    a line read below its floor never books alone; a name of 3 letters or fewer needs a clearer reading, and its
    abbreviation of a longer name more ("TGP" read "TAP" is Tempo de protrombina). ask_from=None: nothing is asked."""
    min_confidence: float = 0.90
    ask_from: float | None = 0.70
    ocr_floor_line: float = 75
    ocr_floor_short: float = 85
    ocr_floor_synonym: float = 95
    top_k: int = 3

    @property
    def below_booking(self) -> float:
        """The highest confidence an exam that must not be booked alone can have."""
        return round(self.min_confidence - 0.01, 2)

    @property
    def below_asking(self) -> float:
        """The highest confidence of an exam that must not even be asked: a [s/N] about an exam the
        order does not name invites a wrong "s"."""
        return round(self.ask_from - 0.01, 2) if self.ask_from is not None else self.below_booking

    def ocr_floor(self, query: str, name: str) -> float:
        """The OCR reading a line needs to book this match alone (query and name already plain)."""
        if len(query.replace(' ', '')) <= 3 and query != name:  # an abbreviation of a longer name
            return self.ocr_floor_synonym
        short = min(len(query.replace(' ', '')), len(name.replace(' ', ''))) <= 3
        return self.ocr_floor_short if short else self.ocr_floor_line

    def band(self, confidence: float) -> str | None:
        """'booked', 'ask' or None (left out) for an exam at this confidence."""
        if confidence >= self.min_confidence:
            return 'booked'
        return 'ask' if self.ask_from is not None and confidence >= self.ask_from else None


@dataclass(frozen=True)
class Hit:
    """A result of the catalog search (mcp_servers/rag.py)."""
    code: str
    name: str
    score: float
    term: str  # the name or synonym it matched ("TGP" for ALT)
    piece: str | None  # the exam of the query it is for, when the search cut a line into its exams
    partial: bool  # only part of an exam the line names (the generic IgM of "Chagas IgG e IgM")


def by_piece(text: str, reply: object) -> dict[str, list[Hit]]:
    """The hits of a search's reply (a list, or one hit: each item with a code), by the piece of the searched
    text each is for: an exam the search cut the line into ("Colesterol total e Triglicerideos"), or the text."""
    pieces: dict[str, list[Hit]] = {}
    for item in reply if isinstance(reply, list) else [reply]:
        if isinstance(item, dict) and 'code' in item:
            hit = Hit(item['code'], str(item.get('name', '')), float(item.get('score', 0)), str(item.get('term', '')),
                      str(item['piece']) if item.get('piece') else None, bool(item.get('partial')))
            pieces.setdefault(hit.piece or text, []).append(hit)
    return pieces


def untied_best(hits: list[Hit]) -> Hit | None:
    """The best hit, if it scores at least FIND_FLOOR and no other hit scores the same."""
    ranked = sorted(hits, key=lambda hit: -hit.score)  # on a tie, the first
    if not ranked or ranked[0].score < FIND_FLOOR or len(ranked) > 1 and ranked[1].score == ranked[0].score:
        return None
    return ranked[0]


def line_support(query: str, lines: list[str]) -> tuple[float, int | None]:
    """(how well the query matches a line the OCR read, that line): 1.0 when the query is whole words of it ("Exame:
    Creatinina"), else the character similarity (small OCR typos); (0.0, None) for nothing in common. A tie: the first."""
    query, best_ratio, best_index = words(query), 0.0, None
    for index, line in enumerate(lines):
        if query and f' {query} ' in f' {line} ':
            return 1.0, index
        ratio = difflib.SequenceMatcher(None, query, line).ratio()
        if ratio > best_ratio:  # compare ratios only: an index is never compared with None
            best_ratio, best_index = ratio, index
    return best_ratio, best_index


def remember_ocr(order: OrderRecord, reply: object) -> dict[str, list[str]]:
    """Keep what the OCR read (lines plain and as read, PII masked; counts, readings); return the model's copy.
    A reply outside the contract (leitura.OcrReading, of this version) is not read: nothing to book from."""
    try:
        reading = OcrReading.model_validate(reply)
    except ValidationError:
        return {'lines': []}
    order.ocr_read, order.ocr_lines = reading.lines, [words(line) for line in reading.lines]
    order.ocr_confidence, order.ocr_intent, order.ocr_terms = reading.line_confidence, reading.line_intent, reading.exam_terms
    order.ocr_contested = None if reading.contested_exams is None else {
        item.code: item.reason for item in reading.contested_exams}  # None: every exam asked at most
    order.page_clean, order.cancel_unlinked, order.off_list = reading.page_clean, reading.cancel_unlinked, reading.off_list
    order.pii_masked, order.instructions_removed = reading.pii_masked, reading.instructions_removed
    order.text_removed = reading.text_removed
    return {'lines': [line if index in reading.exam_lines else FREE_TEXT for index, line in enumerate(reading.lines)]}


def remember_search(order: OrderRecord, query: str, reply: object, policy: BookingPolicy) -> None:
    """For every code a search returned, piece by piece: its RAG score and how well the query matches a line read (the
    model's own spelling fix of a misread line cannot raise its confidence)."""
    for piece, hits in by_piece(query, reply).items() or [(query, [])]:
        remember_piece(order, piece, hits, policy)


def remember_piece(order: OrderRecord, query: str, hits: list[Hit], policy: BookingPolicy) -> None:
    """remember_search for one piece of text and its own hits: the best one may book alone."""
    lines, query = order.ocr_lines or [], words(query)
    support, index = line_support(query, lines)
    span = query if support == 1.0 else None
    glued = pieces_of(query, lines) if support < 1.0 else []
    if glued:  # the words read with a connective the OCR glued to them: "ureia" in "2 ureiae creatinina"
        index, start, end, support = max(glued, key=lambda piece: piece.support)  # on a tie, the first
        span = lines[index][start:end]  # its own piece of the line, not the whole line
    candidates = dict(order.candidates or {})
    best = max(hits, key=lambda hit: hit.score, default=None)  # on a tie, the first
    top = best.score if best else 0.0
    tied = sum(hit.score == top for hit in hits) > 1
    for hit in hits:
        # the words read are the best hit's: a neighbour ("Colesterol HDL" for "Colesterol LDL") is at
        # most asked, never booked alone, and holds no copy of the line
        score = hit.score
        if not named_by_the_order(hit, query, tied and score == top):
            score = min(score, policy.below_asking)
        elif hit is not best:
            score = min(score, policy.below_booking)
        confidence = round(min(score, support), 2)
        if hit.code not in candidates or confidence > candidates[hit.code].confidence:
            candidates[hit.code] = Candidate(
                hit.name, confidence, score, support, index, (order.ocr_read or lines)[index] if index is not None else '',
                span if hit is best else None, policy.ocr_floor(query, words(hit.name)))
    order.candidates = candidates
    remember_find(order, query, hits, policy)


def named_by_the_order(hit: Hit, query: str, tied_best: bool) -> bool:
    """Whether a hit is what the order names, else only reported: not only part of an exam the line names, no antibody
    class not written, no resemblance of letters, no tie of best matches sharing no word with the words read ("Anti HAV":
    HIV and Anti HCV, both 0,88; "T3": T3 livre and T3 total share it, still asked)."""
    named = f'{words(hit.name)} {words(hit.term)}'  # its name, and the synonym it matched
    unwritten_class = (set(words(hit.name).split()) & CLASSES) - set(query.split())
    return not (hit.partial or unwritten_class or resemblance(query, named, hit.score)
                or (tied_best and not shares_a_word(query, named)))


def pieces_of(query: str, lines: list[str]) -> list[Piece]:
    """Every piece of the lines with the query's words, word by word (reads_as: "tsh" in "tshe", support 0.86). Empty
    if the query is not a piece of any line (a model's own rewording, a catalog name not written)."""
    wanted, pieces = query.split(), []
    for index, line in enumerate(lines):
        found = [(match.start(), match.group()) for match in re.finditer(r'\S+', line)]
        for first in range(len(found) - len(wanted) + 1):
            window = found[first:first + len(wanted)]
            if all(reads_as(token, word) for (_, token), word in zip(window, wanted, strict=True)):
                start, end = window[0][0], window[-1][0] + len(window[-1][1])
                text = line[start:end]
                support = 1.0 if text == query else difflib.SequenceMatcher(None, query, text).ratio()
                pieces.append(Piece(index, start, end, round(support, 2)))
    return pieces


def reads_as(token: str, word: str) -> bool:
    """A word read is the query's word, or it with up to GLUED_LETTERS letters the OCR glued to it ("tshe")."""
    return token == word or (len(word) >= 3 and token.startswith(word) and len(token) - len(word) <= GLUED_LETTERS)


def remember_find(order: OrderRecord, query: str, hits: list[Hit], policy: BookingPolicy) -> None:
    """Keep what this search found in the order, if anything: its untied best hit, from FIND_FLOOR,
    with every piece of the lines its query matched (order.finds, one per exam and pieces)."""
    best, pieces = untied_best(hits), pieces_of(query, order.ocr_lines or []) if query else []
    if best is None or not pieces:
        return
    find = Find(best.code, best.name, best.score, query, pieces, policy.ocr_floor(query, words(best.name)))
    same = (find.code, [piece[:3] for piece in pieces])
    earlier = [other for other in order.finds or [] if (other.code, [piece[:3] for piece in other.pieces]) == same]
    finds = [other for other in order.finds or [] if other not in earlier]
    finds.append(find if not earlier or find.score > earlier[0].score else earlier[0])
    order.finds = finds


def best_spot(candidate: Candidate, taken: list[Taken], order: OrderRecord, policy: BookingPolicy,
              contest: str = '') -> Spot | Refused | str:
    """The free piece of the order (places) where the candidate is most confident (Spot); when every piece is taken, the
    name of the exam holding it; when its only pieces are on 'negated', 'history' or 'prep' lines, or the page contests
    it (`contest`), Refused."""
    free: list[Spot] = []
    holders: list[str] = []
    refused: list[Refused] = []
    for line, start, end, support in places(candidate, order.ocr_lines or []):
        kind = intent_of(order, line)
        if kind in NOT_ANCHORS or contest in BLOCKING:  # "Ferritina", then "Obs.: cancele a Ferritina"
            refused.append(Refused(contest if contest in BLOCKING else kind, line))
            continue
        holder = next((other.name for other in taken if other.line == line and other.start < end and start < other.end), None)
        if holder:
            holders.append(holder)
            continue
        reading = reading_at(order.ocr_confidence, line, candidate.floor, policy)  # below its floor, never booked alone
        doubt = doubt_at(order, kind, contest, line, start, end, candidate.name)
        if doubt:
            reading = min(reading, policy.below_booking)  # a note, a doubt, a table, a contest: asked at most
        free.append(Spot(round(min(candidate.score, support, reading), 2), line, start, end, doubt))
    if holders and not free:
        return holders[0]
    if refused and not free:  # a negation or a history first: the reason that matters most
        return min(refused, key=lambda item: (item.reason not in BLOCKING, item.reason != 'negated', item.line))
    return max(free, key=lambda spot: spot.confidence, default=Spot(0.0, candidate.line, 0, 0, None))  # a tie: the first


def places(candidate: Candidate, lines: list[str]) -> list[tuple[int, int, int, float]]:
    """(line, start, end, support): each whole-word occurrence of the candidate's words, or else the line most like them."""
    if candidate.span:
        return [(i, *match.span(), candidate.support) for i, line in enumerate(lines)
                for match in re.finditer(rf'\b{re.escape(candidate.span)}\b', line)]
    return [] if candidate.line is None else [(candidate.line, 0, len(lines[candidate.line]), candidate.support)]


def doubt_at(order: OrderRecord, kind: str, contest: str, line: int, start: int, end: int, name: str) -> str | None:
    """Why an exam written clearly here is asked, or None: a contest, another exam's longer name over it, a table or a
    form, a line that is not a list line ('uncertain'), or a page with more than its list ('page')."""
    if doubt := contest or longer(order, line, start, end, name):
        return doubt
    if kind not in ('request', 'unrecognized'):
        return kind if kind in ('table', 'form') else 'uncertain'
    return None if order.page_clean is True else 'page'


def longer(order: OrderRecord, line: int, start: int, end: int, name: str) -> str | None:
    """'longer' (asked at most) when the OCR read another exam's longer name over this piece: "proteina c reativa"."""
    for term, other in [pair for terms in (order.ocr_terms or [])[line:line + 1] for pair in terms]:
        spans = [found.span() for found in re.finditer(rf'\b{re.escape(str(term))}\b', (order.ocr_lines or [])[line])]
        if other != name and len(str(term)) > end - start and any(s <= start and end <= e for s, e in spans):
            return 'longer'
    return None


def sort_out(codes: Iterable[str], candidates: dict[str, Candidate], order: OrderRecord, policy: BookingPolicy,
             accounted: list[Accounted]) -> tuple[list[Item], list[Item], list[Item]]:
    """(booked, to ask, left out). Longer words claim their text first: "Clearance de creatinina" takes its line and
    "Creatinina" its next free occurrence. `accounted` gets the piece of every exam sorted out on its own text, with the
    exam's plain name when only similar to a line: it then accounts only for searches of words of that name."""
    taken: list[Taken] = []
    bands: dict[str | None, list[Item]] = {'booked': [], 'ask': [], None: []}
    for code in sorted(codes, key=lambda code: -len(candidates[code].span or '')):
        candidate = candidates[code]
        item: Item = {'name': candidate.name, 'confidence': candidate.confidence, 'line': candidate.line,
                      'read': candidate.read, 'code': code}
        contest = order.ocr_contested.get(code, '') if order.ocr_contested is not None else 'unknown'  # asked
        spot = best_spot(candidate, taken, order, policy, contest)
        if isinstance(spot, Refused):  # the order says not to do it, that it was done, or only prepares for it
            bands[None].append(item | {'reason': spot.reason, 'line': spot.line, 'read': (order.ocr_read or [])[spot.line]})
            continue
        if isinstance(spot, str):  # every piece of it is held by the exam named
            bands[None].append(item | {'reason': 'line_used', 'used_by': spot})
            continue
        item['confidence'], line, start, end, doubt = spot
        item['line'], item['read'] = line, (order.ocr_read or [])[line] if line is not None else item['read']
        if doubt:  # why it is asked and not booked: the line is a note, or says something against it
            item['why'] = doubt
        if line is not None:  # and the exam's own words, for the check of the order
            accounted.append(Accounted(line, start, end, None if candidate.span else words(candidate.name),
                                       words(candidate.name)))
        band = policy.band(item['confidence'])
        bands[band].append(item if band else item | {'reason': 'score'})
        if band:
            taken.append(Taken(line, start, end, candidate.name))
    return bands['booked'], bands['ask'], bands[None]


def held(claimed: list[Accounted], query: str, line: int, start: int, end: int) -> bool:
    """Whether an exam already stands on any of this piece: exactly, or, only similar to its line, with words of its name
    ("Hemoglobina" inside a "Hemoglobina glicda", not the "TSH" glued to a "T4 Iivre")."""
    return any(other.line == line and other.start < end and start < other.end
               and (other.similar is None or bool(pieces_of(query, [other.similar]))) for other in claimed)


def reading_at(readings: list[float] | None, line: int, floor: float, policy: BookingPolicy) -> float:
    """The OCR reading of a line as a confidence: 1.0 from its floor, below it (or none) at most below_booking."""
    if readings is None or line >= len(readings):
        return policy.below_booking
    return 1.0 if readings[line] >= floor else min(readings[line] / 100, policy.below_booking)


def omitted(proposed: Container[str], accounted: list[Accounted], order: OrderRecord, policy: BookingPolicy) -> list[Item]:
    """The exams a search found in the order but the model left out of the booking call, so none vanishes without a
    word: one per piece of text not held by a proposed exam (held), longer pieces first, at the confidence the order gives
    it there; on several lines, on the free one most like a list of exams ("Exames: Hemograma completo, Glicose", not
    "Obs: jejum de 8 horas para glicose")."""
    lines, finds, claimed = order.ocr_lines or [], order.finds or [], list(accounted)
    reported: list[Item] = []

    def listing(line: int, start: int, end: int) -> float:  # how much of the line the exams cover
        covered = set(range(start, end))
        for other, s, e in [entry[:3] for entry in claimed] + [piece[:3] for find in finds for piece in find.pieces]:
            covered.update(range(s, e) if other == line else ())
        return len(covered) / max(len(lines[line]), 1)

    for find in sorted(finds, key=lambda find: (find.pieces[0].start - find.pieces[0].end, -find.score)):
        free = [piece for piece in find.pieces if not held(claimed, find.query, *piece[:3])]
        if find.code in proposed or not free:
            continue
        wanted = [piece for piece in free if intent_of(order, piece.line) not in NOT_ANCHORS]
        line, start, end, support = max(wanted or free, key=lambda piece: listing(*piece[:3]))  # on a tie, the first
        reading = reading_at(order.ocr_confidence, line, find.floor, policy)
        # the order says not to do it, or only prepares for it: reported with that reason, not as a warning
        reason = 'omitted' if wanted else intent_of(order, line)
        reported.append({'code': find.code, 'name': find.name, 'line': line, 'reason': reason,
                         'confidence': round(min(find.score, support, reading), 2), 'read': (order.ocr_read or [])[line]})
        claimed.append(Accounted(line, start, end, None, words(find.name)))
    return reported
