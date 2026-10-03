"""CF-APP-02 fixed compiler corpus and real ADK child isolation proofs."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from clinic_adk.compiler import AgentSpec, RESERVED_NAMES, emit, parse_spec
from clinic_adk.errors import SafeError
from clinic_adk.sandbox import check_generated

PROJECT = Path(__file__).resolve().parents[1]
BASE = json.loads((PROJECT / 'examples/agent.json').read_bytes())
CORPUS = json.loads((PROJECT / 'tests/fixtures/compiler_adversarial.json').read_bytes())


@pytest.mark.parametrize('change', CORPUS['root_changes'])
def test_sealed_root_rejections(change):
    value = copy.deepcopy(BASE)
    value.update(change)
    with pytest.raises(SafeError) as error:
        parse_spec(json.dumps(value).encode())
    assert 'CANARY_CF02' not in str(error.value)
    assert '__import__' not in str(error.value)


@pytest.mark.parametrize('change', CORPUS['stage_changes'])
def test_sealed_stage_rejections(change):
    value = copy.deepcopy(BASE)
    value['stages'][0].update(change)
    with pytest.raises(SafeError):
        parse_spec(json.dumps(value).encode())


@pytest.mark.parametrize('name', sorted(RESERVED_NAMES))
def test_all_reserved_identifiers_are_rejected(name):
    value = copy.deepcopy(BASE)
    value['name'] = name
    with pytest.raises(SafeError):
        parse_spec(json.dumps(value).encode())


@pytest.mark.parametrize('raw', [b'', b'[]', b'null', b'{}', b'\xff',
    b'{"a":1,"a":2}', b'{"timeout_seconds":NaN}', b'x' * 16385,
    b'[' * 1100 + b']' * 1100, json.dumps(BASE).encode('utf-16'),
    json.dumps(BASE).encode('utf-32')])
def test_raw_boundary_corpus(raw):
    with pytest.raises(SafeError):
        parse_spec(raw)


@pytest.mark.parametrize('case', ['duplicate', 'cycle', 'reorder', 'missing', 'long', 'extra'])
def test_graph_bounds(case):
    value = copy.deepcopy(BASE)
    if case == 'duplicate':
        value['stages'][1]['name'] = value['stages'][0]['name']
    elif case == 'cycle':
        value['stages'][0]['next'] = value['stages'][0]['name']
    elif case == 'reorder':
        value['stages'].reverse()
    elif case == 'missing':
        value['stages'].pop()
    elif case == 'long':
        value['name'] = 'a' * 49
    else:
        value['stages'].append({'name': 'extra', 'kind': 'format'})
    with pytest.raises(SafeError):
        parse_spec(json.dumps(value).encode())


@pytest.mark.parametrize('case', ['mutated_name', 'mutated_kind', 'timeout_payload', 'constructed'])
def test_unvalidated_ir_never_reaches_python(case):
    spec = parse_spec(json.dumps(BASE).encode())
    if case == 'mutated_name':
        spec.stages[0].name = "x\n    __import__('os').system('id')"
    elif case == 'mutated_kind':
        spec.stages[0].kind = 'shell'
    elif case == 'timeout_payload':
        spec.timeout_seconds = "__import__('os').system('id')"
    else:
        spec = AgentSpec.model_construct(**{**BASE, 'timeout_seconds': 'PAYLOAD'})
    with pytest.raises(SafeError):
        emit(spec)


def test_emitter_determinism_and_allowlisted_ast():
    spec = parse_spec(json.dumps(BASE).encode())
    source = emit(spec)
    equivalent = parse_spec(json.dumps(dict(reversed(list(BASE.items()))), indent=4).encode())
    assert source == emit(equivalent)
    tree = ast.parse(source)
    assert [n.module for n in tree.body if isinstance(n, ast.ImportFrom)] == [
        'google.adk', 'google.adk.workflow', 'clinic_adk.runtime']
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id in ('eval', 'exec', '__import__', 'open') for n in ast.walk(tree))
    compile(source, '<fixture-adk>', 'exec')


def test_published_schema_matches_model():
    assert json.loads((PROJECT / 'examples/agent.schema.json').read_bytes()) == AgentSpec.model_json_schema()


@pytest.mark.parametrize('filename', ['agent.json', 'agent-variant.json'])
def test_real_adk_subprocess_runs_twice_with_identical_hash(filename):
    spec = parse_spec((PROJECT / 'examples' / filename).read_bytes())
    first = check_generated(spec)
    second = check_generated(spec)
    assert first == second
    assert first['source_sha256'] == hashlib.sha256(emit(spec).encode()).hexdigest()
    assert first['adk_version'] == '2.10.0'
    assert first['self_checks'] == {'network': 2, 'filesystem': 2, 'process': 1}
    assert first['denied'] == {'network': 0, 'filesystem': 0, 'process': 0}


def test_real_child_network_filesystem_and_process_spies(tmp_path):
    outside = tmp_path / 'outside-canary.txt'
    outside.write_text('CANARY_CF02', encoding='utf8')
    child = tmp_path / 'child'
    child.mkdir()
    script = child / 'probe.py'
    script.write_text(textwrap.dedent('''
        import json, os, socket, subprocess, sys
        from pathlib import Path
        sys.path.insert(0, '/app/src')
        from clinic_adk.sandbox_worker import install_fence, limits
        limits()
        'egress.invalid'.encode('idna')  # Codec initialization is trusted setup.
        root = Path.cwd()
        counts = install_fence(root, ['/app/src'])
        attacks = [
            lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM),
            lambda: socket.getaddrinfo('egress.invalid', 443),
            lambda: (root / '../outside-canary.txt').read_text(),
            lambda: (root / '../outside-canary.txt').write_text('CHANGED'),
            lambda: os.rename(root / 'probe.py', root / '../escape.py'),
            lambda: subprocess.run([sys.executable, '-c', 'pass'], shell=False),
        ]
        for attack in attacks:
            try:
                attack()
            except PermissionError as error:
                assert str(error).startswith('SANDBOX_DENIED_')
                continue
            raise AssertionError('SIDE_EFFECT_NOT_BLOCKED')
        print(json.dumps(counts, sort_keys=True))
    '''), encoding='utf8')
    result = subprocess.run([sys.executable, '-I', '-B', str(script)], cwd=child,
                            capture_output=True, timeout=10, shell=False)
    assert result.returncode == 0, result.stderr.decode()
    assert json.loads(result.stdout) == {'network': 2, 'filesystem': 3, 'process': 1}
    assert outside.read_text() == 'CANARY_CF02'
    assert not (tmp_path / 'escape.py').exists()


def test_preflight_parent_has_hard_timeout_and_sanitized_errors(monkeypatch):
    def deadline(*args, **kwargs):
        assert kwargs['timeout'] == 25 and kwargs['shell'] is False
        assert kwargs['env']['OTEL_SDK_DISABLED'] == 'true'
        assert Path(kwargs['cwd']).is_dir()
        raise subprocess.TimeoutExpired('CANARY_CF02', 25)
    monkeypatch.setattr(subprocess, 'run', deadline)
    with pytest.raises(SafeError, match='SANDBOX_TIMEOUT'):
        check_generated(parse_spec(json.dumps(BASE).encode()))


def test_real_preflight_deadline_reaps_child_and_removes_temporary(monkeypatch):
    from clinic_adk import sandbox
    before = set(Path('/tmp').glob('cf-adk-*'))
    monkeypatch.setattr(sandbox, 'PREFLIGHT_TIMEOUT', 0.02)
    with pytest.raises(SafeError, match='SANDBOX_TIMEOUT'):
        check_generated(parse_spec(json.dumps(BASE).encode()))
    assert set(Path('/tmp').glob('cf-adk-*')) == before
    import os
    with pytest.raises(ChildProcessError):
        os.waitpid(-1, os.WNOHANG)


def test_real_posix_resource_limits():
    code = '''import sys,json,resource
sys.path.insert(0, '/app/src')
from clinic_adk.sandbox_worker import limits
limits()
print(json.dumps([resource.getrlimit(key) for key in
    (resource.RLIMIT_CPU, resource.RLIMIT_AS, resource.RLIMIT_FSIZE,
     resource.RLIMIT_NOFILE, resource.RLIMIT_CORE, resource.RLIMIT_NPROC)]))
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code],
                            capture_output=True, timeout=10, shell=False)
    assert result.returncode == 0, result.stderr.decode()
    assert json.loads(result.stdout) == [[n, n] for n in (15, 1024 ** 3, 65536, 64, 0, 0)]
