"""AgentSpec -> generated/agent.py (Google ADK), then compile and import it."""
import importlib.util
import inspect
import os
import py_compile
import re
import tempfile
import textwrap
from pathlib import Path
from string import Template

from runtime import API_VERSION

from .live import check_live, live_tools
from .spec import TranspileError, load_plugins, parse_spec

TEMPLATE = Template((Path(__file__).parent / 'agent_template.py.tmpl').read_text(encoding='utf-8'))
WIDTH = 120  # the generated file keeps the repository's line length (pyproject.toml)
# Written next to an agent.py, so its folder is an ADK agent folder: `adk run <folder>` and `adk web
# <folder>` import it and run its `app`. runtime first: it silences ADK's [EXPERIMENTAL] notices.
PACKAGE = ('"""ADK agent folder: `adk run` and `adk web` load agent.py and run its `app`."""\n'
           'import runtime  # noqa: F401\n\nfrom . import agent  # noqa: F401\n')
ABOUT = ('Declared here, from the spec, with Google ADK classes: the model, each LlmAgent (its tools, its instruction, '
         'its output_key), {root}, and the App that `adk run` and `adk web` load (resumable: a tool call that asks for '
         "a confirmation pauses and resumes). Imported from `runtime`, the transpiler's runtime library (interface "
         '{version}, tested on its own, the same for every spec):')
RUNTIME_DOC = {  # the docstring line of each name imported from runtime
    'LiveOpenAPIToolset': "- LiveOpenAPIToolset: ADK's OpenAPIToolset, built from an API's live /openapi.json on first use,\n"
                          '  on a host that ALLOWED_HOSTS allows;',
    'McpToolset': "- McpToolset: ADK's McpToolset, on a host that ALLOWED_HOSTS allows (checked on import);",
    'gemini': '- gemini, guarded: the model with retries (and the reserve model), and the fixed rule put before each '
              'instruction;',
    'require_api': '- require_api: stops this file on a runtime with another interface.',
}


def fill(text):
    """The text in lines of at most 100 columns, never breaking inside `backticks`."""
    return textwrap.fill(re.sub(r'`[^`]*`', lambda code: code[0].replace(' ', '\xa0'), text), 100).replace('\xa0', ' ')


def call(function, arguments, indent=0):
    """function(name=value, ...), one argument per line: the layout of every call of the file."""
    inner = ' ' * (indent + 4)
    return f'{function}(\n' + ''.join(f'{inner}{name}={value},\n' for name, value in arguments) + ' ' * indent + ')'


def listing(items, indent):
    """[item, ...], one item per line."""
    return '[\n' + ''.join(f'{" " * (indent + 4)}{item},\n' for item in items) + ' ' * indent + ']'


def literal(text, indent):
    """The text as Python string literals, one per line, each at most WIDTH columns wide; written
    next to each other they are the same string."""
    pieces, line = [], ''
    for token in re.findall(r'\s*\S+\s*', text) or [text]:
        if line and indent + len(repr(line + token)) > WIDTH:
            pieces.append(line)
            line = ''
        line += token
    pieces.append(line)
    if ''.join(pieces) != text:  # an explicit check: an assert would be gone under `python -O`
        raise TranspileError([f'instruction: o texto não pôde ser dividido em linhas sem mudar ({text[:60]!r}...)'])
    return [' ' * indent + repr(piece) for piece in pieces]


def toolset(spec, server, names):
    """The ADK toolset that exposes only these tools of one declared server."""
    declared = spec.servers[server]
    if not declared.mcp:
        url = declared.openapi_url
        return call('LiveOpenAPIToolset', [('openapi_url', repr(url)), ('base_url', repr(url.removesuffix('/openapi.json'))),
                                           ('tool_filter', repr(names))], 8)
    return call('McpToolset', [('connection_params', f'SseConnectionParams(url={declared.url!r})'),
                               ('tool_filter', repr(names))], 8)


