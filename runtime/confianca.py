"""Booking policy: how confident the agent is in each exam, and which ones it books.

An exam's confidence is min(RAG score, how well the search matches a line the OCR read,
the OCR's own reading of that line). From `min_confidence` it is booked on its own; from
`ask_from` only if the person says yes; below that it is only reported. Each exam takes
its own piece of the order: one piece of text, one exam.
"""
import difflib
import re
from dataclasses import dataclass

from catalogo import words

# A search "finds" an exam of the order when its best hit, untied, scores at least the catalog's own
# floor (the RAG returns nothing below it) and its query is a piece of a line read. An exam found but
# left out of the booking call by the model is reported (runtime/callbacks.py), never booked.
FIND_FLOOR = 0.60
# An antibody class names a different exam: "Chagas IgM" is not Chagas IgG, one letter apart. A match
# whose name has a class the words read do not have is only reported, never booked nor asked.
CLASSES = {'iga', 'igg', 'igm', 'ige'}
# Below this, a match that shares no word with the exam's name is only a resemblance of letters (the
# booking policy's own bound: a misread "- GA" taken as IgA scores 0,80): "Anti HAV" is not "HIV
# antigeno e anticorpos" (0,70), "LABORATORIO" is not Paratormônio. It is reported, not asked.
# Connectives are not shared words ("SOLICITAÇÃO DE E" is not Glicemia de jejum), nor is the "anti" of
# every serology ("Anti HAV" is not Anti HCV).
RESEMBLANCE = 0.80
CONNECTIVES = {'a', 'o', 'as', 'os', 'e', 'de', 'da', 'do', 'das', 'dos', 'em', 'no', 'na', 'com', 'sem', 'para', 'por',
               'anti'}


def shares_a_word(query, name):
    """Whether the words read and an exam's name have a word in common (connectives and "anti" aside)."""
    return bool((set(query.split()) & set(name.split())) - CONNECTIVES)


def resemblance(query, name, score):
    """Whether a match is only a resemblance of letters: below RESEMBLANCE, no word in common."""
    return score < RESEMBLANCE and not shares_a_word(query, name)
GLUED = 2  # letters the OCR may glue to a word ("TSH e" read "TSHe"): "tsh" still matches "tshe"


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
    def below_booking(self):
        """The highest confidence an exam that must not be booked alone can have."""
        return round(self.min_confidence - 0.01, 2)

    @property
    def below_asking(self):
        """The highest confidence of an exam that must not even be asked: a [s/N] about an exam the
        order does not name invites a wrong "s"."""
        return round(self.ask_from - 0.01, 2) if self.ask_from is not None else self.below_booking

    def ocr_floor(self, query, name):
        """The OCR reading a line needs to book this match alone (query and name already plain)."""
        if len(query.replace(' ', '')) <= 3 and query != name:  # an abbreviation of a longer name
            return self.ocr_floor_synonym
        short = min(len(query.replace(' ', '')), len(name.replace(' ', ''))) <= 3
        return self.ocr_floor_short if short else self.ocr_floor_line

    def band(self, confidence, said_yes):
        """'booked', 'ask' or None (left out) for an exam at this confidence."""
        if confidence >= self.min_confidence or (said_yes and self.ask_from is not None and confidence >= self.ask_from):
            return 'booked'
        return 'ask' if self.ask_from is not None and confidence >= self.ask_from else None


def line_support(query, lines):
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


def remember_ocr(state, reply):
    """Keep what the OCR read: lines (plain and as read, PII already masked), counts and readings."""
    state['pii_masked'] = reply.get('pii_masked', {})
    state['instructions_removed'] = reply.get('instructions_removed', 0)
    state['ocr_lines'] = [words(line) for line in reply.get('lines', [])]
    state['ocr_read'] = [str(line) for line in reply.get('lines', [])]
    readings = reply.get('line_confidence')
    valid = isinstance(readings, list) and len(readings) == len(state['ocr_read']) and all(
        isinstance(value, (int, float)) and not isinstance(value, bool) for value in readings)
    state['ocr_confidence'] = [float(value) for value in readings] if valid else None  # None: no reading


def remember_search(state, query, hits, policy):
    """For every code a search returned: its RAG score and how well the query matches a line read.
    The model's own spelling fix of a misread line cannot raise its confidence. A line the search
    split into its exams ("Colesterol total e Triglicerideos": each hit carries its 'piece') is
    remembered piece by piece, as if each exam had been searched on its own."""
    hits = [hit for hit in hits if isinstance(hit, dict) and 'code' in hit]
    by_piece: dict = {}
    for hit in hits:
        by_piece.setdefault(str(hit['piece']) if hit.get('piece') else query, []).append(hit)
    for piece, piece_hits in by_piece.items() or [(query, [])]:
        remember_piece(state, piece, piece_hits, policy)


