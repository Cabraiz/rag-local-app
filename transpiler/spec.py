"""Agent spec: the JSON a user writes, validated before any code is generated."""
import builtins
import json
import keyword
import os
import re
import unicodedata
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

IDENTIFIER = r'^[a-z][a-z0-9_]{0,39}$'
TOOL_REF = r'^[a-z][a-z0-9_]{0,39}\.[a-z][a-z0-9_]{0,39}$'
SSE_URL = r'^https?://[A-Za-z0-9.-]+(:\d+)?/sse$'
OPENAPI_URL = r'^https?://[A-Za-z0-9.-]+(:\d+)?/openapi\.json$'
MODEL = r'^gemini-[a-z0-9.-]{1,40}$'
# The hosts a server URL may name, unless ALLOWED_HOSTS (comma separated; "host" for any port,
# "host:port" for one) says otherwise: by default, exactly the three compose services on their ports.
# A spec cannot point the agent at another host or port (SSRF: 169.254.169.254, the internal network,
# a local service such as localhost:2375); the deployment can, and tests that run servers on this
# machine set ALLOWED_HOSTS themselves.
DEFAULT_ALLOWED_HOSTS = 'ocr:8001,rag:8002,api:8000'
ALLOWED_HOST = re.compile(r'[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:\d{1,5})?')  # "host" or "host:port"
# Names an agent cannot have: each agent becomes a variable of the generated module.
RESERVED = {'user', 'gemini', 'guarded', 'root_agent', 'runtime'}
# The roles of the booking policy (runtime/): which tool reads the order, which searches the
# catalog (both feed an exam's confidence) and which books. A spec without "roles" (the first
# spec format) gets these, each only if an agent uses it.
V1_ROLES = {'read': 'ocr.extract_exam_text', 'search': 'rag.search_exams', 'book': 'api.create_appointment'}


class TranspileError(Exception):
    """Spec problems, each one as 'field: reason' in Portuguese."""

    def __init__(self, problems):
        self.problems = problems
        super().__init__('\n'.join(problems))


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


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


class Roles(Strict):
    read: str | None = Field(default=None, pattern=TOOL_REF, description='reads the order (OCR): lines and readings')
    search: str | None = Field(default=None, pattern=TOOL_REF, description='searches the catalog: code, name, score')
    book: str | None = Field(default=None, pattern=TOOL_REF, description='books the exams (OpenAPI operation)')


class OcrFloor(Strict):
    line: float = Field(default=75, ge=0, le=100)
    short_code: float = Field(default=85, ge=0, le=100)
    short_synonym: float = Field(default=95, ge=0, le=100)


class Booking(Strict):
    """The booking policy (runtime/confianca.py); the defaults are the measured values. Below 0.80
    a match is too weak to book alone (a misread "- GA" taken as IgA scores 0.80)."""
    min_confidence: float = Field(default=0.90, ge=0.8, le=1)
    ask_from: float | None = Field(default=0.70, gt=0, le=1)  # null: no question, the middle band is left out
    ocr_floor: OcrFloor = OcrFloor()
    top_k: int = Field(default=3, ge=1, le=10)


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
    # Used by `cli run` only, for a second run when `model` stays unavailable after the retries.
    fallback_model: str | None = Field(default=None, pattern=MODEL)
    servers: dict[str, Server] = Field(min_length=1, max_length=10)
    roles: Roles | None = None  # None: the first spec format, with V1_ROLES
    booking: Booking = Booking()
    agents: list[Agent] = Field(min_length=1, max_length=10)

    @field_validator('servers')
    @classmethod
    def server_names(cls, value):
        for name in value:
            if not re.fullmatch(IDENTIFIER, name):
                raise PydanticCustomError('server_name', f'"{name}": use minúsculas, dígitos e _ (começando por letra)')
        return value

    def declared_roles(self):
        """role -> server.tool reference, as the spec declares it (V1_ROLES without "roles")."""
        return self.roles.model_dump() if self.roles is not None else dict(V1_ROLES)

    def role_refs(self):
        """role -> server.tool reference of the tool that plays it in this pipeline, or None."""
        used = {tool for agent in self.agents for tool in agent.tools}
        return {role: ref if ref in used else None for role, ref in self.declared_roles().items()}

    def tool_for(self, role):
        """The tool name that plays a role ('read', 'search' or 'book'), or None if none does."""
        reference = self.role_refs()[role]
        return reference.split('.')[1] if reference else None

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
    'dict_type': 'deve ser um objeto JSON',
}
PATTERN_HINTS = {
    IDENTIFIER: 'use minúsculas, dígitos e _ (começando por letra)',
    SSE_URL: 'esperado http://host:porta/sse',
    OPENAPI_URL: 'esperado http://host:porta/openapi.json',
    MODEL: 'esperado gemini-<versão>',
    TOOL_REF: 'use servidor.ferramenta, ex.: ocr.extract_exam_text',
}


