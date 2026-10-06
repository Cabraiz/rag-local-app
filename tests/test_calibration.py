"""The 0.90 booking threshold, recomputed with the current RAG on the versioned calibration set.

tests/calibration/queries.jsonl has 631 queries that reached the catalog (a top-1 hit):
103 OCR lines read from 40 fictional request images (personal data already masked) and
528 synthetic queries (exact names, upper case, synonyms, 1- and 2-edit typos, and lines
that are not exams). `expected` is the right code, or null when nothing should be booked;
`score` is the RAG score recorded when the threshold was chosen. "Somatomedina C" was first
labelled as not an exam; it is the other name of IGF-1 (FICT-045) and is labelled so.
"""
import json
import re
from pathlib import Path

from mcp_servers import rag
from runtime.confianca import BookingPolicy

ROOT = Path(__file__).resolve().parents[1]
QUERIES = [json.loads(line) for line in (ROOT / 'tests/calibration/queries.jsonl').read_text(encoding='utf-8').splitlines()]
# The threshold the generated agent uses (runtime's booking policy), so the test follows the code.
THRESHOLD = BookingPolicy().min_confidence
MIN_RECALL = 0.84  # the share of right matches kept, as stated in docs/arquitetura.md


def top_hit(query):
    hits = rag.search(query, top_k=1)
    return (hits[0]['code'], hits[0]['score']) if hits else (None, 0.0)


def test_calibration_set_has_no_personal_data():
    personal = re.compile(r'\d{3}\.\d{3}\.\d{3}|\d{4,5}-\d{4}|\d{2}/\d{2}/\d{4}|@|'
                          r'(?i:paciente|cpf|telefone|e-?mail|crm|nascimento|conv[eê]nio|m[eé]dic[oa])')
    assert len(QUERIES) == 631
    assert not [q['query'] for q in QUERIES if personal.search(q['query'])]


def test_threshold_lets_no_wrong_match_through_and_keeps_most_right_ones():
    right, wrong = [], []
    for query in QUERIES:
        code, score = top_hit(query['query'])
        is_right = code is not None and code == query['expected']
        # A catalog change may raise a score (a new synonym), never lower a right match.
        assert not is_right or score >= query['score'], query
        (right if is_right else wrong).append(score)
    assert (len(right), len(wrong)) == (621, 10)
    assert [score for score in wrong if score >= THRESHOLD] == []
    kept = sum(score >= THRESHOLD for score in right)
    assert kept / len(right) >= MIN_RECALL, f'{kept} of {len(right)} right matches kept at {THRESHOLD}'
    # Below the threshold the same set lets wrong matches through: the cut is what removes them.
    assert sum(score >= 0.80 for score in wrong) > 0
