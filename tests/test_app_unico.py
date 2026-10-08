"""One generated file, one behaviour: what `cli run` and `adk run` / `adk web` share, and what a long
`adk web` process needs (a bounded memory of orders, locks that work across event loops, a quiet
console)."""
import asyncio
import json
import os
import pty
import re
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime import pedido
from runtime.adk import LiveOpenAPIToolset
from runtime.callbacks import BookingCallbacks
from tests.test_adk_seguranca import call, two_medium_exams
from transpiler import transpile

ROOT = Path(__file__).resolve().parents[1]


def test_adk_runs_console_shows_no_experimental_notices(tmp_path):
    # ADK announces every experimental feature it meets (FallbackModel, ResumabilityConfig, the tool
    # confirmation, pluggable auth) on stderr; the generated folder imports runtime first, which silences them.
    transpile(ROOT / 'specs' / 'agent.json', tmp_path / 'generated' / 'agent.py')
    environment = {**os.environ, 'PYTHONPATH': str(ROOT), 'ADK_DISABLE_LOAD_DOTENV': '1', 'PYTHONWARNINGS': 'default'}
    done = subprocess.run([sys.executable, '-m', 'google.adk.cli', 'run', '--in_memory', 'generated'], cwd=tmp_path,
                          input='exit\n', capture_output=True, text=True, timeout=120, env=environment)
    assert 'Running agent clinic_scheduler' in done.stdout, done.stdout + done.stderr
    assert '[EXPERIMENTAL]' not in done.stdout + done.stderr


def console_of_adk_run(folder, home):
    """What `adk run` shows on a terminal (a pseudo-terminal: ADK asks about telemetry only on one), with
    `exit` typed twice: once for a telemetry question, if one came, then for the agent."""
    master, terminal = pty.openpty()
    environment = {**os.environ, 'PYTHONPATH': str(ROOT), 'ADK_DISABLE_LOAD_DOTENV': '1', 'HOME': str(home)}
    process = subprocess.Popen([sys.executable, '-m', 'google.adk.cli', 'run', '--in_memory', 'generated'], cwd=folder,
                               env=environment, stdin=terminal, stdout=terminal, stderr=terminal)
    os.close(terminal)
    os.write(master, b'exit\nexit\n')
    shown = b''
    while True:
        try:
            chunk = os.read(master, 4096)
        except OSError:  # the process closed the terminal
            break
        if not chunk:
            break
        shown += chunk
    process.wait(timeout=120)
    return shown.decode(errors='replace')


@pytest.mark.parametrize('image_config', [True, False])
def test_adk_run_asks_nothing_about_telemetry_with_the_images_config(tmp_path, image_config):
    # ADK asks "Enable telemetry? [Y/n]" (Enter is yes) until ~/.adk/config.json answers; no variable turns
    # it off. The agent image writes the answer "no" (Dockerfile); without it, the question comes.
    written = re.search(r"printf '(\{.*\})\\n' > /home/app/\.adk/config\.json", (ROOT / 'Dockerfile').read_text())
    assert written and json.loads(written[1]) == {'telemetry': False}
    home = tmp_path / 'home'
    (home / '.adk').mkdir(parents=True)
    if image_config:
        (home / '.adk' / 'config.json').write_text(written[1] + '\n', encoding='utf-8')
    transpile(ROOT / 'specs' / 'agent.json', tmp_path / 'generated' / 'agent.py')
    shown = console_of_adk_run(tmp_path, home)
    assert 'Running agent clinic_scheduler' in shown, shown
    assert ('Enable telemetry' not in shown and 'Error' not in shown) is image_config, shown


