"""The exam catalog and how text is compared with it: one matcher (ExamMatcher) shared by the RAG search
(mcp_servers/rag.py), the PII mask (guardrails/pii.py) and the intent rules (guardrails/intent.py), so none of
them imports another or keeps its own copy of the catalog.

The knowledge base is data/exams.json (120 fictional exams, each with a name and synonyms).
The query and every name or synonym are normalized (accents and case removed) and compared
with two simple, explainable signals from the standard library:
  - shared words (Jaccard overlap): "glicemia jejum" ~ "glicemia de jejum";
  - character similarity (difflib): tolerates OCR typos such as "hemograma compieto".
The best signal is the score. A single word is an exam word when it is one, or one OCR error
from one by the Tolerance of the reader that asks (LIST_WORD, NOT_A_NAME, MAY_LEAVE).

The API (api/main.py) is a separate service whose image does not carry this module: it
reads the codes and names from the same data/exams.json on its own.
"""
import difflib
import functools
import json
import os
import re
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

CATALOG_PATH = Path(os.environ.get('EXAMS_PATH', Path(__file__).resolve().parent / 'data' / 'exams.json'))
MIN_SCORE = 0.6  # the lowest score the RAG returns as a candidate

GREEK = str.maketrans({'α': ' alfa ', 'β': ' beta ', 'γ': ' gama '})  # "β-HCG" is written as "Beta HCG" too
# Abbreviated words of request forms, expanded after the dot is gone: "Hemograma compl." -> completo.
EXPANSIONS = {'compl': 'completo', 'jej': 'jejum'}


def fold(text: str) -> str:
    """Lower case without accents, punctuation kept: "Glicêmia-de JEJUM" -> "glicemia-de jejum".
    Every comparison of texts in the project starts here (RAG, PII, injection, booking)."""
    return ''.join(c for c in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(c))


def plain(text: str) -> str:
    """fold() char by char, so a position in it is the same in the text: "NÃO" -> "nao". ExamMatcher.pattern reads it."""
    return ''.join((fold(char) or ' ')[:1] for char in text)


def words(text: object) -> str:
    """fold() and only the words, one space apart: "Ferro sérico — Conceição" -> "ferro serico
    conceicao". A value that is not text (from the model) is compared as its str()."""
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in fold(str(text))).split())


def normalize(text: str) -> str:
    """words() as the RAG search compares them, with Greek letters spelled out and the
    abbreviations of request forms expanded: 'β-HCG' -> 'beta hcg', 'Glicemia jej.' ->
    'glicemia jejum'. Only the search (and the safety net that must score like it) uses this
    variant: the PII guard and the booking checks compare what was written, word for word."""
    return ' '.join(EXPANSIONS.get(word, word) for word in words(text.casefold().translate(GREEK)).split())


# Shared by every reader of an order (the OCR's guards, the RAG search, the booking runtime). A list marker
# before the first exam: "1)", "2.", "-", "•"; never the "25-" of "25-OH vitamina D". The connectives
# between the words of a line, and the antibody classes (a class names a different exam: "Chagas IgM").
CONNECTIVES = frozenset(('a', 'o', 'as', 'os', 'e', 'de', 'da', 'do', 'das', 'dos', 'em', 'no', 'na', 'com', 'sem', 'para',
                         'por'))
CLASSES = frozenset(('iga', 'igg', 'igm', 'ige'))
LIST_MARKER = re.compile(r'^\s*(?:[-–•*·>]+|\(?\d{1,2}\s*(?:[.)]+|-(?!\s*(?:oh|hidrox|\d))))\s*', re.IGNORECASE)
# Words that qualify an exam ("TSH ULTRASSENSÍVEL", "Hemograma Completo Com Plaquetas"): never a name.
EXAM_MODIFIERS = frozenset(('ultrassensivel', 'ultra', 'sensivel', 'fracoes', 'fracao', 'plaquetas', 'total', 'livre',
                            'serico', 'serica', 'soro', 'plasma', 'sangue', 'urina', 'jejum', 'basal', 'dosagem', 'pesquisa',
                            'contagem', 'quantitativo', 'quantitativa', 'qualitativo', 'qualitativa', 'completo',
                            'completa', 'automatizado', 'colesterol'))
# What may stand next to an exam on a line that books alone (guardrails/intent.py): its qualifiers, the
# sample, a type ("Urina tipo I", "HIV 1 e 2", "Vitamina B12 e D"), a fasting time, routine or follow-up,
# the disease a serology is for ("IgG para toxoplasmose", "Doença de Chagas IgG", "Hepatite A IgM").
QUALIFIERS = EXAM_MODIFIERS | {'e', 'de', 'do', 'da', 'em', 'com', 'para', 'tipo', 'i', 'ii', '1', '2', 'a', 'b', 'c', 'd',
                               'igg', 'igm', 'iga', 'ige', 'rotina', 'controle', 'urgente', 'h', 'hs', 'hrs', 'hora',
                               'horas', 'manha', 'pos', 'prandial', 'isolada', 'amostra', 'sorologia', 'doenca',
                               'hepatite', 'hav', 'imunoglobulina', 'imunoglobulinas'}


