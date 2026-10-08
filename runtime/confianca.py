"""Booking policy: how confident the agent is in each exam, and which ones it books.

An exam's confidence is min(RAG score, how well the search matches a line the OCR read,
the OCR's own reading of that line). From `min_confidence` it is booked on its own; from
`ask_from` only if the person says yes; below that it is only reported. Each exam takes
its own piece of the order: one piece of text, one exam.

From the OCR (guardrails/intent.py): only a 'request' line (nothing but exams) of a clean page (`page_clean`)
books alone; any other exam found is asked. None is booked from a line that says not to do it ('negated'), that it
was done ('history') or that prepares for it ('prep'), nor anywhere the page contests it (`contested_exams`). Without
a usable line_intent, contested_exams or a true page_clean in the OCR's reply (leitura.OcrReading): fail closed.
"""
import difflib
import re
from collections.abc import Container, Iterable
from dataclasses import dataclass

from pydantic import ValidationError

from catalogo import CLASSES, CONNECTIVES, words
from leitura import OcrReading

from .pedido import Accounted, Candidate, Find, Item, OrderRecord, Piece

# A search "finds" an exam of the order when its best hit, untied, scores at least the catalog's own
# floor (the RAG returns nothing below it) and its query is a piece of a line read. An exam found but
# left out of the booking call by the model is reported (runtime/callbacks.py), never booked.
FIND_FLOOR = 0.60
# A match whose name has an antibody class (catalogo.CLASSES) the words read do not have is only reported.
# Below this, a match that shares no word with the exam's name is only a resemblance of letters (the
# booking policy's own bound: a misread "- GA" taken as IgA scores 0,80): "Anti HAV" is not "HIV
# antigeno e anticorpos" (0,70), "LABORATORIO" is not Paratormônio. It is reported, not asked.
# Connectives are not shared words ("SOLICITAÇÃO DE E" is not Glicemia de jejum), nor is the "anti" of
# every serology ("Anti HAV" is not Anti HCV).
RESEMBLANCE = 0.80
NOT_SHARED = CONNECTIVES | {'anti'}
GLUED = 2  # letters the OCR may glue to a word ("TSH e" read "TSHe"): "tsh" still matches "tshe"


def shares_a_word(query: str, name: str) -> bool:
    """Whether the words read and an exam's name have a word in common (connectives and "anti" aside)."""
    return bool((set(query.split()) & set(name.split())) - NOT_SHARED)


def resemblance(query: str, name: str, score: float) -> bool:
    """Whether a match is only a resemblance of letters: below RESEMBLANCE, no word in common."""
    return score < RESEMBLANCE and not shares_a_word(query, name)


# What a line asks for (leitura.Intent); nothing is booked from a NOT_ANCHORS line.
BLOCKING = {'negated', 'history'}
NOT_ANCHORS = BLOCKING | {'prep'}
FREE_TEXT = '[linha de texto livre omitida]'  # the model's copy of a line not in the OCR's exam_lines (all, without)
# A free piece of a line for an exam: (confidence, line, start, end, why it is asked although written clearly).
Spot = tuple[float, int | None, int, int, str | None]
Taken = tuple[int | None, int, int, str]  # a piece an exam booked or asked holds: (line, start, end, its name)


def intent_of(order: OrderRecord, line: int | None) -> str:
    """The kind of a line read; 'unknown' without a usable line_intent (fail closed: asked at most)."""
    intents = order.ocr_intent
    return intents[line] if intents is not None and line is not None and line < len(intents) else 'unknown'


