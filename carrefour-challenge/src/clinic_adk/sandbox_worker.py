"""Fixed child entrypoint. Only validated JSON arrives on stdin; no path/code flags."""
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import resource
import runpy
import socket
import subprocess
import sys
import sysconfig


def limits():
    for key, value in ((resource.RLIMIT_CPU, 15), (resource.RLIMIT_AS, 1024 ** 3),
                       (resource.RLIMIT_FSIZE, 65536), (resource.RLIMIT_NOFILE, 64),
                       (resource.RLIMIT_CORE, 0), (resource.RLIMIT_NPROC, 0)):
        resource.setrlimit(key, (value, value))


def install_fence(temporary, roots):
    """Record and refuse sockets, processes and filesystem accesses outside roots."""
    counts = {'network': 0, 'filesystem': 0, 'process': 0}
    temporary = Path(temporary).resolve()
    roots = tuple(Path(root).resolve() for root in roots) + (temporary,)

    def denied(category):
        counts[category] += 1
        raise PermissionError('SANDBOX_DENIED_' + category.upper())

    def allowed(value, write=False):
        # Permit already-open file descriptors; do not reopen /proc/self/fd paths.
        if type(value) is int:
            return True
        try:
            path = Path(os.fsdecode(value)).resolve()
            return any(path.is_relative_to(root) for root in ((temporary,) if write else roots))
        except (TypeError, ValueError, OSError):
            return False

    writes = {'os.remove', 'os.rmdir', 'os.mkdir', 'os.rename', 'os.link', 'os.symlink',
              'os.chmod', 'os.chown', 'os.truncate', 'os.utime'}

    def audit(event, args):
        if event.startswith('socket.'):
            if event == 'socket.gethostname':  # Local kernel metadata; no DNS or IO.
                return
            # asyncio may use an AF_UNIX socketpair for wakeups; no IP sockets/DNS.
            if event == 'socket.__new__' and args[1] == socket.AF_UNIX:
                return
            denied('network')
        if event.startswith(('subprocess.', 'os.exec', 'os.spawn')) or event in (
                'os.system', 'os.fork', 'os.forkpty', 'os.posix_spawn', 'pty.spawn'):
            denied('process')
        if event == 'open':
            mode, flags = args[1:3]
            write = (bool(set(mode or '') & set('wax+'))
                     or bool((flags or 0) & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)))
            if not allowed(args[0], write):
                denied('filesystem')
        elif event in ('os.listdir', 'os.scandir'):
            if not allowed(args[0]):
                denied('filesystem')
        elif event in writes:
            paths = args[:2] if event in ('os.rename', 'os.link', 'os.symlink') else args[:1]
            if not all(allowed(path, True) for path in paths):
                denied('filesystem')
    sys.addaudithook(audit)
    return counts


def verify_fence(counts):
    """Fixed negative probes inside the same worker that will run the ADK graph."""
    attacks = (
        ('network', lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM)),
        ('network', lambda: socket.getaddrinfo('blocked.invalid', 443)),
        ('filesystem', lambda: open('/etc/passwd', 'rb')),
        ('filesystem', lambda: open('/cf-adk-preflight-denied', 'wb')),
        ('process', lambda: subprocess.run([sys.executable, '-c', 'pass'], shell=False)),
    )
    for category, attack in attacks:
        try:
            attack()
        except PermissionError as error:
            if str(error) == 'SANDBOX_DENIED_' + category.upper():
                continue
            raise RuntimeError('FENCE_NOT_PROVEN') from None
        raise RuntimeError('FENCE_NOT_PROVEN')
    return dict(counts)


async def run_graph(module, spec):
    from google.adk import Workflow
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    class ProbeRuntime:
        def __init__(self):
            self.stages = []

        async def step(self, kind, node_input):
            self.stages.append(kind)
            return {'stages': list(self.stages)}

    runtime = ProbeRuntime()
    agent = module['build_agent'](runtime)
    if not isinstance(agent, Workflow) or not isinstance(module['root_agent'], Workflow):
        raise ValueError('NOT_ADK')
    sessions = InMemorySessionService()
    await sessions.create_session(app_name='compiler_preflight', user_id='fictional', session_id='fixed')
    runner = Runner(app_name='compiler_preflight', node=agent, session_service=sessions)
    output = None
    async with asyncio.timeout(min(10, spec.timeout_seconds)):
        async for event in runner.run_async(user_id='fictional', session_id='fixed',
                new_message=types.Content(role='user', parts=[types.Part(text='fixture ficticia')])):
            candidate = getattr(event, 'output', None)
            if isinstance(candidate, dict) and 'stages' in candidate:
                output = candidate['stages']
    if output != runtime.stages or output != ['ocr', 'retrieve', 'validate', 'schedule', 'format']:
        raise ValueError('INCOMPLETE_ADK')
    return output


def main():
    limits()
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))  # Fixed trusted package root, independent of JSON.
    from clinic_adk.compiler import parse_spec, emit
    # Load dependencies before fencing their discovery; runtime actions stay fenced.
    import clinic_adk.runtime
    import google.adk.runners
    import google.adk.sessions
    import urllib3.util.connection  # Trusted IPv6 capability probe before graph fence.
    'blocked.invalid'.encode('idna')
    spec = parse_spec(sys.stdin.buffer.read(16385))
    source = emit(spec).encode()
    temporary = Path.cwd().resolve()
    path = temporary / 'generated.py'
    path.write_bytes(source)
    compile(source, '<generated-adk-preflight>', 'exec')
    version = importlib.metadata.version('google-adk')
    # Create the event loop before the socket fence (its Unix wakeup socketpair).
    loop = asyncio.new_event_loop()
    roots = [project, project.parent / 'data', sysconfig.get_path('stdlib'),
             sysconfig.get_path('purelib'), sysconfig.get_path('platlib')]
    counts = install_fence(temporary, roots)
    self_checks = verify_fence(counts)
    try:
        module = runpy.run_path(str(path), run_name='generated_clinic_preflight')
        stages = loop.run_until_complete(run_graph(module, spec))
    finally:
        loop.close()
    print(json.dumps({'sandbox': 'posix-offline-adk-preflight', 'source_sha256': hashlib.sha256(source).hexdigest(),
                      'stages': stages, 'adk_version': version, 'self_checks': self_checks,
                      'denied': {key: counts[key] - self_checks[key] for key in counts}}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        # Never reflect SDK exceptions, input strings or paths to the parent.
        sys.exit(2)
