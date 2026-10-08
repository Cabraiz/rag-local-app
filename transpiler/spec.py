"""Agent spec: the JSON a user writes, validated before any code is generated."""
import builtins
import importlib
import json
import keyword
import os
import re
import unicodedata
from typing import Literal
from urllib.parse import urlsplit

from google.adk.plugins.base_plugin import BasePlugin
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from runtime.pedido import RECORD_KEYS

IDENTIFIER = r'^[a-z][a-z0-9_]{0,39}$'
TOOL_REF = r'^[a-z][a-z0-9_]{0,39}\.[a-z][a-z0-9_]{0,39}$'
SSE_URL = r'^https?://[A-Za-z0-9.-]+(:\d+)?/sse$'
OPENAPI_URL = r'^https?://[A-Za-z0-9.-]+(:\d+)?/openapi\.json$'
MODEL = r'^gemini-[a-z0-9.-]{1,40}$'
PLUGIN_PATH = r'^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)*\.[A-Z][A-Za-z0-9]{0,39}$'  # module.Class
# The packages a spec may load an App plugin from: the project's runtime and ADK's own plugins. A spec
# never makes the transpiler import any other module; another package is added here, in code.
PLUGIN_PACKAGES = ('runtime', 'google.adk.plugins')
# Names of the generated module that a plugin class must not shadow.
GENERATED_NAMES = {'App', 'LiveOpenAPIToolset', 'LlmAgent', 'LoopAgent', 'McpToolset', 'ParallelAgent',
                   'ResumabilityConfig', 'SequentialAgent', 'SseConnectionParams'}
# The hosts a server URL may name, unless ALLOWED_HOSTS (comma separated; "host" for any port,
# "host:port" for one) says otherwise: by default, exactly the three compose services on their ports.
# A spec cannot point the agent at another host or port (SSRF: 169.254.169.254, the internal network,
# a local service such as localhost:2375); the deployment can, and tests that run servers on this
# machine set ALLOWED_HOSTS themselves.
DEFAULT_ALLOWED_HOSTS = 'ocr:8001,rag:8002,api:8000'
ALLOWED_HOST = re.compile(r'[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:\d{1,5})?')  # "host" or "host:port"
# Names an agent cannot have: each agent becomes a variable of the generated module.
RESERVED = {'app', 'user', 'gemini', 'guarded', 'root_agent', 'runtime'}


class TranspileError(Exception):
    """Spec problems, each one as 'field: reason' in Portuguese."""

    def __init__(self, problems):
        self.problems = problems
        super().__init__('\n'.join(problems))


class Strict(BaseModel):
    # strict: a value of another JSON type is refused, never converted ("0.9" is text, not a number).
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, strict=True)


class Server(Strict):
    """An MCP server over SSE (url + tools) or an HTTP API described by OpenAPI (openapi_url + operations)."""
    url: str | None = Field(default=None, pattern=SSE_URL, description='SSE endpoint of the MCP server')
    tools: list[str] | None = Field(default=None, min_length=1, description='The tools the agents may call')
    openapi_url: str | None = Field(default=None, pattern=OPENAPI_URL)
    operations: list[str] | None = Field(default=None, min_length=1, description='The operation_ids the agents may call')

    @field_validator('tools', 'operations')
    @classmethod
    def tool_names(cls, value):
        for name in value or []:
            if not re.fullmatch(IDENTIFIER, name):
                raise PydanticCustomError('tool_name', f'"{name}": use minúsculas, dígitos e _ (começando por letra)')
        if value and len(set(value)) < len(value):
            raise PydanticCustomError('tool_name', 'nome repetido')
        return value

    @model_validator(mode='after')
    def one_kind(self):
        mcp, openapi = (self.url, self.tools), (self.openapi_url, self.operations)
        if not ((all(mcp) and not any(openapi)) or (all(openapi) and not any(mcp))):
            raise PydanticCustomError('server_kind', 'declare url e tools (servidor MCP) ou openapi_url e '
                                                     'operations (API OpenAPI), um dos dois')
        return self

    @property
    def mcp(self):
        return self.url is not None

    @property
    def address(self):
        """(field, URL) of the server's address in the spec."""
        return ('url', self.url) if self.mcp else ('openapi_url', self.openapi_url)

    @property
    def names(self):
        return (self.tools if self.mcp else self.operations) or []