def describe(error):
    field = '.'.join(str(part) for part in error['loc']) or '(raiz)'
    if error['type'] == 'string_pattern_mismatch':
        reason = 'formato inválido: ' + PATTERN_HINTS[error['ctx']['pattern']]
    elif error['type'] in ('greater_than', 'greater_than_equal', 'less_than_equal'):
        bounds = {'greater_than': 'maior que', 'greater_than_equal': 'no mínimo', 'less_than_equal': 'no máximo'}
        limit = next(iter(error['ctx'].values()))
        reason = f'deve ser {bounds[error["type"]]} {limit}'.replace('.', ',')
    elif error['type'] in ('too_short', 'too_long'):
        reason = 'lista vazia' if error['type'] == 'too_short' else 'itens demais (máximo 10)'
    elif error['type'] in ('tool_ref', 'control_character', 'tool_name', 'server_kind', 'server_name'):
        reason = error['msg']
    else:
        reason = REASONS.get(error['type'], error['msg'])
    return f'{field}: {reason}'


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


def check_servers(spec):
    """Every server is on an allowed host (and port, if the entry names one); no tool name is
    exposed by two servers, since the callbacks know a tool by its name."""
    (hosts, problems), owners = allowed_hosts(), {}
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


def check_roles(spec):
    """Each role on a tool an agent uses, of the kind the runtime reads, and booking only with
    the reading and the search that make an exam's confidence."""
    if spec.roles is None:  # the first spec format: V1_ROLES, checked by tool_problems as before
        return []
    problems, declared = [], spec.declared_roles()
    used = {tool for agent in spec.agents for tool in agent.tools}
    for role, reference in declared.items():
        if reference is None:
            continue
        server = spec.servers.get(reference.split('.')[0])
        if reference not in used:
            problems.append(f'roles.{role}: "{reference}" não está nas tools de nenhum agente')
        elif server is not None and server.mcp != (role != 'book'):
            kind = 'uma operação de uma API OpenAPI' if role == 'book' else 'uma ferramenta de um servidor MCP'
            problems.append(f'roles.{role}: "{reference}" precisa ser {kind}')
    references = [reference for reference in declared.values() if reference]
    if len(set(references)) < len(references):
        problems.append('roles: cada papel usa uma ferramenta diferente')
    if declared['book'] and not (declared['read'] and declared['search']):
        problems.append('roles.book: agendar pede roles.read e roles.search: a confiança de um exame vem da '
                        'leitura do pedido e da busca no catálogo')
    elif declared['search'] and not declared['read']:
        problems.append('roles.search: a busca é medida contra o pedido lido: declare também roles.read')
    return problems


def check_booking(spec):
    booking, floor = spec.booking, spec.booking.ocr_floor
    problems = []
    if booking.ask_from is not None and booking.ask_from >= booking.min_confidence:
        problems.append('booking.ask_from: deve ser menor que booking.min_confidence (ou null, sem pergunta)')
    if not floor.line <= floor.short_code <= floor.short_synonym:
        problems.append('booking.ocr_floor: use line <= short_code <= short_synonym (uma sigla pede leitura mais clara)')
    return problems