def test_without_anyone_to_answer_the_record_leaves_the_middle_band_out():
    # `cli run --yes` (or no terminal) says so in the order's record, not in an environment variable.
    callbacks, tool = BookingCallbacks(booking_tool='create_appointment'), SimpleNamespace(name='create_appointment')
    callbacks.can_ask = lambda: True  # a terminal is there, but the record says nobody answers
    state = {}
    two_medium_exams(callbacks, ask=False)
    reply = asyncio.run(callbacks.before_tool(tool, {'exams': [{'code': 'A'}]}, call(state, 'c1')))
    # the reason is the missing yes, not the confidence (an independent run-through: a handwritten order with --yes)
    assert reply == {'blocked': 'nenhum exame pode ser agendado sem a confirmação da lista: rode num terminal, sem --yes, para responder'}
    assert [(item['code'], item['reason']) for item in state['low_confidence']] == [('A', 'needs_confirmation')]


def session(number):
    return SimpleNamespace(app_name='generated', user_id='pessoa', id=f's{number}')


def test_a_long_adk_web_keeps_a_bounded_number_of_orders(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(pedido.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(pedido, 'KEEP_FINISHED', 3)
    orders = pedido.Orders()
    for number in range(5):  # five orders that ended: only the 3 most recent are kept
        orders.of(SimpleNamespace(session=session(number))).finished = True
    running = orders.of(SimpleNamespace(session=session(9)))  # one that stopped past the POST: never finished
    running.booked_appointment = {'id': 'a1'}
    assert [key[2] for key in orders.records] == ['s2', 's3', 's4', 's9']
    clock[0] = pedido.IDLE_SECONDS - 1
    assert orders.of(SimpleNamespace(session=session(9))) is running  # still answers "já foi criado"
    clock[0] = 2 * pedido.IDLE_SECONDS
    orders.of(SimpleNamespace(session=session(7)))
    assert [key[2] for key in orders.records] == ['s7']  # every record idle that long is gone


def test_the_contract_is_fetched_under_a_lock_of_each_event_loop(monkeypatch):
    # adk web serves requests on several threads and loops; an asyncio.Lock made once belongs to the
    # first loop that waits on it, and a second loop waiting on it fails.
    monkeypatch.setenv('ALLOWED_HOSTS', '127.0.0.1')
    toolset, loads = LiveOpenAPIToolset(openapi_url='http://127.0.0.1:9/openapi.json', base_url='http://127.0.0.1:9',
                                        tool_filter=['create_appointment']), []

    class Loaded:
        async def get_tools(self, readonly_context=None):
            return ['create_appointment']

    async def load():
        loads.append(threading.current_thread().name)
        await asyncio.sleep(0.3)
        return Loaded()
    monkeypatch.setattr(toolset, 'load', load)
    results, failures = [], []

    def loop():
        async def three_at_once():
            return await asyncio.gather(*(toolset.get_tools() for _ in range(3)))
        try:
            results.extend(asyncio.run(three_at_once()))
        except Exception as error:  # reported below
            failures.append(error)
    threads = [threading.Thread(target=loop) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert failures == [] and results == [['create_appointment']] * 6
    assert 1 <= len(loads) <= 2  # once per loop at most, never once per call


def test_copying_a_record_keeps_the_file_name_private():
    context = SimpleNamespace(state={})
    pedido.Orders.publish(context, pedido.OrderRecord(image_file='pedido-joao-silva.png', image_token='pedido-1.png',
                                                      finished=True, own_key='k', refused=True))
    assert context.state == {'image_token': 'pedido-1.png', 'refused': True}


pytestmark = pytest.mark.filterwarnings(r'ignore:\[EXPERIMENTAL\]:UserWarning')


def test_a_missing_api_key_ends_the_step_with_one_clear_line():
    """`adk run` without GOOGLE_API_KEY: the step says what to do instead of a raw traceback."""
    from types import SimpleNamespace

    from runtime import callbacks

    booking = callbacks.BookingCallbacks.__new__(callbacks.BookingCallbacks)
    published = {}
    booking.orders = SimpleNamespace(of=lambda context: pedido.OrderRecord(),
                                     publish=lambda context, order: published.update(order.view()))
    error = ValueError('No API key was provided. Please pass a valid API key.')
    reply = booking.model_failed(None, None, error)
    assert 'GOOGLE_API_KEY não definida' in reply.content.parts[0].text
    assert published['model_error'] == callbacks.NO_KEY
    assert booking.model_failed(None, None, RuntimeError('something else')) is None
