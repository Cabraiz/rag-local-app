"""The exam catalog and how text is compared with it, shared by the RAG search
(mcp_servers/rag.py) and the PII safety net (guardrails/pii.py), so neither imports the other.

The knowledge base is data/exams.json (120 fictional exams, each with a name and synonyms).
The query and every name or synonym are normalized (accents and case removed) and compared
with two simple, explainable signals from the standard library:
  - shared words (Jaccard overlap): "glicemia jejum" ~ "glicemia de jejum";
  - character similarity (difflib): tolerates OCR typos such as "hemograma compieto".
The best signal is the score.

The API (api/main.py) is a separate service whose image does not carry this module: it
reads the codes and names from the same data/exams.json on its own.
"""
import difflib
import functools
import json
import os
import re
import unicodedata
from pathlib import Path

CATALOG_PATH = Path(os.environ.get('EXAMS_PATH', Path(__file__).resolve().parent / 'data' / 'exams.json'))
MIN_SCORE = 0.6  # the lowest score the RAG returns as a candidate

GREEK = str.maketrans({'α': ' alfa ', 'β': ' beta ', 'γ': ' gama '})  # "β-HCG" is written as "Beta HCG" too
# Abbreviated words of request forms, expanded after the dot is gone: "Hemograma compl." -> completo.
EXPANSIONS = {'compl': 'completo', 'jej': 'jejum'}


def fold(text: str) -> str:
    """Lower case without accents, punctuation kept: "Glicêmia-de JEJUM" -> "glicemia-de jejum".
    Every comparison of texts in the project starts here (RAG, PII, injection, booking)."""
    return ''.join(c for c in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(c))


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
    """The catalog of CATALOG_PATH, read on first use and then kept. Importing this module reads
    no file, so the agent's confidence rules (runtime/), which use only words(), need none. The
    servers still read it when they import: guardrails/pii.py (default arguments),
    guardrails/injection.py (EXAM_TERMS) and mcp_servers/rag.py. That is wanted: they need the
    catalog anyway, and the read happens once, on one thread, before any request."""
    return load_catalog()


def __getattr__(name: str) -> list[dict]:
    """`catalogo.CATALOG` (and `from catalogo import CATALOG`) is catalog(), read on first use."""
    if name == 'CATALOG':
        return catalog()
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


@functools.cache
def vocabulary() -> frozenset[str]:
    """Every word of every exam name and synonym (fold()): what an exam is written with."""
    return frozenset(fold(word) for exam in catalog() for term in [exam['name'], *exam['synonyms']]
                     for word in re.findall(r'\w+', term))


@functools.lru_cache(maxsize=65536)
def exam_word(word: str) -> bool:
    """A word (fold()) of an exam's name, a qualifier, one with the connective "e" the OCR glued to it ("TSHe",
    "eT4"), or one OCR error from one: "compieto", not "risco" (0,80 from "urico": the bound is 0,85)."""
    glued = (word[:-1] if word.endswith('e') else '', word[1:] if word.startswith('e') else '')
    return word in QUALIFIERS or bool({word, *glued} & vocabulary()) or (
        len(word) >= 4 and word.isalpha() and bool(difflib.get_close_matches(word, vocabulary(), 1, 0.85)))


def similarity(query: str, term: str) -> float:
    """Score in [0, 1]; 1.0 means the normalized texts are identical."""
    query_words, term_words = set(query.split()), set(term.split())
    overlap = len(query_words & term_words) / len(query_words | term_words)
    characters = difflib.SequenceMatcher(None, query, term).ratio()
    return max(overlap, characters)
