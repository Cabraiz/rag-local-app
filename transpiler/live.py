"""Asks each declared server which tools it really has, when it answers: an MCP server its
list_tools, an API its /openapi.json. A server that does not answer (offline transpile, CI)
keeps the spec's declared list; `cli run` asks again before calling Gemini, and there every
server has to answer."""
import asyncio
import concurrent.futures
import re

import httpx

from runtime import rede

from .spec import load_plugins

SECONDS = 3  # per server, all at once


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
    schema, each property and the items of a list property resolved. `shared`: the parameters of the
    whole path."""
    content = ((operation.get('requestBody') or {}).get('content') or {}).get('application/json') or {}
    body = resolve(document, content.get('schema'))
    properties = {name: resolve(document, schema) for name, schema in (body.get('properties') or {}).items()}
    properties = {name: schema | ({'items': resolve(document, schema['items'])} if 'items' in schema else {})
                  for name, schema in properties.items()}
    parameters = [resolve(document, parameter) for parameter in [*(shared or []), *operation.get('parameters', [])]]
    named = [(re.sub(r'[^a-z0-9]+', '_', str(parameter.get('name', '')).lower()), parameter) for parameter in parameters]
    return {'method': method.upper(),
            'parameters': sorted(name for name, _ in named),
            'needed': sorted(name for name, parameter in named
                             if parameter.get('in') == 'path' or parameter.get('required') is True),
            'body': body | {'properties': properties}}


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
    """Every declared tool exists on the server that answered, and each plugin's check_live passes (the
    booking plugin: each role's tool takes what the runtime sends it). `required` (cli run, after each
    server answered a GET): a server that did not list its tools is a problem, not the declared list."""
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
    return problems + [problem for plugin, config, where in load_plugins(spec)[0] if hasattr(plugin, 'check_live')
                       for problem in plugin.check_live(spec, config, live, where)]