class Plugin(Strict):
    """An App plugin (ADK's BasePlugin): its class as module.Class and the keyword arguments it gets."""
    path: str = Field(pattern=PLUGIN_PATH)
    kwargs: dict[str, JsonValue] = {}

    @field_validator('kwargs')
    @classmethod
    def argument_names(cls, value):
        for name in value:  # each name becomes a keyword argument of the generated call
            if name == 'servers':
                raise PydanticCustomError('tool_name', '"servers": o transpile passa os servidores da spec ao plugin')
            if not re.fullmatch(IDENTIFIER, name) or keyword.iskeyword(name):
                raise PydanticCustomError('tool_name', f'"{name}": use minúsculas, dígitos e _ (começando por letra)')
        return value


class Agent(Strict):
    name: str = Field(pattern=IDENTIFIER)
    instruction: str = Field(min_length=10, max_length=2000)
    output_key: str = Field(pattern=IDENTIFIER)
    model: str | None = Field(default=None, pattern=MODEL)
    tools: list[str] = []

    @field_validator('instruction')
    @classmethod
    def printable(cls, value):
        # Line breaks and tabs are text; other control or invisible format
        # characters (NUL, ESC, zero-width, bidi overrides) can hide instructions.
        if any(unicodedata.category(c) in ('Cc', 'Cf') and c not in '\n\t' for c in value):
            raise PydanticCustomError('control_character', 'caractere de controle ou invisível não permitido')
        return value

    @field_validator('tools')
    @classmethod
    def references(cls, value):
        for tool in value:
            if not re.fullmatch(TOOL_REF, tool):
                raise PydanticCustomError('tool_ref', f'"{tool}": use servidor.ferramenta, ex.: ocr.extract_exam_text')
        return value


class AgentSpec(Strict):
    name: str = Field(pattern=IDENTIFIER)
    model: str = Field(pattern=MODEL)
    # Answers the same request when `model` is overloaded or out of quota (runtime.adk.gemini).
    fallback_model: str | None = Field(default=None, pattern=MODEL)
    servers: dict[str, Server] = Field(min_length=1, max_length=10)
    agents: list[Agent] = Field(min_length=1, max_length=10)
    # What runs the agents: in order, at the same time, in order up to max_iterations times, or
    # nothing (null: the one agent is the root).
    workflow: Literal['SequentialAgent', 'ParallelAgent', 'LoopAgent'] | None = 'SequentialAgent'
    max_iterations: int | None = Field(default=None, ge=1, le=10)
    plugins: list[Plugin] = Field(default=[], max_length=10)

    @field_validator('servers')
    @classmethod
    def server_names(cls, value):
        for name in value:
            if not re.fullmatch(IDENTIFIER, name):
                raise PydanticCustomError('server_name', f'"{name}": use minúsculas, dígitos e _ (começando por letra)')
        return value

    def tools_of(self, server):
        """The tools (or operations) declared for a server, as server.tool references."""
        declared = self.servers.get(server)
        return {f'{server}.{name}' for name in (declared.names if declared else [])}


