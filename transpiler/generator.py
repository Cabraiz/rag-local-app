"""AgentSpec -> generated/agent.py (Google ADK), then compile and import it."""
import importlib.util
import os
import py_compile
import re
import tempfile
import textwrap
from pathlib import Path
from string import Template

from runtime import API_VERSION

from .live import check_live, live_tools
from .spec import TranspileError, parse_spec

TEMPLATE = Template((Path(__file__).parent / 'agent_template.py.tmpl').read_text(encoding='utf-8'))
WIDTH = 120  # the generated file keeps the repository's line length (pyproject.toml)
# Written next to an agent.py, so its folder is an ADK agent folder: `adk run <folder>` and `adk web
# <folder>` import it and run its `app`. runtime first: it silences ADK's [EXPERIMENTAL] notices.
PACKAGE = ('"""ADK agent folder: `adk run` and `adk web` load agent.py and run its `app`."""\n'
           'import runtime  # noqa: F401\n\nfrom . import agent  # noqa: F401\n')


def call(function, arguments, indent):
    """function(name=value, ...) on one line, or one argument per line when that is wider than WIDTH."""
    one = f'{function}({", ".join(f"{name}={value}" for name, value in arguments)})'
    if indent + len(one) + 1 <= WIDTH:  # + the comma after it
        return one
    inner = ' ' * (indent + 4)
    return f'{function}(\n' + ''.join(f'{inner}{name}={value},\n' for name, value in arguments) + ' ' * indent + ')'


def assigned(name, function, arguments):
    """`name = function(...)`, on one line when the whole line fits in WIDTH."""
    one = call(function, arguments, len(f'{name} = ') - 1)  # -1: no comma follows an assignment
    if '\n' not in one:
        return f'{name} = {one}'
    return f'{name} = {function}(\n' + ''.join(f'    {key}={value},\n' for key, value in arguments) + ')'


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


def render_agent(spec, index, agent) -> str:
    servers: dict[str, list[str]] = {}  # server -> its tools this agent uses, in the spec's order
    for reference in dict.fromkeys(agent.tools):  # a tool written twice is filtered once
        server, name = reference.split('.')
        servers.setdefault(server, []).append(name)
    earlier = [other.output_key for other in spec.agents[:index]]
    # A pipeline that searches but does not book ends in a list of exams: the last agent's answer,
    # checked by the same policy a booking would be (runtime/callbacks.py).
    calls_an_api = any(not spec.servers[reference.split('.')[0]].mcp for other in spec.agents for reference in other.tools)
    lists = index == len(spec.agents) - 1 and spec.tool_for('search') and not spec.tool_for('book') and not calls_an_api
    model = model_call(spec, agent.model) if agent.model else 'MODEL'
    instruction = f'    instruction=guarded({agent.instruction!r}),'
    lines = [f'{agent.name} = LlmAgent(', f'    name={agent.name!r},', f'    model={model},']
    lines += [instruction] if len(instruction) <= WIDTH else ['    instruction=guarded(', *literal(agent.instruction, 8),
                                                               '    ),']
    toolsets = [toolset(spec, server, names) for server, names in servers.items()]
    single = f'    tools=[{toolsets[0]}],' if len(toolsets) == 1 else ''
    if single and len(single) <= WIDTH and '\n' not in single:
        lines.append(single)
    elif toolsets:
        lines += ['    tools=[', *(f'        {item},' for item in toolsets), '    ],']
    if earlier:
        lines.append(f'    before_agent_callback=CALLBACKS.fill_missing({", ".join(map(repr, earlier))}),')
    if lists:
        lines.append(f'    after_agent_callback=CALLBACKS.review_list({agent.output_key!r}),')
    lines.append('    before_model_callback=CALLBACKS.before_model,')  # the model sees the image's token, never its name
    lines.append('    on_model_error_callback=CALLBACKS.model_failed,')  # one clear line, not a traceback
    if toolsets:  # an agent without tools makes no tool call to check
        lines += ['    before_tool_callback=CALLBACKS.before_tool,', '    after_tool_callback=CALLBACKS.after_tool,']
    lines += [f'    output_key={agent.output_key!r},', ')']
    return '\n' + '\n'.join(lines) + '\n'


def imports(spec):
    """(import lines, docstring lines): only what this spec's file uses."""
    kinds = {spec.servers[reference.split('.')[0]].mcp for agent in spec.agents for reference in agent.tools}
    lines = ['from google.adk.agents import LlmAgent, SequentialAgent', 'from google.adk.apps import App, ResumabilityConfig']
    if True in kinds:
        lines.append('from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams')
    names = ['BookingCallbacks', 'BookingPolicy', *(['LiveOpenAPIToolset'] if False in kinds else []),
             *(['McpToolset'] if True in kinds else []), 'gemini', 'guarded', 'require_api']
    lines += ['', f'from runtime import {", ".join(names)}']
    doc = ["- BookingCallbacks: ADK callbacks that start the order from the session or the user's message, check",
           '  in code every exam code the model proposes and write the final report;',
           '- BookingPolicy: the thresholds below, as a typed value;']
    if False in kinds:
        doc += ["- LiveOpenAPIToolset: ADK's OpenAPIToolset, built from an API's live /openapi.json on first use,",
                '  on a host that ALLOWED_HOSTS allows;']
    if True in kinds:
        doc.append("- McpToolset: ADK's McpToolset, on a host that ALLOWED_HOSTS allows (checked on import);")
    doc += ['- gemini, guarded: the model with retries (and the reserve model), and the fixed rule put before each instruction;',
            '- require_api: stops this file on a runtime with another interface.']
    return '\n'.join(lines), '\n'.join(doc)