def remember_piece(state, query, hits, policy):
    """remember_search for one piece of text and its own hits: the best one may book alone."""
    lines, query = state.get('ocr_lines', []), words(query)
    support, index = line_support(query, lines)
    candidates = dict(state.get('candidates', {}))
    hits = [hit for hit in hits if isinstance(hit, dict) and 'code' in hit]
    best = max(hits, key=lambda hit: float(hit.get('score', 0)), default=None)  # on a tie, the first
    top = float(best.get('score', 0)) if best else 0.0
    tied = sum(float(hit.get('score', 0)) == top for hit in hits) > 1
    for hit in hits:
        score, name = float(hit.get('score', 0)), str(hit.get('name', ''))
        named = f'{words(name)} {words(hit.get("term", ""))}'  # its name, and the synonym it matched ("TGP" for ALT)
        # the words read are the best hit's: a neighbour ("Colesterol HDL" for "Colesterol LDL") is at
        # most asked, never booked alone, and holds no copy of the line. A hit that is not what the order
        # names is only reported, not even asked: one the search marked as only part of an exam the line
        # names (the generic IgM of "Chagas IgG e IgM"), one with an antibody class not written, a
        # resemblance of letters, or a best match tied with another exam that shares no word with the
        # words read ("Anti HAV": HIV and Anti HCV, both 0,88). A tie of related exams that do share it
        # ("T3": T3 livre and T3 total) is still asked
        if hit.get('partial') or (set(words(name).split()) & CLASSES) - set(query.split()) \
                or resemblance(query, named, score) or (tied and score == top and not shares_a_word(query, named)):
            score = min(score, policy.below_asking)
        elif hit is not best:
            score = min(score, policy.below_booking)
        confidence = round(min(score, support), 2)
        if confidence > candidates.get(hit['code'], {}).get('confidence', -1):
            candidates[hit['code']] = {
                'name': name, 'confidence': confidence, 'score': score, 'support': support, 'line': index,
                'read': state.get('ocr_read', lines)[index] if index is not None else '',
                # the words found in a line, only for the exam that matches them best; None if only similar
                'span': query if support == 1.0 and hit is best else None,
                'floor': policy.ocr_floor(query, words(name))}
    state['candidates'] = candidates
    remember_find(state, query, hits, policy)


def pieces_of(query, lines):
    """Every [line, start, end, support] of the query's words in the lines, word by word: a word matches
    the same word or one the OCR glued up to GLUED letters to ("tsh" in "tshe", support 0.86). Empty if
    the query is not a piece of any line (a model's own rewording, a catalog name not written)."""
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
                pieces.append([index, start, end, round(support, 2)])
    return pieces


def remember_find(state, query, hits, policy):
    """Keep what this search found in the order, if anything: its untied best hit, from FIND_FLOOR,
    with every piece of the lines its query matched (state['finds'], one per exam and pieces)."""
    scores = sorted((float(hit.get('score', 0)) for hit in hits), reverse=True)
    pieces = pieces_of(query, state.get('ocr_lines', [])) if query else []
    if not scores or scores[0] < FIND_FLOOR or (len(scores) > 1 and scores[1] == scores[0]) or not pieces:
        return
    best = next(hit for hit in hits if float(hit.get('score', 0)) == scores[0])
    find = {'code': best['code'], 'name': str(best.get('name', '')), 'score': scores[0], 'query': query,
            'pieces': pieces, 'floor': policy.ocr_floor(query, words(best.get('name', '')))}
    same = (find['code'], [piece[:3] for piece in pieces])
    earlier = [other for other in state.get('finds', []) if (other['code'], [piece[:3] for piece in other['pieces']]) == same]
    finds = [other for other in state.get('finds', []) if other not in earlier]
    finds.append(find if not earlier or find['score'] > earlier[0]['score'] else earlier[0])
    state['finds'] = finds


def best_spot(candidate, taken, state, policy):
    """((confidence, line, start, end), None) on the free piece of the order where the candidate
    is most confident: a whole-word occurrence of its words in any line, or else the line most
    like them. (None, exam holding it) when every such piece is taken."""
    lines, readings = state.get('ocr_lines', []), state.get('ocr_confidence')
    span, index = candidate['span'], candidate['line']
    spots = ([(i, *m.span(), 1.0) for i, line in enumerate(lines) for m in re.finditer(rf'\b{re.escape(span)}\b', line)]
             if span else [(index, 0, len(lines[index]), candidate['support'])] if index is not None else [])
    free, holders = [], []
    for line, start, end, support in spots:
        holder = next((name for held, s, e, name in taken if held == line and s < end and start < e), None)
        if readings is None or line >= len(readings):  # no reading of the line: fail closed, asked at most
            reading = policy.below_booking
        else:  # below the floor a line is never booked alone, even a reading of 93 under a floor of 95
            reading = 1.0 if readings[line] >= candidate['floor'] else min(readings[line] / 100, policy.below_booking)
        if holder:
            holders.append(holder)
        else:
            free.append((round(min(candidate['score'], support, reading), 2), line, start, end))
    if spots and not free:
        return None, holders[0]
    return max(free, key=lambda spot: spot[0], default=(0.0, index, 0, 0)), None  # on a tie, the first


