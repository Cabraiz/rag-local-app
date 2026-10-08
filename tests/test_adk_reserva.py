"""The spec's reserve model under `adk run`, per request, against a Gemini API on this machine.

The generated agent's model is ADK's FallbackModel: the spec's model, then its fallback_model. Here the
main model is the real Gemini client of the generated file, talking to a fake Gemini API that answers
503 (overloaded) or 429 (out of quota); the reserve is the scripted model of tests/test_adk_run.py,
or the real client too when both must fail. The OCR and catalog MCP servers and the API are the real
ones, and the command is ADK's own `adk run`.
"""
import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from click.testing import CliRunner
from google.adk.cli import cli as adk_cli
from google.adk.cli.cli_tools_click import main as adk_main
from google.adk.models import FallbackModel, Gemini
from google.adk.runners import Runner
from google.genai import types

import tests.test_adk_run as adk
from runtime.adk import Primary, gemini
from tests.test_adk_run import agent_folder  # noqa: F401  (fixture)
from tests.test_alucinacao import IMAGE, appointment, services, stored_ids  # noqa: F401

pytestmark = [
    pytest.mark.skipif(shutil.which('tesseract') is None, reason='Tesseract runs inside the Docker image'),
    pytest.mark.xdist_group('spec-ports'),  # the real servers, on the spec's ports (tests/test_adk_run.py)
    pytest.mark.filterwarnings(r'ignore:\[EXPERIMENTAL\]:UserWarning'),
    pytest.mark.filterwarnings('ignore::DeprecationWarning'),
]
MAIN, RESERVE = 'gemini-3.5-flash', 'gemini-3.5-flash-lite'  # specs/agent.json


class Unavailable(BaseHTTPRequestHandler):
    """The Gemini API, offline: every model in `refused` answers `status`; the calls are kept."""
    status, refused, calls = 503, {MAIN}, []

    def do_POST(self):  # noqa: N802 (http.server's name)
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        model = self.path.split('/models/')[-1].split(':')[0]
        type(self).calls.append(model)
        code = self.status if model in self.refused else 404
        body = json.dumps({'error': {'code': code, 'message': 'This model is currently experiencing high demand',
                                     'status': 'UNAVAILABLE'}}).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def gemini_api(monkeypatch):
    server = ThreadingHTTPServer(('127.0.0.1', 0), Unavailable)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(Unavailable, 'calls', [])
    monkeypatch.setenv('GOOGLE_GEMINI_BASE_URL', f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setenv('GOOGLE_API_KEY', 'not-used')
    yield Unavailable
    server.shutdown()


def reserve_runner(reserve):
    """ADK's Runner as `adk run` builds it, with each step's reserve model replaced by `reserve()`; the
    main model stays the generated file's real Gemini client."""
    class ReserveRunner(Runner):
        def __init__(self, *args, app=None, **kwargs):
            steps = [app.root_agent]
            while steps:
                step = steps.pop()
                steps.extend(getattr(step, 'sub_agents', []))
                if isinstance(getattr(step, 'model', None), FallbackModel):
                    assert isinstance(step.model.models[0], Primary)
                    step.model.models = [step.model.models[0], reserve()]
            super().__init__(*args, app=app, **kwargs)
    return ReserveRunner


def run(agent_folder, monkeypatch, reserve, *lines):  # noqa: F811
    monkeypatch.setattr(adk_cli, 'Runner', reserve_runner(reserve))
    typed = ''.join(f'{line}\n' for line in [*lines, 'exit'])
    done = CliRunner().invoke(adk_main, ['run', '--in_memory', str(agent_folder[0])], input=typed)
    return done


@pytest.mark.parametrize('status', [503, 429])
def test_an_unavailable_main_model_hands_the_same_request_to_the_reserve_and_the_order_is_booked(
        agent_folder, services, gemini_api, monkeypatch, status):  # noqa: F811
    monkeypatch.setattr(gemini_api, 'status', status)
    before = stored_ids(services)
    done = run(agent_folder, monkeypatch, adk.Scripted, IMAGE, 'yes')
    assert done.exception is None, done.output
    assert [appointment(id_) for id_ in stored_ids(services) if id_ not in before] == adk.all_three(), done.output
    assert f'Aviso: modelo principal indisponível; usando {RESERVE}' in done.output
    assert 'Agendamento confirmado pela API' in done.output.split('[clinic_scheduler]: ', 1)[1]
    # Each step asked the main model once, with no retry of a 429 or 503, and then the reserve.
    assert gemini_api.calls and set(gemini_api.calls) == {MAIN}


def test_when_both_models_fail_adk_run_ends_in_one_clear_line_not_a_traceback(
        agent_folder, services, gemini_api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(gemini_api, 'refused', {MAIN, RESERVE})
    before = stored_ids(services)

    def real_reserve():  # the generated file's reserve, without its backoff (5 attempts: about 30 s)
        return Gemini(model=RESERVE, retry_options=types.HttpRetryOptions(attempts=1))
    done = run(agent_folder, monkeypatch, real_reserve, IMAGE)
    assert done.exception is None and 'Traceback' not in done.output, done.output
    assert gemini_api.calls == [MAIN, RESERVE]  # the first step; the next ones make no model call
    report = done.output.split('[clinic_scheduler]: ', 1)[1]
    assert 'Gemini indisponível no momento (HTTP 503); tente novamente; nada foi agendado' in report
    assert stored_ids(services) == before


def test_the_generated_model_has_the_specs_reserve_and_no_reserve_without_one(monkeypatch):
    monkeypatch.delenv('GEMINI_MODEL', raising=False)
    model = gemini(MAIN, fallback=RESERVE)
    assert isinstance(model, FallbackModel) and sorted(model.retriable_status_codes) == [429, 503]
    main, reserve = model.models
    assert (main.model, sorted(main.retry_options.http_status_codes)) == (MAIN, [500])  # 429/503: the reserve, at once
    assert (reserve.model, sorted(reserve.retry_options.http_status_codes)) == (RESERVE, [429, 500, 503])
    assert type(gemini(MAIN)) is Gemini and type(gemini(MAIN, fallback=MAIN)) is Gemini
    monkeypatch.setenv('GEMINI_MODEL', RESERVE)  # what `cli run` sets for its reserve run
    assert type(gemini(MAIN, fallback=RESERVE)) is Gemini
