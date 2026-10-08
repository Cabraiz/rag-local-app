"""The booking policy as an ADK plugin of the App, its tools and thresholds given by the spec as kwargs. It is
the BookingCallbacks of runtime/callbacks.py, called by the agent's place in the pipeline: the root opens the
order and reports it, a later step fills what earlier ones left empty, and a pipeline that lists exams without
booking reviews the last answer. The transpiler checks a spec with its Config, check_spec and check_live."""
from google.adk.plugins.base_plugin import BasePlugin
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

from .callbacks import BookingCallbacks
from .confianca import BookingPolicy

PATH = 'runtime.plugin.BookingPlugin'  # how a spec names it in "plugins"
ROLES = ('read', 'search', 'book')
# What the runtime sends to the tool of each role (runtime/callbacks.py).
ROLE_PARAMETERS = {'read': ('filename',), 'search': ('query', 'top_k')}
MEASURED = BookingPolicy()  # each default is the measured value and also the floor


def rule(text):
    """A rule of this plugin, in the transpiler's `field: reason` messages."""
    return PydanticCustomError('spec_rule', text)


class BookingConfig(BaseModel):
    """The plugin's kwargs: which tool reads the order, which searches the catalog (both feed an exam's
    confidence) and which books, each as server.tool, and the policy (runtime/confianca.py). A spec may
    make the policy stricter, never looser (at 0.80 a misread "- GA" would book as IgA)."""
    model_config = ConfigDict(extra='forbid', strict=True)
    read: str | None = None
    search: str | None = None
    book: str | None = None
    min_confidence: float = Field(default=MEASURED.min_confidence, le=1)
    ask_from: float | None = Field(default=MEASURED.ask_from, le=1)  # None: no question, the middle band is left out
    ocr_floor_line: float = Field(default=MEASURED.ocr_floor_line, le=100)
    ocr_floor_short: float = Field(default=MEASURED.ocr_floor_short, le=100)
    ocr_floor_synonym: float = Field(default=MEASURED.ocr_floor_synonym, le=100)
    top_k: int = Field(default=MEASURED.top_k, ge=1, le=10)

    @field_validator('min_confidence', 'ask_from', 'ocr_floor_line', 'ocr_floor_short', 'ocr_floor_synonym')
    @classmethod
    def not_looser(cls, value, info: ValidationInfo):
        floor = getattr(MEASURED, str(info.field_name))
        if value is not None and value < floor:
            raise rule(f'deve ser no mínimo {floor:g}'.replace('.', ',') + ', o valor medido: uma spec pode deixar '
                       'a política mais rígida, nunca mais frouxa')
        earlier = info.data  # the fields above, when they were valid
        if info.field_name == 'ask_from' and value is not None and value >= earlier.get('min_confidence', 2):
            raise rule('deve ser menor que min_confidence (ou null, sem pergunta)')
        line, short = earlier.get('ocr_floor_line'), earlier.get('ocr_floor_short')
        if info.field_name == 'ocr_floor_synonym' and line is not None and short is not None and not line <= short <= value:
            raise rule('use ocr_floor_line <= ocr_floor_short <= ocr_floor_synonym (uma sigla pede leitura mais clara)')
        return value


