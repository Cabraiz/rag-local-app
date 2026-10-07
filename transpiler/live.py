"""Asks each declared server which tools it really has, when it answers: an MCP server its
list_tools, an API its /openapi.json. A server that does not answer (offline transpile, CI)
keeps the spec's declared list; `cli run` asks again before calling Gemini, and there every
server has to answer."""
import asyncio
import concurrent.futures
import re

import httpx

from runtime import rede

SECONDS = 3  # per server, all at once
# What the runtime sends to (and reads from) the tool of each role (runtime/callbacks.py).
ROLE_PARAMETERS = {'read': ('filename',), 'search': ('query', 'top_k')}


async def mcp_tools(url):
    """tool name -> its input schema, from the MCP server's list_tools."""
    from mcp import ClientSession  # the MCP SDK comes with google-adk
    from mcp.client.sse import sse_client

    async with sse_client(url, timeout=SECONDS, sse_read_timeout=SECONDS) as streams, ClientSession(*streams) as session:
        await session.initialize()
        return {tool.name: tool.input_schema or {} for tool in (await session.list_tools()).tools}


async def api_operations(url):
    """operationId -> describe_operation() for every operation of the API's OpenAPI document."""
    async with httpx.AsyncClient(timeout=SECONDS) as client:
        response = await client.get(url)
        response.raise_for_status()
    return operations(response.json())


def operations(document):
    """operationId -> its method, parameter names and JSON body, from an OpenAPI document."""
    paths = document.get('paths', {}) if isinstance(document, dict) else {}
    return {operation['operationId']: describe_operation(document, method, operation, item.get('parameters'))
            for item in paths.values() if isinstance(item, dict)
            for method, operation in item.items() if isinstance(operation, dict) and 'operationId' in operation}


def resolve(document, schema):
    """A schema with its local $ref (#/components/...) followed, a few levels deep; {} if absent."""
    for _ in range(10):
        if not (isinstance(schema, dict) and isinstance(schema.get('$ref'), str)):
            break
        target = document
        for part in schema['$ref'].removeprefix('#/').split('/'):
            target = target.get(part, {}) if isinstance(target, dict) else {}
        schema = target
    return schema if isinstance(schema, dict) else {}


def describe_operation(document, method, operation, shared=None):
    """method (POST...), parameters (names as the ADK tool takes them: Idempotency-Key ->
    idempotency_key), needed (the path parameters and the required ones) and body: the JSON body's
    schema, with the items of `exams` resolved. `shared`: the parameters of the whole path."""
    content = ((operation.get('requestBody') or {}).get('content') or {}).get('application/json') or {}
    body = resolve(document, content.get('schema'))
    exams = resolve(document, (body.get('properties') or {}).get('exams'))
    parameters = [resolve(document, parameter) for parameter in [*(shared or []), *operation.get('parameters', [])]]
    named = [(re.sub(r'[^a-z0-9]+', '_', str(parameter.get('name', '')).lower()), parameter) for parameter in parameters]
    return {'method': method.upper(),
            'parameters': sorted(name for name, _ in named),
            'needed': sorted(name for name, parameter in named
                             if parameter.get('in') == 'path' or parameter.get('required') is True),
            'body': body | ({'exam_item': resolve(document, exams.get('items'))} if exams else {})}


async def ask_servers(spec):
    async def ask(name, server):
        try:
            listing = mcp_tools(server.url) if server.mcp else api_operations(server.openapi_url)
            return name, await asyncio.wait_for(listing, SECONDS * 2)
        except Exception:  # down, unknown host, not that kind of server: None (see check_live)
            return name, None
    return dict(await asyncio.gather(*(ask(name, server) for name, server in spec.servers.items())))


