import argparse
import asyncio
import hashlib
from types import ModuleType
import json
from pathlib import Path
import sys
from uuid import UUID, uuid4
from .compiler import parse_spec, emit
from .errors import SafeError
from .safe_logging import setup
from .file_input import bounded_file, atomic_artifact

class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default prints attacker-controlled arguments to stderr.
        raise SafeError('CLI_INVALID_ARGUMENTS')

def read_spec(path):
    candidate = Path(path).resolve()
    root = Path('/app/examples').resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file() or candidate.stat().st_size > 16384:
        raise SafeError('SPEC_PATH_DENIED')
    return parse_spec(bounded_file(candidate, 16384, 'SPEC_PATH_DENIED'))

def artifact_path(name):
    if not isinstance(name, str) or '/' in name or '\\' in name or not name.endswith('.py') or len(name) > 80:
        raise SafeError('ARTIFACT_NAME_DENIED')
    candidate = (Path('/artifacts') / name).resolve()
    if not candidate.is_relative_to(Path('/artifacts').resolve()):
        raise SafeError('ARTIFACT_PATH_DENIED')
    return candidate

async def execute(spec, source, image_ref, request_id):
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    from .runtime import Runtime
    from google.adk import Workflow
    expected = emit(spec).encode()
    raw = bounded_file(source, len(expected), 'GENERATED_ARTIFACT_CHANGED')
    if raw != expected:
        raise SafeError('GENERATED_ARTIFACT_CHANGED')
    module = ModuleType('generated_clinic_agent')
    module.__file__ = str(source)
    # Execute these exact verified bytes, never reread a mutable artifact path.
    exec(compile(raw, str(source), 'exec'), module.__dict__)
    runtime = Runtime(image_ref=image_ref, request_id=request_id)
    agent = module.build_agent(runtime)
    if not isinstance(agent, Workflow) or not isinstance(module.root_agent, Workflow):
        raise SafeError('GENERATED_AGENT_NOT_ADK')
    sessions = InMemorySessionService()
    await sessions.create_session(app_name='clinic_lab', user_id='fictional_demo', session_id=runtime.request_id)
    runner = Runner(app_name='clinic_lab', node=agent, session_service=sessions)
    result = None
    try:
        async with asyncio.timeout(spec.timeout_seconds):
            async for event in runner.run_async(user_id='fictional_demo', session_id=runtime.request_id,
                    new_message=types.Content(role='user', parts=[types.Part(text='Processar pedido ficticio local autorizado.')])):
                output = getattr(event, 'output', None)
                if isinstance(output, dict) and 'result' in output:
                    result = output['result']
    except TimeoutError:
        # The workflow deadline can expire after the API committed but before the
        # reply arrived. Preserve the same reconciliation contract as HTTP timeouts.
        code = ('APPOINTMENT_OUTCOME_UNKNOWN_RETRY_SAME_KEY' if 'schedule' in runtime.stages
                else 'WORKFLOW_TIMEOUT_BEFORE_APPOINTMENT')
        raise SafeError(code) from None
    if result is None or runtime.stages != ['ocr', 'retrieve', 'validate', 'schedule', 'format']:
        raise SafeError('ADK_INCOMPLETE_RESULT')
    result['generated_source_sha256'] = hashlib.sha256(raw).hexdigest()
    return result

def safe_failure_code(error):
    # SDKs can wrap a typed application failure; never serialize their messages.
    pending, seen = [error], set()
    for _ in range(16):
        if not pending:
            break
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, SafeError):
            return current.code
        pending.extend(e for e in (current.__cause__, current.__context__) if e is not None)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions[:8])
    return 'WORKFLOW_FAILED_SAFE'

def main():
    setup()
    parser = SafeArgumentParser(description='Transpilador JSON -> Google ADK; clinica inteiramente ficticia.')
    commands = parser.add_subparsers(dest='command', required=True)
    transpile = commands.add_parser('transpile')
    transpile.add_argument('--spec', default='/app/examples/agent.json')
    transpile.add_argument('--output', default='agent.py')
    run = commands.add_parser('run')
    run.add_argument('--spec', default='/app/examples/agent.json')
    run.add_argument('--agent', default='agent.py')
    run.add_argument('--image', default='request.png')
    run.add_argument('--request-id')
    request_id = None
    try:
        args = parser.parse_args()
        if args.command == 'run':
            candidate = args.request_id or str(uuid4())
            try:
                if str(UUID(candidate)) != candidate:
                    raise ValueError()
            except (TypeError, ValueError):
                raise SafeError('INVALID_REQUEST_ID') from None
            request_id = candidate
        spec = read_spec(args.spec)
        if args.command == 'transpile':
            source = emit(spec).encode()
            path = artifact_path(args.output)
            # Fixed artifact root only; never write to a caller-supplied directory.
            atomic_artifact(path, source)
            print(json.dumps({'ok': True, 'generated': path.name, 'sha256': hashlib.sha256(source).hexdigest()}))
        else:
            result = asyncio.run(execute(spec, artifact_path(args.agent), args.image, request_id))
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except SafeError as error:
        print(json.dumps({'ok': False, 'error': error.code, 'request_id': request_id}), file=sys.stderr)
        return 2
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        print(json.dumps({'ok': False, 'error': safe_failure_code(error), 'request_id': request_id}), file=sys.stderr)
        return 2

if __name__ == '__main__':
    sys.exit(main())