def sort_out(codes, candidates, answers, state, policy, accounted=None):
    """(booked, to ask, left out). Longer words claim their text first: "Clearance de
    creatinina" takes its line and "Creatinina" its next free occurrence; an exam the person
    declined holds no text. `accounted`, if given, gets the piece of text of every exam sorted out
    on its own text (booked, asked, declined or left out for its confidence), with the exam's plain
    name when only similar to a line: it then accounts only for searches of words of that name."""
    taken, bands = [], {'booked': [], 'ask': [], None: []}  # taken: (line, start, end, exam name)
    for code in sorted(codes, key=lambda code: -len(candidates[code]['span'] or '')):
        candidate = candidates[code]
        item = {key: candidate[key] for key in ('name', 'confidence', 'line', 'read')} | {'code': code}
        spot, holder = best_spot(candidate, taken, state, policy)
        if spot is None:
            bands[None].append(item | {'reason': 'line_used', 'used_by': holder})
            continue
        item['confidence'], line, start, end = spot
        item['line'], item['read'] = line, state.get('ocr_read', [])[line] if line is not None else item['read']
        if accounted is not None and line is not None:
            accounted.append((line, start, end, None if candidate['span'] else words(candidate['name']),
                              words(candidate['name'])))  # and the exam's own words, for the check of the order
        if answers.get(code) is False:  # declined: reported at the confidence it was asked at, and holds no text
            bands[None].append(item | {'reason': 'declined'})
            continue
        band = policy.band(item['confidence'], answers.get(code))
        bands[band].append(item if band else item | {'reason': 'score'})
        if band:
            taken.append((line, start, end, candidate['name']))
    return bands['booked'], bands['ask'], bands[None]


def held(claimed, query, line, start, end):
    """Whether a piece of a line is text an exam already stands on: any of it under an exact piece,
    or, under a line an exam is only similar to, words of that exam's name ("Hemoglobina" inside a
    "Hemoglobina glicda", not the "TSH" glued to a "T4 Iivre")."""
    return any(other == line and s < end and start < e and (name is None or pieces_of(query, [name]))
               for other, s, e, name, *_ in claimed)


def reading_at(readings, line, floor, policy):
    """The OCR reading of a line as a confidence: 1.0 from its floor, below it at most below_booking;
    no reading of the line fails closed, as in best_spot."""
    if readings is None or line >= len(readings):
        return policy.below_booking
    return 1.0 if readings[line] >= floor else min(readings[line] / 100, policy.below_booking)


def omitted(proposed, accounted, state, policy):
    """The exams a search found in the order but the model left out of the booking call, so none
    vanishes without a word: one per piece of text, longer pieces first ("TSH" and "T4 livre" on one
    line are two), at the confidence the order gives it there (search, match, OCR reading). A piece
    an exam the model proposed already accounts for ("Colesterol" inside a booked "Colesterol LDL";
    inside a "T4 Iivre" booked by similarity, only the words of T4 livre) is not left out. Written
    on several lines, the exam is reported on the free one most like a list of exams: where it and
    the other exams found or proposed cover most of the line ("Glicose" in "Exames: Hemograma
    completo, Glicose", not in "Obs: jejum de 8 horas para glicose"). Booking does not change."""
    readings, lines, reported, claimed = state.get('ocr_confidence'), state.get('ocr_lines', []), [], list(accounted)
    finds = state.get('finds', [])

    def listing(line, start, end):  # how much of the line the exams cover
        covered = set(range(start, end))
        for other, s, e in [entry[:3] for entry in claimed] + [piece[:3] for find in finds for piece in find['pieces']]:
            covered.update(range(s, e) if other == line else ())
        return len(covered) / max(len(lines[line]), 1)

    for find in sorted(finds, key=lambda find: (find['pieces'][0][1] - find['pieces'][0][2], -find['score'])):
        free = [piece for piece in find['pieces'] if not held(claimed, find['query'], *piece[:3])]
        if find['code'] in proposed or not free:
            continue
        line, start, end, support = max(free, key=lambda piece: listing(*piece[:3]))  # on a tie, the first
        reading = reading_at(readings, line, find['floor'], policy)
        reported.append({'code': find['code'], 'name': find['name'], 'line': line, 'reason': 'omitted',
                         'confidence': round(min(find['score'], support, reading), 2),
                         'read': state.get('ocr_read', [])[line]})
        claimed.append((line, start, end, None))
    return reported
