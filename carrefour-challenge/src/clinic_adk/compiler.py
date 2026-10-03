"""Small typed DSL -> validated IR -> deterministic Google ADK Python emitter."""
import ast
import builtins
import hashlib
import json
import keyword
import re
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from typing import Literal
from .errors import SafeError
from .validation import issues

KINDS = ('ocr', 'retrieve', 'validate', 'schedule', 'format')
IDENTIFIER_PATTERN = r'[a-z][a-z0-9_]{0,47}'
RESERVED_NAMES = frozenset(name for name in
    (set(dir(builtins)) | set(keyword.kwlist) | set(keyword.softkwlist) |
     {'runtime', 'node_input', 'build_agent', 'root_agent', 'start', 'end', 'workflow', 'spec_sha256'})
    if re.fullmatch(IDENTIFIER_PATTERN, name))
class Stage(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=48, pattern='^' + IDENTIFIER_PATTERN + '$',
                      json_schema_extra={'not': {'enum': sorted(RESERVED_NAMES)}})
    kind: Literal['ocr', 'retrieve', 'validate', 'schedule', 'format']
    @field_validator('name')
    @classmethod
    def identifier(cls, value):
        if not re.fullmatch(IDENTIFIER_PATTERN, value) or value in RESERVED_NAMES:
            raise ValueError('invalid stage identifier')
        return value

class AgentSpec(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: Literal[1]
    name: str = Field(min_length=1, max_length=48, pattern='^' + IDENTIFIER_PATTERN + '$',
                      json_schema_extra={'not': {'enum': sorted(RESERVED_NAMES)}})
    framework: Literal['google-adk']
    transport: Literal['sse']
    model_mode: Literal['offline']
    timeout_seconds: int = Field(ge=5, le=60)
    stages: list[Stage] = Field(min_length=5, max_length=5)
    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError('schema_version must be integer 1')
        return value
    @field_validator('name')
    @classmethod
    def identifier(cls, value):
        return Stage.identifier(value)
    @model_validator(mode='after')
    def ordered(self):
        if tuple(stage.kind for stage in self.stages) != KINDS:
            raise ValueError('mandatory order: ocr, retrieve, validate, schedule, format')
        if len({stage.name for stage in self.stages}) != 5:
            raise ValueError('duplicate stage')
        return self

def parse_spec(raw):
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 16384:
        raise SafeError('SPEC_SIZE_LIMIT')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SafeError('SPEC_DUPLICATE_KEY')
            result[key] = value
        return result
    try:
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(SafeError('SPEC_INVALID_NUMBER')))
        return AgentSpec.model_validate(data)
    except ValidationError as error:
        # Field/type only: never echo an arbitrary input value.
        diagnostics = issues(error.errors(include_input=False, include_context=False))
        raise SafeError('SPEC_VALIDATION:' + json.dumps(diagnostics, separators=(',', ':'))) from None
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, SafeError):
            raise
        raise SafeError('SPEC_INVALID_JSON') from None

def emit(spec):
    # Pydantic models are mutable, and model_construct deliberately skips validation.
    # Never let an unchecked IR reach Python interpolation, even through internal APIs.
    if type(spec) is not AgentSpec:
        raise SafeError('SPEC_INVALID_IR')
    try:
        raw = json.dumps(spec.model_dump(mode='json', warnings=False), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, RecursionError):
        raise SafeError('SPEC_INVALID_IR') from None
    spec = parse_spec(raw)
    canonical = json.dumps(spec.model_dump(), sort_keys=True, separators=(',', ':')).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    lines = ['# Generated deterministically by clinic_adk.compiler; do not edit.',
             'from google.adk import Workflow', 'from google.adk.workflow import START',
             'from clinic_adk.runtime import Runtime', '', f'SPEC_SHA256 = {digest!r}', '',
             'def build_agent(runtime):']
    for stage in spec.stages:
        lines += [f'    async def {stage.name}(node_input):',
                  f'        return await runtime.step({stage.kind!r}, node_input)', '']
    chain = ', '.join(['START', *[s.name for s in spec.stages]])
    lines += [f'    return Workflow(name={spec.name!r}, edges=[({chain})], max_concurrency=1, timeout={spec.timeout_seconds})',
              '', 'root_agent = build_agent(Runtime())', '']
    source = '\n'.join(lines)
    ast.parse(source)
    compile(source, '<generated-adk-agent>', 'exec')
    return source
