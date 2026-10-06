"""Call one MCP tool over SSE, the way the agent does, and print what travels.

Runs inside the agent container (the image includes this folder):
    docker compose run --rm agent python exemplos/mcp_call.py ocr extract_exam_text filename=pedido.png
    docker compose run --rm agent python exemplos/mcp_call.py rag search_exams query=Glicose top_k=3
    docker compose run --rm agent python exemplos/mcp_call.py rag health

Arguments are key=value pairs (the same in PowerShell 5.1 and Bash); a single JSON
object ('{"filename": "pedido.png"}') is still accepted. Numbers and true/false become
JSON values; anything else is text. Problems end in one "Erro: ..." line, exit code 2.
"""
import asyncio
import json
import sys
import urllib.request

from mcp import ClientSession
from mcp.client.sse import sse_client

SERVERS = {'ocr': 'http://ocr:8001', 'rag': 'http://rag:8002'}


def handshake(base):
    with urllib.request.urlopen(base + '/sse', timeout=5) as response:
        event, data = response.readline().decode().strip(), response.readline().decode().strip()
        print(f'GET {base}/sse -> {response.status} {response.headers["content-type"]}')
        print(f'  {event}\n  {data}')


async def call(base, tool, arguments):
    async with sse_client(base + '/sse') as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
    if result.is_error:
        print('erro:', ' '.join(item.text for item in result.content if hasattr(item, 'text')))
        return 1
    value = result.structured_content
    if value is None:  # no typed result: one JSON text per content item (as cli.tool_result)
        items = [json.loads(item.text) for item in result.content if getattr(item, 'type', '') == 'text']
        value = items[0] if len(items) == 1 else items
    if isinstance(value, dict) and set(value) == {'result'}:
        value = value['result']
    print(f'call_tool {tool}({json.dumps(arguments, ensure_ascii=False)}) ->')
    print(json.dumps(value, indent=2, ensure_ascii=False))
    return 0


USAGE = 'uso: mcp_call.py ocr|rag <ferramenta|health> [chave=valor ...]'


class UsageError(Exception):
    """A problem with the command line; shown as one line."""


def parse_arguments(args):
    """key=value pairs, or one JSON object, into the tool's arguments."""
    if len(args) == 1 and args[0].lstrip().startswith('{'):
        try:
            value = json.loads(args[0])
        except json.JSONDecodeError:
            raise UsageError('JSON inválido nos argumentos; use chave=valor, ex.: filename=pedido.png') from None
        if not isinstance(value, dict):
            raise UsageError('os argumentos devem ser um objeto JSON ou pares chave=valor')
        return value
    arguments = {}
    for arg in args:
        key, sep, raw = arg.partition('=')
        if not sep or not key:
            raise UsageError(f'argumento "{arg}" fora do formato chave=valor (ex.: filename=pedido.png)')
        try:
            value = json.loads(raw)
            arguments[key] = value if isinstance(value, (int, float, bool)) or value is None else raw
        except json.JSONDecodeError:
            arguments[key] = raw
    return arguments


def main(argv):
    if len(argv) < 2:
        raise UsageError(USAGE)
    server, tool, rest = argv[0], argv[1], argv[2:]
    if server not in SERVERS:
        raise UsageError(f'servidor "{server}" desconhecido; use ocr ou rag')
    base = SERVERS[server]
    if tool == 'health':
        with urllib.request.urlopen(base + '/health', timeout=5) as response:
            print(f'GET {base}/health ->', response.read().decode())
        return 0
    arguments = parse_arguments(rest)
    handshake(base)
    return asyncio.run(call(base, tool, arguments))


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except UsageError as error:
        print(f'Erro: {error}', file=sys.stderr)
    except OSError as error:  # connection refused, DNS, timeout: the stack is not up
        print(f'Erro: servidor MCP fora do ar ({type(error).__name__}); suba com docker compose up -d --wait',
              file=sys.stderr)
    except Exception as error:  # an unexpected failure is still one line, not a traceback
        print(f'Erro: a chamada MCP falhou ({type(error).__name__}: {str(error)[:200]})', file=sys.stderr)
    sys.exit(2)