def load_catalog(path: Path = CATALOG_PATH) -> list[dict]:
    """Each exam plus 'terms': the normalized forms of its name and synonyms."""
    exams = json.loads(Path(path).read_text(encoding='utf-8'))
    return [{**exam, 'terms': [normalize(term) for term in [exam['name'], *exam['synonyms']]]} for exam in exams]


@functools.cache
def catalog() -> list[dict]:
    """The catalog of CATALOG_PATH, read on first use and then kept: importing this module reads no file (runtime/ uses
    only words()). The servers read it at import, building matcher() in guardrails/ and mcp_servers/rag.py: they need
    it anyway, and the read happens once, on one thread, before any request."""
    return load_catalog()


def __getattr__(name: str) -> list[dict]:
    """`catalogo.CATALOG` (and `from catalogo import CATALOG`) is catalog(), read on first use."""
    if name == 'CATALOG':
        return catalog()
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def similarity(query: str, term: str) -> float:
    """Score in [0, 1]; 1.0 means the normalized texts are identical."""
    query_words, term_words = set(query.split()), set(term.split())
    overlap = len(query_words & term_words) / len(query_words | term_words)
    characters = difflib.SequenceMatcher(None, query, term).ratio()
    return max(overlap, characters)


class Tolerance(NamedTuple):
    """How far a word (fold()) may be from an exam word and still be one, by difflib's ratio."""
    cutoff: float  # from this ratio, a misread word is the exam word
    shortest: int  # a shorter word counts only as written
    modifiers: bool  # whether EXAM_MODIFIERS ("plaquetas", "sérico") count as exam words


# One per decision, named for it; each value is set by what a mistake costs there.
LIST_WORD = Tolerance(0.85, 4, False)  # books alone (intent): "compieto" is one; "risco" (0.80 from "urico") is not
NOT_A_NAME = Tolerance(0.85, 5, True)  # never part of a name (PII rule 2): from 5 letters, as "Edna" is 0.86 from "DNA"
MAY_LEAVE = Tolerance(0.80, 4, True)  # may leave the OCR in an exam (PII rule 4): "Urlna" 0.80; first names never count


class ExamMatcher:
    """The catalog's names: `written` (words(), as an order writes them: PII, intent, OCR) and `searched` (normalize(),
    the RAG search), each -> its exam; `pattern`, the written names on plain() text, longest first; `vocabulary`, the
    words (fold()) they are made of, and `with_modifiers`, those and EXAM_MODIFIERS."""

    def __init__(self, exams: list[dict]):
        self.exams = exams
        self.names = [term for exam in exams for term in [exam['name'], *exam['synonyms']]]  # as in the catalog
        self.written = {words(term): exam for exam in exams for term in [exam['name'], *exam['synonyms']]}
        self.searched = {term: exam for exam in exams for term in exam['terms']}
        self.word_sets = {frozenset(term.split()): exam['code'] for term, exam in self.searched.items()}  # "igg toxoplasmose" too
        self.vocabulary = frozenset(fold(word) for term in self.names for word in re.findall(r'\w+', term))
        self.with_modifiers = self.vocabulary | EXAM_MODIFIERS
        self.pattern = re.compile('|'.join(r'(?<![a-z0-9])' + r'[\W_]+'.join(map(re.escape, term.split())) + r'(?![a-z0-9])'
                                           for term in sorted(self.written, key=len, reverse=True) if term))
        self.is_exam_word = functools.lru_cache(maxsize=65536)(self._is_exam_word)
        self.search_score = functools.lru_cache(maxsize=65536)(self._search_score)

    def _is_exam_word(self, word: str, tolerance: Tolerance) -> bool:
        """A word of an exam's name (or a modifier, when the tolerance counts them), or one OCR error from one."""
        pool = self.with_modifiers if tolerance.modifiers else self.vocabulary
        return word in pool or (len(word) >= tolerance.shortest and bool(difflib.get_close_matches(word, pool, 1, tolerance.cutoff)))

    def closest(self, query: str) -> Iterator[tuple[float, str, dict]]:
        """For each exam: the best score of a normalized query and the name it came from (a tie: the first, its name)."""
        for exam in self.exams:
            score, index = max((similarity(query, term), -index) for index, term in enumerate(exam['terms']))
            yield score, [exam['name'], *exam['synonyms']][-index], exam

    def _search_score(self, text: str) -> float:
        """The best score the RAG search (mcp_servers/rag.py) would give this text, list number aside."""
        query = normalize(text).lstrip('0123456789 ').strip()
        return max((score for score, *_ in self.closest(query)), default=0.0) if query else 0.0


@functools.cache
def matcher() -> ExamMatcher:
    """The matcher of catalog(), built on first use and then kept."""
    return ExamMatcher(catalog())


@functools.lru_cache(maxsize=65536)
def exam_word(word: str) -> bool:
    """A word (fold()) of a list line that belongs to its exams: a qualifier, an exam word, one with the connective
    "e" the OCR glued to it ("TSHe", "eT4"), or one OCR error from an exam word (LIST_WORD)."""
    exams = matcher()
    glued = (word[:-1] if word.endswith('e') else '', word[1:] if word.startswith('e') else '')
    return word in QUALIFIERS or bool({word, *glued} & exams.vocabulary) or (word.isalpha() and exams.is_exam_word(word, LIST_WORD))
