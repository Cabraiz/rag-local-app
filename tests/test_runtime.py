"""The generated agent.py with the runtime library only, outside the repository."""
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import runtime
from runtime import BookingCallbacks
from tests.versionados import EXAMPLE_SPECS
from transpiler import transpile

ROOT = Path(__file__).resolve().parents[1]
SPEC_FILE = ROOT / 'specs' / 'agent.json'
# Run in a fresh interpreter (-I: no PYTHONPATH, no user site) with only the bundle's folder on
# the path: import the agent, then one booking through its callbacks, as the runner calls them, with
# nobody to answer [s/N]: one exam read clearly is booked, one in the middle band is left out.
CHECK = '''
import json, sys
from types import SimpleNamespace
sys.path.insert(0, '')
import agent, catalogo, runtime

tool = lambda name: SimpleNamespace(name=name)
context = SimpleNamespace(state={}, tool_confirmation=None, actions=SimpleNamespace(skip_summarization=False))
agent.CALLBACKS.can_ask = lambda: False
reply = {'lines': ['- Hemograma completo', '- Glicemia de jejum'], 'line_confidence': [95.0, 95.0],
         'line_intent': ['request', 'request'], 'contested_exams': [], 'page_clean': True, 'pii_masked': {}}
agent.CALLBACKS.after_tool(tool('extract_exam_text'), {}, context, {'content': [{'type': 'text', 'text': json.dumps(reply)}]})
for code, name, score in (('FICT-001', 'Hemograma completo', 1.0), ('FICT-002', 'Glicemia de jejum', 0.8)):
    found = {'structuredContent': {'result': [{'code': code, 'name': name, 'score': score}]}}
    agent.CALLBACKS.after_tool(tool('search_exams'), {'query': name}, context, found)
args = {'exams': [{'code': 'FICT-001', 'name': 'Hemograma completo'}, {'code': 'FICT-002', 'name': 'Glicemia de jejum'}]}
print(agent.root_agent.name, [step.name for step in agent.root_agent.sub_agents])
reply = agent.CALLBACKS.before_tool(tool('create_appointment'), args, context)
print(reply, [exam['code'] for exam in args['exams']],
      [(item['code'], item['reason']) for item in context.state['low_confidence']])
print(runtime.__file__)
print(catalogo.__file__)
print(sorted(name for name in ('transpiler', 'cli', 'api', 'mcp_servers', 'guardrails') if name in sys.modules),
      catalogo.catalog.cache_info().currsize)
'''


def bundle(tmp_path, spec_file):
    """What the agent needs: agent.py, the runtime library and catalogo.py (for words()). No catalog
    file, transpiler, CLI, API, MCP servers or guardrails."""
    folder = tmp_path / 'bundle'
    transpile(spec_file, folder / 'agent.py')
    shutil.copytree(ROOT / 'runtime', folder / 'runtime', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy(ROOT / 'catalogo.py', folder)
    return folder


def test_agent_py_runs_with_the_runtime_library_alone_outside_the_repository(tmp_path):
    bundle_dir = bundle(tmp_path, SPEC_FILE)
    done = subprocess.run([sys.executable, '-I', '-c', CHECK], cwd=bundle_dir, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    name, booking, path, catalog_path, others = done.stdout.splitlines()[-5:]
    assert name == "clinic_scheduler ['extract', 'search', 'schedule']"
    # The confidence rules ran (the line, the search, the OCR reading): the exam at 1,0 goes to the
    # API; the one at 0,80 needs a yes, and with nobody to ask it is left out and reported.
    assert booking == "None ['FICT-001'] [('FICT-002', 'needs_confirmation')]"
    assert Path(path).parent == bundle_dir / 'runtime' and Path(catalog_path).parent == bundle_dir
    assert others == '[] 0'  # none of the other packages imported, and the catalog never read


@pytest.mark.parametrize('spec', EXAMPLE_SPECS)
def test_every_example_spec_imports_with_the_runtime_library_alone(tmp_path, spec):
    check = "import sys; sys.path.insert(0, ''); import agent; print(agent.root_agent.name)"
    done = subprocess.run([sys.executable, '-I', '-c', check], cwd=bundle(tmp_path, ROOT / 'specs' / spec),
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr


def test_a_generated_file_for_another_interface_stops_with_a_clear_message():
    runtime.require_api(runtime.API_VERSION)
    with pytest.raises(ImportError, match='gere de novo com `python -m cli transpile'):
        runtime.require_api(runtime.API_VERSION + 1)


@pytest.mark.parametrize('booking_tool', [None, 'create_appointment'])
def test_an_api_call_that_is_not_the_checked_booking_is_refused(booking_tool):
    # An OpenAPI operation (ADK's RestApiTool has an `endpoint`) other than the booking tool is
    # refused before any request, with no booking role too: a missing role never means "unchecked".
    callbacks, context = BookingCallbacks(booking_tool=booking_tool), SimpleNamespace(state={})
    for name in ('delete_appointment', 'create_appointment' if booking_tool is None else 'get_appointment'):
        reply = callbacks.before_tool(SimpleNamespace(name=name, endpoint=object()), {}, context)
        assert reply == {'blocked': f'operação de API sem papel conferido pelo runtime ({name}); nada foi enviado'}


def test_any_tool_outside_the_roles_is_refused_and_the_booking_takes_only_exams_and_the_key():
    callbacks, context = BookingCallbacks(ocr_tool='extract_exam_text', search_tool='search_exams',
                                          booking_tool='create_appointment'), SimpleNamespace(state={})
    # An MCP tool without a role (a free file name would escape the run's file token): refused.
    reply = callbacks.before_tool(SimpleNamespace(name='read_any_file'), {'filename': '/etc/passwd'}, context)
    assert reply == {'blocked': 'ferramenta sem papel conferido pelo runtime (read_any_file); nada foi enviado'}
    # The booking call with a field the runtime does not check (free text from the model): refused.
    reply = callbacks.before_tool(SimpleNamespace(name='create_appointment'), {'exams': [], 'notas': 'x'}, context)
    assert reply == {'blocked': 'campo(s) fora do agendamento conferido: notas'}


@pytest.mark.parametrize('response, kept', [
    ({'appointment_id': 'a1'}, True),  # any answer that is not an error is the run's appointment
    ({'error': 'Tool create_appointment execution failed. Status Code: 500'}, False),
    ({'blocked': 'nenhum exame com confiança suficiente para agendar'}, False),
])
def test_the_runs_one_appointment_is_any_successful_answer(response, kept):
    callbacks, context = BookingCallbacks(booking_tool='create_appointment'), SimpleNamespace(state={})
    callbacks.after_tool(SimpleNamespace(name='create_appointment'), {}, context, response)
    assert context.state.get('booked_appointment') == (response if kept else None)
