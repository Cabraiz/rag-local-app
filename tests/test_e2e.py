"""End to end with Gemini: OCR -> RAG -> API through the generated ADK agent.

Runs only when asked, in the compose `tests-e2e` service (ocr, rag and api up, the key from .env):
    docker compose run --rm tests-e2e
The `tests` service never gets the key, so the full suite skips this test; `tests-e2e` sets
E2E_REQUIRED, and there a missing key is a failure, so the run cannot look like a pass.
"""
import os
import re
from pathlib import Path

import httpx
import pytest

import cli
from transpiler import load_spec

REQUIRED = os.environ.get('E2E_REQUIRED') == '1'  # set only by the tests-e2e service
pytestmark = pytest.mark.skipif(
    not os.environ.get('GOOGLE_API_KEY') and not REQUIRED,
    reason='sem GOOGLE_API_KEY: o ponta a ponta chama o Gemini; rode docker compose run --rm tests-e2e (chave do .env)',
)
SPEC_FILE = Path(__file__).resolve().parents[1] / 'specs' / 'agent.json'
# samples/pedido.png asks for Hemograma completo, Glicemia de jejum and Creatinina (tests/test_ocr.py reads them).
EXPECTED = ['FICT-001', 'FICT-002', 'FICT-005']


def api_base_url(spec_file: Path = SPEC_FILE) -> str:
    """The API's address, from the spec's servers.api.openapi_url (checked offline in test_transpiler.py)."""
    return load_spec(spec_file).servers['api'].openapi_url.removesuffix('/openapi.json')


def test_order_image_is_scheduled_with_codes_from_the_catalog(tmp_path, capsys):
    assert os.environ.get('GOOGLE_API_KEY'), 'tests-e2e precisa da GOOGLE_API_KEY: preencha GOOGLE_API_KEY= no .env'
    agent = tmp_path / 'agent.py'
    assert cli.main(['transpile', str(SPEC_FILE), '--output', str(agent)]) == 0
    capsys.readouterr()

    status = cli.main(['run', '--image', 'pedido.png', '--agent', str(agent), '--spec', str(SPEC_FILE)])
    out, err = capsys.readouterr()
    if 'Gemini indisponível no momento' in err:  # provider overload after 5 attempts, not a code failure
        pytest.skip(err.strip())
    assert status == 0, err
    assert '[extract] chamando extract_exam_text' in out and '[search] chamando search_exams' in out
    codes = re.findall(r'\| (FICT-\d{3}) +\|', out)
    assert sorted(codes) == EXPECTED, out  # exactly the order's exams: none missing, none invented
    confirmed = re.search(r'id ([0-9a-f-]{36}), status (\w+)', out)
    assert confirmed and confirmed[2] == 'scheduled', out

    # PII never reaches the terminal: the OCR masked it before the LLM saw the text.
    assert 'PII reconhecida e mascarada pelo OCR:' in out
    assert not re.search(r'\d{3}\.\d{3}\.\d{3}-\d{2}|[\w.]+@[\w.]+', out)

    # The appointment really exists in the API, with the same exams.
    stored = httpx.get(f'{api_base_url()}/appointments/{confirmed[1]}', timeout=5)
    assert stored.status_code == 200
    assert sorted(exam['code'] for exam in stored.json()['exams']) == sorted(codes)