class BookingPlugin(BasePlugin, BookingCallbacks):
    """BookingCallbacks as an App plugin. `servers` (server name -> address) comes from the spec's servers,
    so the kwargs name each tool as server.tool; the rest is BookingConfig, checked again here, so a
    hand-edited agent.py cannot loosen the policy either."""
    Config = BookingConfig

    def __init__(self, *, servers: dict[str, str] | None = None, **kwargs):
        config, servers = BookingConfig.model_validate(kwargs), servers or {}
        # role -> (server, tool), (None, None) for a role the spec leaves out
        read, search, book = (getattr(config, role).split('.') if getattr(config, role) else (None, None)
                              for role in ROLES)
        BasePlugin.__init__(self, name='booking')
        BookingCallbacks.__init__(
            self, ocr_tool=read[1], search_tool=search[1], booking_tool=book[1],
            policy=BookingPolicy(**{name: given for name, given in kwargs.items() if name not in ROLES}),  # as written
            ocr_url=servers.get(read[0] or ''), search_url=servers.get(search[0] or ''), servers=list(servers.values()))

    @staticmethod
    def of(app):
        """The BookingPlugin of a generated app (`cli run` opens the order on it)."""
        found = [plugin for plugin in getattr(app, 'plugins', None) or [] if isinstance(plugin, BookingPlugin)]
        if not found:
            raise ValueError(f'{getattr(app, "name", app)} não tem o BookingPlugin: gere de novo com o transpile')
        return found[0]

    async def before_agent_callback(self, *, agent, callback_context):
        if agent.parent_agent is None:
            return await self.start_order(callback_context)
        steps = agent.parent_agent.sub_agents
        earlier = [step.output_key for step in steps[:steps.index(agent)] if getattr(step, 'output_key', None)]
        return self.fill_missing(*earlier)(callback_context) if earlier else None

    async def after_agent_callback(self, *, agent, callback_context):
        if agent.parent_agent is None:
            return await self.report(callback_context)
        lists = self.booking_tool is None and self.search_tool is not None  # it searches and books nothing
        if lists and agent is agent.parent_agent.sub_agents[-1]:
            return self.review_list(agent.output_key)(callback_context)
        return None

    async def before_model_callback(self, *, callback_context, llm_request):
        return self.before_model(callback_context, llm_request)

    async def on_model_error_callback(self, *, callback_context, llm_request, error):
        return self.model_failed(callback_context, llm_request, error)

    async def before_tool_callback(self, *, tool, tool_args, tool_context):
        return await self.before_tool(tool, tool_args, tool_context)

    async def after_tool_callback(self, *, tool, tool_args, tool_context, result):
        return self.after_tool(tool, tool_args, tool_context, result)

    @classmethod
    def check_spec(cls, spec, config, where):
        """The roles on tools the agents use, of the kind the runtime reads, and booking only with the
        reading and the search that make an exam's confidence; then each agent's tools."""
        problems, refs = [], {role: getattr(config, role) for role in ROLES}
        used = {tool for agent in spec.agents for tool in agent.tools}
        if spec.workflow != 'SequentialAgent':
            problems.append('workflow: o BookingPlugin lê, busca e agenda em ordem: use SequentialAgent')
        for role, reference in refs.items():
            server = spec.servers.get(str(reference).split('.')[0])
            if reference is not None and reference not in used:
                problems.append(f'{where}.{role}: "{reference}" não está nas tools de nenhum agente')
            elif server is not None and server.mcp != (role != 'book'):
                kind = 'uma operação de uma API OpenAPI' if role == 'book' else 'uma ferramenta de um servidor MCP'
                problems.append(f'{where}.{role}: "{reference}" precisa ser {kind}')
        declared = [reference for reference in refs.values() if reference]
        if len(set(declared)) < len(declared):
            problems.append(f'{where}: cada papel usa uma ferramenta diferente')
        if refs['book'] and not (refs['read'] and refs['search']):
            problems.append(f'{where}.book: agendar pede read e search: a confiança de um exame vem da leitura do '
                            'pedido e da busca no catálogo')
        elif refs['search'] and not refs['read']:
            problems.append(f'{where}.search: a busca é medida contra o pedido lido: declare também read')
        done: set[str] = set()  # the tools of the earlier agents
        for index, agent in enumerate(spec.agents):
            problems += agent_problems(spec, refs, f'agents.{index}.tools', agent, done, where)
            done |= set(agent.tools)
        return problems

    @classmethod
    def check_live(cls, spec, config, live, where):
        """Each role's tool, as the server that answered describes it, takes what the runtime sends."""
        problems = []
        for role, parameters in ROLE_PARAMETERS.items():
            reference = getattr(config, role)
            server, tool = reference.split('.') if reference else (None, None)
            schema = (live.get(server) or {}).get(tool)
            missing = [name for name in parameters if schema is not None and name not in schema.get('properties', {})]
            if missing:
                problems.append(f'{where}.{role}: "{reference}" não recebe {" nem ".join(missing)}, que o runtime envia')
        return problems + book_problems(config.book, live, where)