def comment(text):
    """A comment block of the generated file."""
    return '\n'.join(f'# {line}' for line in textwrap.wrap(text, 96))


def policy_comments(spec):
    """The comments above POLICY and CALLBACKS, true for this spec: it books, it only lists, or neither."""
    books, searches, reads = spec.tool_for('book'), spec.tool_for('search'), spec.tool_for('read')
    asks = spec.booking.ask_from is not None
    floors = ('A line the OCR read (0-100) below its floor is never sure: ocr_floor_line, ocr_floor_short '
              '(3 letters or fewer), ocr_floor_synonym (an abbreviation of a longer name, such as "TGP"). '
              'top_k: results per search.')
    if books:
        bands = ('An exam at or above min_confidence is booked; '
                 + ('from ask_from up, only if the person says yes [s/N]; ' if asks else 'nobody is asked; ')
                 + 'below, it is left out and reported. ' + floors)
        last = ('The booking tool receives only codes a search returned, each on its own piece of the order, '
                'by the POLICY above.')
    elif searches:
        bands = ('This spec lists exams and books none. An exam at or above min_confidence is listed as sure; '
                 + ('from ask_from up, it is listed marked to check; ' if asks else '')
                 + 'below, it is left out and reported. ' + floors)
        last = ("The last agent's list keeps only codes a search returned, each on its own piece of the order, "
                'sorted by the POLICY above.')
    else:
        bands, last = 'This spec neither books nor lists exams: the thresholds below are not used.', ''
    steps = ['One set of callbacks for every step; each acts only on its own tool.',
             "The reading tool gets only this run's file." if reads else '',
             'Each search keeps its candidates.' if searches else '', last, 'The model only proposes.']
    return comment(bands), comment(' '.join(step for step in steps if step))


def server_urls(spec):
    """The callbacks' arguments with the servers' URLs: the MCP servers the runtime itself calls (the
    reader's image check, the catalog search of the whole order) and every server, whose addresses are
    checked when an order starts."""
    urls = []
    for role, argument in (('read', 'ocr_url'), ('search', 'search_url')):
        reference = spec.role_refs()[role]
        server = spec.servers[reference.split('.')[0]] if reference else None
        if server is not None and server.mcp:
            urls.append((argument, repr(server.url)))
    return [*urls, ('servers', repr([server.address[1] for server in spec.servers.values()]))]


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
    return comment(text)


def number(value):
    """A spec number as a literal: 75 rather than 75.0."""
    return repr(int(value)) if isinstance(value, float) and value.is_integer() else repr(value)


def render(spec, spec_file):
    # repr() turns every spec value into a Python literal, so no spec text can
    # become code; the spec patterns already limit names and URLs.
    booking = spec.booking
    policy_comment, callbacks_comment = policy_comments(spec)
    import_lines, runtime_doc = imports(spec)
    roles = [(f'{role}_tool', repr(spec.tool_for(key))) for role, key in (('ocr', 'read'), ('search', 'search'),
                                                                          ('booking', 'book')) if spec.tool_for(key)]
    return TEMPLATE.substitute(
        imports=import_lines, runtime_doc=runtime_doc,
        callbacks='CALLBACKS = ' + call('BookingCallbacks', [*roles, ('policy', 'POLICY'), *server_urls(spec)], 0),
        policy_comment=policy_comment, callbacks_comment=callbacks_comment,
        spec_file=re.sub(r'[^A-Za-z0-9._-]', '_', Path(spec_file).name),  # docstring text, not a literal
        model=model_call(spec, spec.model), model_comment=model_comment(spec), name=repr(spec.name),
        api_version=repr(API_VERSION),
        min_confidence=number(booking.min_confidence), ask_from=number(booking.ask_from), top_k=number(booking.top_k),
        ocr_floor_line=number(booking.ocr_floor.line), ocr_floor_short=number(booking.ocr_floor.short_code),
        ocr_floor_synonym=number(booking.ocr_floor.short_synonym),
        agents=''.join(render_agent(spec, index, agent) for index, agent in enumerate(spec.agents)),
        sub_agents=', '.join(agent.name for agent in spec.agents),
        app=assigned('app', 'App', [('name', repr(spec.name)), ('root_agent', 'root_agent'),
                                    ('resumability_config', 'ResumabilityConfig(is_resumable=True)')]),
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
    imports as an ADK pipeline. `checked`, if given, gets the servers that answered."""
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