# Copy of ADK 2.10's _TEMPLATE_VAR_PATTERN (google/adk/flows/llm_flows/prompt/_instructions_utils.py),
# with a group around the name. That module is private, so it is copied, not imported.
ADK_PLACEHOLDER = re.compile(r'(?<![\$\{\\])\{+([^{}]*)\}+')
STATE_PREFIXES = ('app', 'user', 'temp')


def placeholder_problem(field, match, available):
    """Why ADK would fail on this {...} at run time, or None if it is fine or plain text.

    ADK fills a valid state name ({key}, {temp:key}, {key?}) or {artifact.name}; anything
    else ({"code": ...}) stays as text. Only {key} written by an earlier agent is allowed.
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
    if optional:
        return f'{field}: {match.group(0)} é opcional ("?") e não é aceito; use {{{name}}} de um agente anterior'
    if name not in available:
        return f'{field}: {{{name}}} não é saída de um agente anterior'
    return None


def check_agents(spec):
    """Across agents: unique names and keys, placeholders of earlier agents only, tools of
    declared servers, and booking only after the order was read and searched."""
    problems, available, names, done = [], [], set(), set()
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
        if agent.output_key in available:
            problems.append(f'{where}.output_key: "{agent.output_key}" já é usado por outro agente')
        available.append(agent.output_key)
        problems += tool_problems(spec, where, agent, done)
        done |= set(agent.tools)
    return problems


def tool_problems(spec, where, agent, done):
    """An agent's tools: each of a declared server and declared there, and booking only once, after
    earlier agents read the order and searched the catalog (`done`: the tools of earlier agents)."""
    problems, roles = [], spec.declared_roles()
    for tool in agent.tools:
        server = tool.split('.')[0]
        if server not in spec.servers:
            problems.append(f'{where}.tools: "{tool}" usa o servidor "{server}", que não está em servers')
        elif tool not in spec.tools_of(server):
            problems.append(f'{where}.tools: "{tool}" não está declarada em servers.{server}')
        elif not spec.servers[server].mcp and tool != roles['book']:
            # Fail closed: the runtime checks the codes only on the book role's call; any other API
            # operation would go out unchecked (a spec without "roles" names it api.create_appointment).
            problems.append(f'{where}.tools: "{tool}" é uma operação de API e só a de roles.book chega a um agente, '
                            'porque só ela passa pela checagem dos códigos antes da chamada; declare-a em roles.book '
                            'ou tire-a do agente')
        elif tool not in roles.values():
            # The runtime lets through only the tools of the roles, each with its own check (the reading
            # gets this run's file only): a tool without a role would be refused when called.
            problems.append(f'{where}.tools: "{tool}" não tem papel em roles (read, search ou book); o runtime só deixa '
                            'passar as ferramentas dos papéis, cada uma conferida: declare o papel ou tire-a do agente')
    book, needed = roles['book'], [roles[role] for role in ('read', 'search') if roles[role]]
    if book in agent.tools and book in done:
        problems.append(f'{where}.tools: {book} já está em outro agente: um pedido, um agendamento')
    elif book in agent.tools and not set(needed) <= done:
        problems.append(f'{where}.tools: {book} só agenda códigos achados no catálogo: '
                        f'antes dele, agentes anteriores precisam usar {" e ".join(needed)}')
    return problems


def parse_spec(text):
    """JSON text -> AgentSpec, or TranspileError listing every problem."""
    try:
        data = json.loads(text, object_pairs_hook=reject_duplicates)
    except json.JSONDecodeError as error:
        raise TranspileError([f'JSON inválido (linha {error.lineno}, coluna {error.colno}): {error.msg}']) from None
    try:
        spec = AgentSpec.model_validate(data)
    except ValidationError as error:
        raise TranspileError([describe(item) for item in error.errors()]) from None
    problems = check_servers(spec) + check_roles(spec) + check_booking(spec) + check_agents(spec)
    if problems:
        raise TranspileError(problems)
    return spec