def agent_problems(spec, refs, where, agent, done, plugin):
    """Every tool of an agent plays a role, an API operation only the book one (the only call checked
    before it goes out), and booking only once, after earlier agents read the order and searched the
    catalog. Tools the spec does not declare are the transpiler's own problems."""
    problems = []
    for tool in agent.tools:
        server = spec.servers.get(tool.split('.')[0])
        if server is None or tool not in spec.tools_of(tool.split('.')[0]):
            continue
        if not server.mcp and tool != refs['book']:
            problems.append(f'{where}: "{tool}" é uma operação de API e só a de {plugin}.book chega a um agente, '
                            f'porque só ela passa pela checagem dos códigos antes da chamada; declare-a em '
                            f'{plugin}.book ou tire-a do agente')
        elif tool not in refs.values():
            # The runtime lets through only the tools of the roles, each with its own check (the reading
            # gets this run's file only): a tool without a role would be refused when called.
            problems.append(f'{where}: "{tool}" não tem papel em {plugin} (read, search ou book); o runtime só deixa '
                            'passar as ferramentas dos papéis, cada uma conferida: declare o papel ou tire-a do agente')
    book, needed = refs['book'], [refs[role] for role in ('read', 'search') if refs[role]]
    if book in agent.tools and book in done:
        problems.append(f'{where}: {book} já está em outro agente: um pedido, um agendamento')
    elif book in agent.tools and not set(needed) <= done:
        problems.append(f'{where}: {book} só agenda códigos achados no catálogo: '
                        f'antes dele, agentes anteriores precisam usar {" e ".join(needed)}')
    return problems


def book_problems(reference, live, where):
    """The booking operation, as the API that answered describes it, takes what the runtime sends and
    nothing else: our Idempotency-Key and a body of exams, each with a code, and no other field (the
    runtime checks only the exams). An API that did not answer is checked again by `cli run`."""
    server, tool = reference.split('.') if reference else (None, None)
    operation = (live.get(server) or {}).get(tool)
    if not (isinstance(operation, dict) and 'method' in operation):
        return []
    problems, body = [], operation.get('body') or {}
    fields = body.get('properties') or {}
    item = (fields.get('exams') or {}).get('items') or {}
    if 'idempotency_key' not in operation.get('parameters', []):
        problems.append(f'{where}.book: "{reference}" não recebe Idempotency-Key, que o runtime envia para que '
                        'um POST repetido não agende duas vezes')
    needed = [name for name in operation.get('needed', []) if name != 'idempotency_key']
    if needed:  # the runtime sends only the exams and our key: such a call would always be refused
        problems.append(f'{where}.book: "{reference}" pede {", ".join(needed)} (no caminho ou obrigatório), que o '
                        'runtime não envia: ele manda só exams e a Idempotency-Key')
    if 'exams' not in fields or 'code' not in (item.get('properties') or {}):
        problems.append(f'{where}.book: "{reference}" não recebe um corpo com exams[].code, que o runtime confere')
    elif set(fields) != {'exams'} or body.get('additionalProperties') is not False:
        problems.append(f'{where}.book: "{reference}" aceita no corpo outros campos além de exams; o runtime só '
                        'confere os exames, então a API precisa recusar o resto (additionalProperties: false)')
    return problems


def roles_of(spec):
    """role -> server.tool reference of the spec's BookingPlugin (None for a role it leaves out), or {}
    when the spec has none: what `cli run` reads, searches and books with."""
    for plugin in spec.plugins:
        if plugin.path == PATH:
            return {role: plugin.kwargs.get(role) for role in ROLES}
    return {}