REASONS = {
    'missing': 'campo obrigatório ausente',
    'extra_forbidden': 'campo não permitido',
    'string_type': 'deve ser texto',
    'list_type': 'deve ser uma lista',
    'model_type': 'deve ser um objeto JSON',
    'string_too_short': 'texto curto demais',
    'string_too_long': 'texto longo demais',
    'float_type': 'deve ser um número',
    'float_parsing': 'deve ser um número',
    'int_type': 'deve ser um número inteiro',
    'int_parsing': 'deve ser um número inteiro',
    'int_from_float': 'deve ser um número inteiro, sem parte decimal',
    'bool_type': 'deve ser true ou false',
    'bool_parsing': 'deve ser true ou false',
    'dict_type': 'deve ser um objeto JSON',
    'model_attributes_type': 'deve ser um objeto JSON',
    'finite_number': 'deve ser um número finito',
}
CUSTOM = ('tool_ref', 'control_character', 'tool_name', 'server_kind', 'server_name', 'spec_rule')
PATTERN_HINTS = {
    IDENTIFIER: 'use minúsculas, dígitos e _ (começando por letra)',
    SSE_URL: 'esperado http://host:porta/sse',
    OPENAPI_URL: 'esperado http://host:porta/openapi.json',
    MODEL: 'esperado gemini-<versão>',
    TOOL_REF: 'use servidor.ferramenta, ex.: ocr.extract_exam_text',
    PLUGIN_PATH: 'esperado modulo.Classe, ex.: runtime.plugin.BookingPlugin',
}


def describe(error, prefix=()):
    """'field: reason' in Portuguese; `prefix`: where the value is in the spec (a plugin's kwargs)."""
    field = '.'.join(str(part) for part in (*prefix, *error['loc'])) or '(raiz)'
    if error['type'] == 'string_pattern_mismatch':
        reason = 'formato inválido: ' + PATTERN_HINTS.get(error['ctx']['pattern'], error['ctx']['pattern'])
    elif error['type'] in ('greater_than_equal', 'less_than_equal'):
        bounds = {'greater_than_equal': 'no mínimo', 'less_than_equal': 'no máximo'}
        limit = next(iter(error['ctx'].values()))
        reason = f'deve ser {bounds[error["type"]]} {limit:g}'.replace('.', ',')
    elif error['type'] in ('too_short', 'too_long'):
        reason = 'lista vazia' if error['type'] == 'too_short' else 'itens demais (máximo 10)'
    elif error['type'] == 'literal_error':
        reason = f'use {error["ctx"]["expected"]}'
    elif error['type'] in CUSTOM:  # a rule of ours (or of a plugin's Config), already in Portuguese
        reason = error['msg']
    else:  # pydantic's own message is in English: an unlisted type is named, not quoted
        reason = REASONS.get(error['type'], f'valor inválido ({error["type"]})')
    if error['type'] in ('float_type', 'int_type', 'bool_type') and isinstance(error.get('input'), str):
        reason += ' (veio como texto: escreva sem aspas)'
    return f'{field}: {reason}'


def reject_constant(name):
    """NaN, Infinity and -Infinity, which Python's json accepts but JSON does not have."""
    raise TranspileError([f'JSON inválido: {name} não é um número JSON'])


def reject_duplicates(pairs):
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise TranspileError([f'{key}: chave duplicada no JSON' for key in duplicates])
    return dict(pairs)


def allowed_hosts():
    """(hosts, problems): ALLOWED_HOSTS, or DEFAULT_ALLOWED_HOSTS when it is unset or names no host
    (empty, blank, only commas). An entry that is not "host" or "host:port" is a problem, never ignored."""
    hosts = [host.strip().lower() for host in os.environ.get('ALLOWED_HOSTS', '').split(',') if host.strip()]
    if not hosts:
        hosts = DEFAULT_ALLOWED_HOSTS.split(',')
    problems = [f'ALLOWED_HOSTS: "{host}" não é host nem host:porta (ex.: ocr:8001,clinica.interna:8443)'
                for host in hosts if not ALLOWED_HOST.fullmatch(host)]
    return hosts, problems