def live_tools(spec):
    """server -> {tool: input schema} as the server answers, or None for a server that did not answer."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(ask_servers(spec))
    # Called from inside an event loop, which asyncio.run cannot nest: ask from a thread with its own
    # loop. The caller's loop waits for it (at most SECONDS * 2), as a synchronous call would.
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, ask_servers(spec)).result()


def check_addresses(spec):
    """Resolve each server's name once, before any request of `cli run`. ALLOWED_HOSTS allows names;
    a name that resolves to a local or metadata address (DNS rebinding, an /etc/hosts entry) is a
    problem (runtime/rede.py). Only a host written as that address (an IP, or localhost), and so listed
    by itself in ALLOWED_HOSTS, may point there. Inside rede.pinned_names(), the run keeps the addresses
    checked here, and a name that did not resolve keeps none."""
    problems = []
    for name, server in spec.servers.items():
        field, url = server.address
        resolved = rede.resolve(url)
        if resolved is None:
            continue
        host, addresses = resolved
        refused = [address for address in addresses if rede.unsafe(address)]
        if refused:
            problems.append(f'servers.{name}.{field}: "{host}" resolve para {", ".join(refused)}, {rede.REFUSED}')
        elif rede.PINS is not None:
            rede.PINS.setdefault(host, addresses)
    return problems


def check_live(spec, live, required=False):
    """Every declared tool exists on the server that answered, and each role's tool takes what the
    runtime sends it. `required` (cli run, after each server answered a GET): a server that did not
    list its tools is a problem, not the declared list."""
    problems = []
    for name in [name for name, tools in live.items() if required and tools is None]:
        server = spec.servers[name]
        listing = 'ferramentas (é um servidor MCP?)' if server.mcp else 'operações (é um /openapi.json?)'
        problems.append(f'servers.{name}: {server.address[1]} respondeu, mas não listou as {listing}')
    for name, tools in live.items():
        server = spec.servers[name]
        for missing in [tool for tool in server.names if tools is not None and tool not in tools]:
            field = 'tools' if server.mcp else 'operations'
            problems.append(f'servers.{name}.{field}: "{missing}" não existe neste servidor '
                            f'(use {", ".join(sorted(tools)) or "nenhuma"})')
    for role, parameters in ROLE_PARAMETERS.items():
        reference = spec.role_refs()[role]
        server, tool = reference.split('.') if reference else (None, None)
        schema = (live.get(server) or {}).get(tool)
        missing = [name for name in parameters if schema is not None and name not in schema.get('properties', {})]
        if missing:
            problems.append(f'roles.{role}: "{reference}" não recebe {" nem ".join(missing)}, que o runtime envia')
    return problems + book_problems(spec, live)


def book_problems(spec, live):
    """The booking operation, as the API that answered describes it, takes what the runtime sends and
    nothing else: our Idempotency-Key and a body of exams, each with a code, and no other field (the
    runtime checks only the exams). An API that did not answer is checked again by `cli run`."""
    reference = spec.role_refs()['book']
    server, tool = reference.split('.') if reference else (None, None)
    operation = (live.get(server) or {}).get(tool)
    if not (isinstance(operation, dict) and 'method' in operation):
        return []
    problems, body = [], operation.get('body') or {}
    fields, item = set(body.get('properties') or {}), body.get('exam_item') or {}
    if 'idempotency_key' not in operation.get('parameters', []):
        problems.append(f'roles.book: "{reference}" não recebe Idempotency-Key, que o runtime envia para que '
                        'um POST repetido não agende duas vezes')
    needed = [name for name in operation.get('needed', []) if name != 'idempotency_key']
    if needed:  # the runtime sends only the exams and our key: such a call would always be refused
        problems.append(f'roles.book: "{reference}" pede {", ".join(needed)} (no caminho ou obrigatório), que o '
                        'runtime não envia: ele manda só exams e a Idempotency-Key')
    if 'exams' not in fields or 'code' not in (item.get('properties') or {}):
        problems.append(f'roles.book: "{reference}" não recebe um corpo com exams[].code, que o runtime confere')
    elif fields != {'exams'} or body.get('additionalProperties') is not False:
        problems.append(f'roles.book: "{reference}" aceita no corpo outros campos além de exams; o runtime só confere '
                        'os exames, então a API precisa recusar o resto (additionalProperties: false)')
    return problems