@dataclass(frozen=True)
class BookingPolicy:
    """The values a spec can change (`booking` block); the defaults are the measured ones.

    min_confidence was calibrated on 631 queries (tests/calibration/queries.jsonl, rechecked
    by tests/test_calibration.py): at 0.90 none of the 10 wrong matches passes (a misread
    "- GA" taken as IgA scores 0.80) and 522 of the 621 right matches stay.
    The OCR floors (line_confidence, 0-100): a line read below its floor never books alone,
    however well the catalog matches it. A code of 3 letters or fewer needs a clearer
    reading, and more so when it only matches as another name of a longer exam: a
    handwritten "TGP" read as "TAP" (93) is Tempo de protrombina. Measured with the OCR's
    real reply on the 120 handwritten orders, 200 load-test orders and a 240-image bench:
    no wrong exam booked alone; 605 of the 618 exams of the load set are booked alone and
    the rest is asked. Without a usable reading the rule fails closed: nothing is booked
    without a yes. ask_from=None turns the question off: below min_confidence is left out.
    """
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
    """(how well the query matches a line the OCR read, index of that line).

    1.0 when the query appears in a line as whole words ("Exame: Creatinina");
    otherwise the character similarity, which tolerates small OCR typos. No line at
    all, or nothing in common with any line, is (0.0, None); on a tie the first wins.
    """
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
    """For every code a search returned: its RAG score and how well the query matches a line read.
    The model's own spelling fix of a misread line cannot raise its confidence. A line the search
    split into its exams is remembered piece by piece, as if each exam had been searched on its own."""
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
        score, named = hit.score, f'{words(hit.name)} {words(hit.term)}'  # its name, and the synonym it matched
        # the words read are the best hit's: a neighbour ("Colesterol HDL" for "Colesterol LDL") is at
        # most asked, never booked alone, and holds no copy of the line. A hit that is not what the order
        # names is only reported, not even asked: one the search marked as only part of an exam the line
        # names, one with an antibody class not written, a resemblance of letters, or a best match tied
        # with another exam that shares no word with the words read ("Anti HAV": HIV and Anti HCV, both
        # 0,88). A tie of related exams that do share it ("T3": T3 livre and T3 total) is still asked
        if hit.partial or (set(words(hit.name).split()) & CLASSES) - set(query.split()) \
                or resemblance(query, named, score) or (tied and score == top and not shares_a_word(query, named)):
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


def pieces_of(query: str, lines: list[str]) -> list[Piece]:
    """Every piece of the lines with the query's words, word by word: a word matches the same word or one
    the OCR glued up to GLUED letters to ("tsh" in "tshe", support 0.86). Empty if the query is not a
    piece of any line (a model's own rewording, a catalog name not written)."""
    wanted, pieces = query.split(), []
    for index, line in enumerate(lines):
        found = [(match.start(), match.group()) for match in re.finditer(r'\S+', line)]
        for first in range(len(found) - len(wanted) + 1):
            window = found[first:first + len(wanted)]
            if all(token == word or (len(word) >= 3 and token.startswith(word) and len(token) - len(word) <= GLUED)
                   for (_, token), word in zip(window, wanted, strict=True)):
                start, end = window[0][0], window[-1][0] + len(window[-1][1])
                text = line[start:end]
                support = 1.0 if text == query else difflib.SequenceMatcher(None, query, text).ratio()
                pieces.append(Piece(index, start, end, round(support, 2)))
    return pieces


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
              contest: str = '') -> tuple[Spot | None, str | None, tuple[str, int] | None]:
    """(spot, None, None) on the free piece of the order where the candidate is most confident: a whole-word
    occurrence of its words in any line, or else the line most like them. (None, exam holding it, None) when
    every such piece is taken; (None, None, (kind, line)) when its only pieces are on lines that say not to do it,
    that it was done, or that prepare for it, or the page says so of it (`contest`). A piece on a note or a
    table, or of an exam contested otherwise, is asked."""
    lines, readings = order.ocr_lines or [], order.ocr_confidence
    span, index = candidate.span, candidate.line
    spots = ([(i, *m.span(), candidate.support) for i, line in enumerate(lines)
              for m in re.finditer(rf'\b{re.escape(span)}\b', line)]
             if span else [(index, 0, len(lines[index]), candidate.support)] if index is not None else [])
    free: list[Spot] = []
    holders: list[str] = []
    refused: list[tuple[str, int]] = []
    for line, start, end, support in spots:
        kind = intent_of(order, line)
        if kind in NOT_ANCHORS or contest in BLOCKING:  # "Ferritina", then "Obs.: cancele a Ferritina"
            refused.append((contest if contest in BLOCKING else kind, line))
            continue
        holder = next((name for held, s, e, name in taken if held == line and s < end and start < e), None)
        reading = reading_at(readings, line, candidate.floor, policy)  # below its floor, never booked alone
        doubt = contest or longer(order, line, start, end, candidate.name) or (
            None if kind in ('request', 'unrecognized') else kind if kind in ('table', 'form') else 'uncertain')
        doubt = doubt or (None if order.page_clean is True else 'page')  # text besides the list: asked
        if doubt:
            reading = min(reading, policy.below_booking)  # a note, a doubt, a table, a contest: asked at most
        if holder:
            holders.append(holder)
        else:
            free.append((round(min(candidate.score, support, reading), 2), line, start, end, doubt))
    if holders and not free:
        return None, holders[0], None
    if refused and not free:  # a negation or a history first: the reason that matters most
        return None, None, min(refused, key=lambda item: (item[0] not in BLOCKING, item[0] != 'negated', item[1]))
    return max(free, key=lambda spot: spot[0], default=(0.0, index, 0, 0, None)), None, None  # on a tie, the first