def check_servers(spec) -> list[str]:
    """Every server is on an allowed host (and port, if the entry names one); no tool name is
    exposed by two servers, since an agent and its plugins know a tool by its name."""
    hosts, problems = allowed_hosts()
    owners: dict[str, list[str]] = {}  # tool -> the servers that declare it
    if problems:  # a broken allowlist: say so instead of judging the servers against it
        return problems
    for name, server in spec.servers.items():
        field, url = server.address
        parts = urlsplit(url)
        written = parts.netloc.rpartition(':')[2] if ':' in parts.netloc else ''  # the URL patterns allow digits only
        if written and not 1 <= int(written) <= 65535:  # port 0 would otherwise read as the default 80
            problems.append(f'servers.{name}.{field}: porta inválida ({written}); use de 1 a 65535')
            continue
        host = (parts.hostname or '').lower()
        port = int(written) if written else (443 if parts.scheme == 'https' else 80)
        if host not in hosts and f'{host}:{port}' not in hosts:
            problems.append(f'servers.{name}.{field}: host "{host}:{port}" fora de ALLOWED_HOSTS '
                            f'({", ".join(hosts)}); quem implanta pode incluí-lo em ALLOWED_HOSTS')
        for tool in server.names:
            owners.setdefault(tool, []).append(name)
    for tool, names in owners.items():
        if len(names) > 1:
            problems.append(f'servers: "{tool}" está em {" e ".join(names)}; um nome de ferramenta, um servidor')
    return problems


def check_workflow(spec):
    problems = []
    if spec.workflow is None and len(spec.agents) > 1:
        problems.append('workflow: null (sem workflow) pede um só agente, que é a raiz')
    if (spec.workflow == 'LoopAgent') != (spec.max_iterations is not None):
        problems.append('max_iterations: obrigatório com LoopAgent, e só com ele')
    return problems


def load_plugins(spec):
    """(class, checked kwargs, field) of each plugin of the spec that loads, and the problems of the others.
    A plugin class may declare `Config` (a pydantic model of its kwargs), checked here with the spec's
    messages, `check_spec(spec, config, field)` and `check_live(spec, config, live, field)`."""
    loaded, problems, names = [], [], set()
    for index, plugin in enumerate(spec.plugins):
        where, (module, _, name) = f'plugins.{index}', plugin.path.rpartition('.')
        if not any(module == package or module.startswith(package + '.') for package in PLUGIN_PACKAGES):
            problems.append(f'{where}.path: "{plugin.path}" fora dos pacotes de plugins ({", ".join(PLUGIN_PACKAGES)})')
            continue
        try:
            found = getattr(importlib.import_module(module), name, None)
        except ImportError:
            found = None
        if not (isinstance(found, type) and issubclass(found, BasePlugin)):
            problems.append(f'{where}.path: "{plugin.path}" não é uma classe de plugin do ADK (BasePlugin)')
            continue
        if name in names | GENERATED_NAMES:  # each plugin class is a name of the generated module
            problems.append(f'{where}.path: "{name}" já é usado (ou é reservado)')
        names.add(name)
        try:
            config = found.Config.model_validate(plugin.kwargs) if hasattr(found, 'Config') else plugin.kwargs
        except ValidationError as error:
            problems += [describe(item, (where, 'kwargs')) for item in error.errors()]
            continue
        loaded.append((found, config, f'{where}.kwargs'))
    return loaded, problems


def check_plugins(spec):
    loaded, problems = load_plugins(spec)
    return problems + [problem for plugin, config, where in loaded if hasattr(plugin, 'check_spec')
                       for problem in plugin.check_spec(spec, config, where)]


# Copy of ADK 2.10's _TEMPLATE_VAR_PATTERN (google/adk/flows/llm_flows/prompt/_instructions_utils.py),
# with a group around the name. That module is private, so it is copied, not imported.
ADK_PLACEHOLDER = re.compile(r'(?<![\$\{\\])\{+([^{}]*)\}+')
STATE_PREFIXES = ('app', 'user', 'temp')