def render_agent(spec, agent):
    servers: dict[str, list[str]] = {}  # server -> its tools this agent uses, in the spec's order
    for reference in dict.fromkeys(agent.tools):  # a tool written twice is filtered once
        server, name = reference.split('.')
        servers.setdefault(server, []).append(name)
    instruction = 'guarded(\n' + '\n'.join(literal(agent.instruction, 8)) + '\n    )'
    arguments = [('name', repr(agent.name)), ('model', model_call(spec, agent.model) if agent.model else 'MODEL'),
                 ('instruction', instruction)]
    if servers:
        arguments.append(('tools', listing([toolset(spec, server, names) for server, names in servers.items()], 4)))
    return f'\n{agent.name} = ' + call('LlmAgent', [*arguments, ('output_key', repr(agent.output_key))]) + '\n'


def render_root(spec):
    """root_agent: the workflow around the agents, or the one agent itself."""
    if spec.workflow is None:
        return f'root_agent = {spec.agents[0].name}'
    loop = [('max_iterations', repr(spec.max_iterations))] if spec.max_iterations else []
    sub_agents = '[' + ', '.join(agent.name for agent in spec.agents) + ']'
    return 'root_agent = ' + call(spec.workflow, [('name', repr(spec.name)), ('sub_agents', sub_agents), *loop])


def value(data, indent):
    """A spec value as a Python literal, a dict with one entry per line, like every call of the file."""
    if not (isinstance(data, dict) and data):
        return repr(data)
    return '{\n' + ''.join(f'{" " * (indent + 4)}{key!r}: {item!r},\n' for key, item in data.items()) + ' ' * indent + '}'


def render_plugin(spec, plugin, found):
    """One plugin of the App, with the spec's kwargs; one that takes `servers` also gets the spec's servers
    (name -> address), so its kwargs can name a tool as server.tool."""
    kwargs = dict(plugin.kwargs)
    if 'servers' in inspect.signature(found).parameters:
        kwargs = {'servers': {name: server.address[1] for name, server in spec.servers.items()}, **kwargs}
    return call(found.__name__, [(name, value(data, 12)) for name, data in kwargs.items()], 8)


def imports(spec, plugins):
    """(import lines, docstring lines): only what this spec's file uses."""
    kinds = {spec.servers[reference.split('.')[0]].mcp for agent in spec.agents for reference in agent.tools}
    adk = [f'from google.adk.agents import {", ".join(sorted({"LlmAgent", spec.workflow or "LlmAgent"}))}',
           'from google.adk.apps import App, ResumabilityConfig']
    if True in kinds:
        adk.append('from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams')
    names = [*(['LiveOpenAPIToolset'] if False in kinds else []), *(['McpToolset'] if True in kinds else []),
             'gemini', 'guarded', 'require_api']
    ours = [f'from runtime import {", ".join(names)}']
    for plugin, found in plugins:
        module = plugin.path.rpartition('.')[0]
        (adk if module.startswith('google.') else ours).append(f'from {module} import {found.__name__}')
    doc = [RUNTIME_DOC[name] for name in names if name in RUNTIME_DOC]
    if plugins:
        doc.append(fill("The App's plugins (ADK's BasePlugin), each with the kwargs the spec gives it: "
                        + ', '.join(f'{found.__name__} from {plugin.path.rpartition(".")[0]}' for plugin, found in plugins)
                        + '.'))
    return '\n'.join(sorted(adk)) + '\n\n' + '\n'.join(sorted(ours)), '\n'.join(doc)


def model_call(spec, model):
    """gemini(<model>), with the spec's reserve model when it has one."""
    return f'gemini({model!r}, fallback={spec.fallback_model!r})' if spec.fallback_model else f'gemini({model!r})'


def model_comment(spec):
    """The comment above MODEL, true for this spec: with or without a reserve model."""
    text = ('Gemini (GEMINI_MODEL in .env, or `-e GEMINI_MODEL=<model>` for one run, can replace it), retried on '
            '429/500/503 up to 5 times.')
    if spec.fallback_model:
        text = ('Gemini (GEMINI_MODEL in .env, or `-e GEMINI_MODEL=<model>` for one run, can replace it). A request '
                f'it refuses with 429 (quota) or 503 (overloaded) goes at once to the reserve model, {spec.fallback_model}, '
                'which retries 429/500/503 up to 5 times; the main model retries only a 500.')
    return '\n'.join(f'# {line}' for line in textwrap.wrap(text, 96))