def longer(order: OrderRecord, line: int, start: int, end: int, name: str) -> str | None:
    """'longer' (asked at most) when the OCR read another exam's longer name over this piece: "proteina c reativa"."""
    return 'longer' if any(other != name and len(str(term)) > end - start and any(s <= start and end <= e for s, e in (
        m.span() for m in re.finditer(rf'\b{re.escape(str(term))}\b', (order.ocr_lines or [])[line])))
        for found in (order.ocr_terms or [])[line:line + 1] for term, other in found) else None


def sort_out(codes: Iterable[str], candidates: dict[str, Candidate], order: OrderRecord, policy: BookingPolicy,
             accounted: list[Accounted]) -> tuple[list[Item], list[Item], list[Item]]:
    """(booked, to ask, left out). Longer words claim their text first: "Clearance de
    creatinina" takes its line and "Creatinina" its next free occurrence. `accounted` gets the
    piece of text of every exam sorted out on its own text (booked, asked or left out for its
    confidence), with the exam's plain name when only similar to a line: it then accounts only
    for searches of words of that name."""
    taken: list[Taken] = []
    bands: dict[str | None, list[Item]] = {'booked': [], 'ask': [], None: []}
    for code in sorted(codes, key=lambda code: -len(candidates[code].span or '')):
        candidate = candidates[code]
        item: Item = {'name': candidate.name, 'confidence': candidate.confidence, 'line': candidate.line,
                      'read': candidate.read, 'code': code}
        contest = order.ocr_contested.get(code, '') if order.ocr_contested is not None else 'unknown'  # asked
        spot, holder, refused = best_spot(candidate, taken, order, policy, contest)
        if refused:  # the order says not to do it, that it was done, or only prepares for it: never booked
            kind, at = refused
            bands[None].append(item | {'reason': kind, 'line': at, 'read': (order.ocr_read or [])[at]})
            continue
        if spot is None:
            bands[None].append(item | {'reason': 'line_used', 'used_by': holder})
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
            taken.append((line, start, end, candidate.name))
    return bands['booked'], bands['ask'], bands[None]


def held(claimed: list[Accounted], query: str, line: int, start: int, end: int) -> bool:
    """Whether a piece of a line is text an exam already stands on: any of it under an exact piece,
    or, under a line an exam is only similar to, words of that exam's name ("Hemoglobina" inside a
    "Hemoglobina glicda", not the "TSH" glued to a "T4 Iivre")."""
    return any(other.line == line and other.start < end and start < other.end
               and (other.similar is None or bool(pieces_of(query, [other.similar]))) for other in claimed)


def reading_at(readings: list[float] | None, line: int, floor: float, policy: BookingPolicy) -> float:
    """The OCR reading of a line as a confidence: 1.0 from its floor, below it (or none) at most below_booking."""
    if readings is None or line >= len(readings):
        return policy.below_booking
    return 1.0 if readings[line] >= floor else min(readings[line] / 100, policy.below_booking)


def omitted(proposed: Container[str], accounted: list[Accounted], order: OrderRecord, policy: BookingPolicy) -> list[Item]:
    """The exams a search found in the order but the model left out of the booking call, so none
    vanishes without a word: one per piece of text, longer pieces first ("TSH" and "T4 livre" on one
    line are two), at the confidence the order gives it there (search, match, OCR reading). A piece
    an exam the model proposed already accounts for ("Colesterol" inside a booked "Colesterol LDL";
    inside a "T4 Iivre" booked by similarity, only the words of T4 livre) is not left out. Written
    on several lines, the exam is reported on the free one most like a list of exams: where it and
    the other exams found or proposed cover most of the line ("Glicose" in "Exames: Hemograma
    completo, Glicose", not in "Obs: jejum de 8 horas para glicose"). Booking does not change."""
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