def placeholder_problem(field, match, available):
    """Why ADK would fail on this {...} at run time, or None if it is fine or plain text.

    ADK fills a valid state name ({key}, {temp:key}, {key?}) or {artifact.name}; anything
    else ({"code": ...}) stays as text. Only {key} written by an earlier agent is allowed, with single
    braces: ADK has no escape, so {{key}} would be filled as {key} too.
    """
    name = match.group(1).strip()
    optional = name.endswith('?')
    name = name.removesuffix('?')
    prefix, _, rest = name.partition(':')
    if name.startswith('artifact.'):
        return f'{field}: {match.group(0)} lê um artefato do ADK; use só {{output_key}} de um agente anterior'
    if rest and prefix in STATE_PREFIXES and rest.isidentifier():
        return f'{field}: {match.group(0)} usa o prefixo de estado "{prefix}:"; use só {{output_key}} de um agente anterior'
    if not name.isidentifier():
        return None  # not a placeholder for ADK: stays as text
    if match.group(0).startswith('{{') or match.group(0).endswith('}}'):
        # ADK strips every brace around a valid name: {{key}} is the placeholder {key}, not the text {key}.
        return (f'{field}: {match.group(0)} não é texto literal: o ADK não tem escape para chaves e lê isso como '
                f'o placeholder {{{name}}}; para citar o nome, escreva-o sem chaves')
    if optional:
        return f'{field}: {match.group(0)} é opcional ("?") e não é aceito; use {{{name}}} de um agente anterior'
    if name not in available:
        return f'{field}: {{{name}}} não é saída de um agente anterior'
    return None


def check_agents(spec) -> list[str]:
    """Across agents: unique names and keys, placeholders of agents that ran before, tools of declared
    servers, declared there."""
    problems, names, keys = [], set(), set()
    available: list[str] = []  # output keys of the agents that ran before this one
    for index, agent in enumerate(spec.agents):
        where = f'agents.{index}'
        if (agent.name in names or agent.name in RESERVED | {spec.name} or keyword.iskeyword(agent.name)
                or hasattr(builtins, agent.name)):  # each agent is a variable of the generated module
            problems.append(f'{where}.name: "{agent.name}" já é usado (ou é reservado)')
        names.add(agent.name)
        for match in ADK_PLACEHOLDER.finditer(agent.instruction):
            problem = placeholder_problem(f'{where}.instruction', match, available)
            if problem:
                problems.append(problem)
        if agent.output_key in keys:
            problems.append(f'{where}.output_key: "{agent.output_key}" já é usado por outro agente')
        elif agent.output_key in RECORD_KEYS:  # the runtime's copy of the order goes to the session state
            problems.append(f'{where}.output_key: "{agent.output_key}" é reservado: o runtime usa essa chave do estado')
        keys.add(agent.output_key)
        if spec.workflow in ('SequentialAgent', 'LoopAgent'):  # in a ParallelAgent no agent runs before another
            available.append(agent.output_key)
        problems += tool_problems(spec, where, agent)
    return problems


def tool_problems(spec, where, agent):
    """An agent's tools: each of a declared server, and declared there."""
    problems = []
    for tool in agent.tools:
        server = tool.split('.')[0]
        if server not in spec.servers:
            problems.append(f'{where}.tools: "{tool}" usa o servidor "{server}", que não está em servers')
        elif tool not in spec.tools_of(server):
            problems.append(f'{where}.tools: "{tool}" não está declarada em servers.{server}')
    return problems


def parse_spec(text):
    """JSON text -> AgentSpec, or TranspileError listing every problem."""
    try:
        data = json.loads(text.removeprefix('﻿'), object_pairs_hook=reject_duplicates,  # a BOM is not JSON
                          parse_constant=reject_constant)
    except json.JSONDecodeError as error:
        raise TranspileError([f'JSON inválido (linha {error.lineno}, coluna {error.colno}): {error.msg}']) from None
    try:
        spec = AgentSpec.model_validate(data)
    except ValidationError as error:
        raise TranspileError([describe(item) for item in error.errors()]) from None
    problems = check_servers(spec) + check_agents(spec) + check_workflow(spec) + check_plugins(spec)
    if problems:
        raise TranspileError(problems)
    return spec