def render(spec, spec_file):
    # repr() turns every spec value into a Python literal, so no spec text can
    # become code; the spec patterns already limit names, URLs and plugin paths.
    loaded = load_plugins(spec)[0]
    plugins = [(plugin, found) for plugin, (found, _, _) in zip(spec.plugins, loaded, strict=True)]
    import_lines, runtime_doc = imports(spec, plugins)
    app = [('name', repr(spec.name)), ('root_agent', 'root_agent')]
    if plugins:
        app.append(('plugins', listing([render_plugin(spec, plugin, found) for plugin, found in plugins], 4)))
    return TEMPLATE.substitute(
        imports=import_lines, runtime_doc=runtime_doc,
        about=fill(ABOUT.format(root=f'the {spec.workflow} that runs them' if spec.workflow else 'which is the root',
                                version=API_VERSION)),
        spec_file=re.sub(r'[^A-Za-z0-9._-]', '_', Path(spec_file).name),  # docstring text, not a literal
        model=model_call(spec, spec.model), model_comment=model_comment(spec), api_version=repr(API_VERSION),
        agents=''.join(render_agent(spec, agent) for agent in spec.agents), root=render_root(spec),
        app='app = ' + call('App', [*app, ('resumability_config', 'ResumabilityConfig(is_resumable=True)')]),
    )


def load_root_agent(path, shown=None, name='root_agent'):
    """Compile and import the generated file; return its root_agent (name='app': the App that `cli run`,
    `adk run` and `adk web` run). `shown`: the name an error gives the file (the final path, when `path`
    is a temporary file next to it)."""
    try:
        py_compile.compile(str(path), doraise=True)
        module_spec = importlib.util.spec_from_file_location('generated_agent', path)
        if module_spec is None or module_spec.loader is None:
            raise ImportError('não é um módulo Python')
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        return getattr(module, name)
    except Exception as error:  # e.g. ADK missing or another version: one clear line, no traceback
        raise TranspileError([f'{shown or path}: o código gerado não pôde ser importado '
                              f'({type(error).__name__}: {str(error)[:200]})']) from None


def write_checked(output, source):
    """Write the agent to `output` only once it imports: first to a temporary file in the same folder,
    then os.replace, atomic. If it does not import, the previous file stays as it was."""
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=f'.{output.stem}-', suffix='.py', dir=output.parent)
    temporary = Path(name)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as file:
            file.write(source)
        os.chmod(temporary, 0o644)  # mkstemp makes it 0600; agent.py stays readable as before
        root_agent = load_root_agent(temporary, shown=output)
        os.replace(temporary, output)
        return root_agent
    finally:
        temporary.unlink(missing_ok=True)  # already gone after os.replace
        Path(importlib.util.cache_from_source(str(temporary))).unlink(missing_ok=True)  # py_compile's .pyc


def load_spec(spec_path):  # file -> AgentSpec, read errors as TranspileError
    try:
        text = Path(spec_path).read_text(encoding='utf-8-sig')  # a BOM (Windows editors) is dropped
    except (OSError, UnicodeDecodeError) as error:
        raise TranspileError([f'{spec_path}: não foi possível ler ({error.__class__.__name__}); '
                              'a spec de exemplo é specs/agent.json']) from None
    return parse_spec(text)


def transpile(spec_path, output_path, checked=None):
    """Read the spec, check its tools on the servers that answer, write the agent, and prove it
    imports as an ADK agent. `checked`, if given, gets the servers that answered."""
    spec = load_spec(spec_path)
    live = live_tools(spec)
    problems = check_live(spec, live)
    if problems:
        raise TranspileError(problems)
    if checked is not None:
        checked.extend(name for name, tools in live.items() if tools is not None)
    output = Path(output_path)
    root_agent = write_checked(output, render(spec, spec_path))
    package = output.parent / '__init__.py'
    if output.name == 'agent.py':  # the folder ADK's tooling loads
        package.write_text(PACKAGE, encoding='utf-8')
    return root_agent
