"""Fictional exam scheduling API (FastAPI + SQLite).

Routes: POST /appointments, GET /appointments/{id}, GET /health.
Swagger UI is served at /docs; the agent reads the same contract from /openapi.json.
Errors never echo the submitted values or a stack trace, and SQL is always parameterized.
The exam list (health data) is stored encrypted with AES-256-GCM (api/crypto.py).
POST takes an optional Idempotency-Key, stored only as an HMAC, for API_IDEMPOTENCY_TTL_HOURS. Each request
logs one JSON line (no body), counts in a per-IP limit (API_RATE_LIMIT_PER_MINUTE; over it, 429), needs a Host in
API_ALLOWED_HOSTS (else 400) and gets the security headers of SecurityHeaders (nosniff, DENY, no-referrer, ...).
Importing this module reads no setting and touches no file: the catalog, the key and the
database are opened when the server starts (lifespan), and an invalid key stops it there.
"""
import hashlib
import hmac
import json
import logging
import math
import os
import re
import sqlite3
import sys
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.crypto import KEY_VARIABLE, CryptoError, decrypt, encrypt, key_bytes, load_key, resolve_key

# Defaults of DB_PATH, DB_KEY_FILE and EXAMS_PATH, which are read when the server starts (lifespan).
DEFAULT_DB_PATH = '/state/appointments.db'
DEFAULT_KEY_FILE = '/keys/db.key'  # the key created on the first start when DB_ENCRYPTION_KEY is empty
DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / 'data' / 'exams.json'
MAX_BODY_BYTES = 16_384  # 20 exams fit in a few KB
# Per client IP. The load test (500 orders, a POST and a GET each, from one container) stays
# well below the default (1200); 0 turns the limit off.
RATE_LIMIT_VARIABLE = 'API_RATE_LIMIT_PER_MINUTE'
TTL_VARIABLE = 'API_IDEMPOTENCY_TTL_HOURS'  # default 24; after it a key is forgotten and books anew
# Host names the API answers, on any port (comma separated): the published loopback port and the
# compose service. A page whose name a DNS rebinding points at 127.0.0.1 sends its own name: 400.
HOSTS_VARIABLE = 'API_ALLOWED_HOSTS'
DEFAULT_HOSTS = '127.0.0.1,localhost,api'
now = time.time  # the clock of the Idempotency-Key expiry


class ConfigError(Exception):
    """A setting the API cannot start with; a fixed message, reported in one line (see StartupError)."""


def setting(variable: str, default: int, minimum: int, meaning: str) -> int:
    """An integer setting, or `default` when it is unset or empty."""
    value = os.environ.get(variable, '').strip() or str(default)
    if not value.isascii() or not value.isdigit() or int(value) < minimum:
        raise ConfigError(f'{variable} inválido: {meaning}.')
    return int(value)


def allowed_hosts() -> frozenset[str]:
    hosts = [host.strip().lower() for host in os.environ.get(HOSTS_VARIABLE, '').split(',') if host.strip()]
    for host in hosts:
        if not re.fullmatch(r'[a-z0-9.-]+|\[[0-9a-f:.]+\]', host):
            raise ConfigError(f'{HOSTS_VARIABLE} inválido: "{host}" não é um nome de host (ex.: {DEFAULT_HOSTS}).')
    return frozenset(hosts or DEFAULT_HOSTS.split(','))


def fingerprints(database_key: bytes, key: str, codes: list[str]) -> tuple[str, ...]:
    """What idempotency stores: HMACs, under keys derived from the database key, of the Idempotency-Key
    (a client may put personal data in it) and of the set of codes, all that defines a booking (the
    stored names are the catalog's; a plain hash of a short code list could be reversed by trying)."""
    return tuple(hmac.new(hmac.new(database_key, label, hashlib.sha256).digest(), text.encode(), hashlib.sha256)
                 .hexdigest() for label, text in ((b'idempotency-key', key), (b'idempotency-body', ' '.join(sorted(codes)))))


class RateLimiter:
    """Token bucket per client: `per_minute` at once, refilled at per_minute/60 a second; thread-safe. Past
    MAX_CLIENTS buckets, the least recently seen client is forgotten (its next request starts full)."""

    MAX_CLIENTS = 10_000

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic):
        self.capacity, self.rate, self.clock = float(per_minute), per_minute / 60, clock
        self.buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()  # client -> (tokens left, when)
        self.lock = threading.Lock()

    def take(self, client: str) -> int:
        """0 if this request may go on; otherwise the seconds until the next one may."""
        with self.lock:
            now = self.clock()
            tokens, when = self.buckets.get(client, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - when) * self.rate)
            allowed = tokens >= 1
            self.buckets[client] = (tokens - 1 if allowed else tokens, now)
            self.buckets.move_to_end(client)
            while len(self.buckets) > self.MAX_CLIENTS:
                self.buckets.popitem(last=False)
            return 0 if allowed else max(1, math.ceil((1 - tokens) / self.rate))


def row_fields(appointment_id: str, status: str, created_at: str) -> str:
    """The clear columns of a row, bound to its encrypted exam list (editing any of them is detected)."""
    return json.dumps([appointment_id, status, created_at])


# One writer at a time inside this process; SQLite's busy timeout covers the rest.
WRITE_LOCK = threading.Lock()


def connect(db_path: Path) -> sqlite3.Connection:
    """One connection per request; waits up to 10 s for a lock instead of failing."""
    connection = sqlite3.connect(db_path, timeout=10)
    connection.execute('PRAGMA busy_timeout = 10000')
    # With WAL, NORMAL syncs at checkpoints instead of every commit: a crash of the API
    # loses nothing; only an OS power loss could undo the most recent commits.
    connection.execute('PRAGMA synchronous = NORMAL')
    return connection


def init_db(db_path: Path, database_key: bytes, cipher: AESGCM) -> sqlite3.Connection:
    """The tables; the clear keys of earlier versions (table `idempotency`) become HMACs with their appointment's
    codes and time, then are erased. All or nothing: another database key raises CryptoError, and the API stops."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(db_path)) as connection, connection:
        connection.execute('PRAGMA journal_mode = WAL')  # readers never block the writer
        connection.execute('PRAGMA secure_delete = ON')
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('CREATE TABLE IF NOT EXISTS appointments '
                           '(id TEXT PRIMARY KEY, status TEXT, exams TEXT, created_at TEXT)')
        connection.execute('CREATE TABLE IF NOT EXISTS idempotency_keys (key_hash TEXT PRIMARY KEY, '
                           'request_hash TEXT NOT NULL, appointment_id TEXT NOT NULL, created_at INTEGER NOT NULL)')
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'idempotency'").fetchone():
            for key, *row in connection.execute('SELECT key, id, status, created_at, exams FROM idempotency '
                                                'JOIN appointments ON id = appointment_id').fetchall():
                codes = [exam['code'] for exam in json.loads(decrypt(cipher, row[3], row_fields(*row[:3])))]
                connection.execute('INSERT OR IGNORE INTO idempotency_keys VALUES (?, ?, ?, ?)', (
                    *fingerprints(database_key, key, codes), row[0], int(datetime.fromisoformat(row[2]).timestamp())))
            connection.execute('DROP TABLE idempotency')
    # Kept open while the server runs: if each request closed the last connection,
    # SQLite would checkpoint and delete the WAL on every POST (one disk sync per request).
    return connect(db_path)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """On start, into app.state: the settings, the catalog, the key (DB_ENCRYPTION_KEY, or the one in its volume,
    created on the first start) and the database. An invalid setting or key stops the start (see StartupError)."""
    state = app.state
    per_minute = setting(RATE_LIMIT_VARIABLE, 1200, 0, 'use um número inteiro de requisições por minuto (0 desliga)')
    state.rate_limiter = RateLimiter(per_minute) if per_minute else None
    state.ttl = 3600 * setting(TTL_VARIABLE, 24, 1, 'use um número inteiro de horas, a partir de 1')
    state.allowed_hosts = allowed_hosts()
    catalog = json.loads(Path(os.environ.get('EXAMS_PATH', DEFAULT_CATALOG)).read_text(encoding='utf-8'))
    state.catalog = {row['code']: row['name'] for row in catalog}  # the same catalog the RAG server uses
    key = resolve_key(os.environ.get(KEY_VARIABLE), Path(os.environ.get('DB_KEY_FILE', DEFAULT_KEY_FILE)))
    state.cipher, state.database_key = load_key(key), key_bytes(key)
    state.db_path = Path(os.environ.get('DB_PATH', DEFAULT_DB_PATH))
    with closing(init_db(state.db_path, state.database_key, state.cipher)):
        yield


class RequestedExam(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(pattern=r'^FICT-[0-9]{3}$', description='Código do exame no catálogo fictício.',
                      examples=['FICT-001'])
    name: str | None = Field(None, min_length=1, max_length=120, examples=['Hemograma'],
                             description='Opcional: a API não o guarda nem o compara; grava e devolve o nome '
                                         'oficial do catálogo.')


class Exam(RequestedExam):
    name: str = Field(description='Nome oficial do exame no catálogo.', examples=['Hemograma completo'])


class AppointmentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    exams: list[RequestedExam] = Field(min_length=1, max_length=20,
                                       description='Exames a agendar (de 1 a 20, sem repetir).')

    @field_validator('exams')
    @classmethod
    def no_duplicates(cls, exams: list[RequestedExam]) -> list[RequestedExam]:
        if len({exam.code for exam in exams}) != len(exams):
            raise ValueError('cada código de exame deve aparecer uma única vez')
        return exams


class Appointment(BaseModel):
    id: str = Field(description='Identificador do agendamento (UUID).')
    status: Literal['scheduled'] = Field(description='Situação do agendamento.')
    exams: list[Exam] = Field(description='Exames agendados, com o nome oficial do catálogo.')
    created_at: str = Field(description='Data e hora de criação (ISO 8601, UTC).')


class Message(BaseModel):
    detail: str = Field(description='Explicação do erro.', examples=['Agendamento não encontrado.'])


class ErrorItem(BaseModel):
    loc: list[str | int] = Field(description='Caminho do campo com problema.', examples=[['body', 'exams', 0, 'code']])
    msg: str = Field(description='Explicação do problema, sem repetir o valor enviado.')
    type: str = Field(description='Tipo do erro, por exemplo `missing`, `extra_forbidden` ou `unknown_exam_code`.')


class ValidationErrors(BaseModel):
    detail: list[ErrorItem] = Field(description='Um item por problema encontrado.')


TOO_MANY_REQUESTS = {'model': Message, 'description': 'Muitas requisições deste cliente (`API_RATE_LIMIT_PER_MINUTE`); '
                     'tente de novo depois de `Retry-After` segundos.', 'headers': {'Retry-After': {
                         'description': 'Segundos até a próxima requisição ser aceita.', 'schema': {'type': 'integer'}}}}
HOST_REFUSED = {'model': Message, 'description': 'Cabeçalho `Host` fora de `API_ALLOWED_HOSTS`.'}


app = FastAPI(title='API de agendamento de exames (fictícia)', version='1.0.0', lifespan=lifespan,
              description='Recebe a lista de exames extraída do pedido médico e cria um agendamento. '
                          'Todos os dados são fictícios; nenhum dado de paciente é enviado ou guardado.')


class Middleware:
    """A plain ASGI middleware; each one below is added with app.add_middleware (the last is outermost)."""

    def __init__(self, app):
        self.app = app


class StartupError(Middleware):
    """A start refused for a key or a setting (CryptoError, ConfigError: fixed messages, never the key) reports
    one line instead of the traceback Starlette sends uvicorn. The error still propagates: uvicorn exits."""

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'lifespan':
            return await self.app(scope, receive, send)

        async def send_without_traceback(message):
            error = sys.exc_info()[1]  # Starlette sends the failure while it handles the exception
            if message['type'] == 'lifespan.startup.failed' and isinstance(error, (CryptoError, ConfigError)):
                message = {**message, 'message': f'A API não subiu: {error}'}
            await send(message)

        await self.app(scope, receive, send_without_traceback)


class BodyLimit(Middleware):
    """Read at most MAX_BODY_BYTES, also for chunked bodies without Content-Length."""

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        too_large = JSONResponse({'detail': f'Corpo maior que {MAX_BODY_BYTES} bytes.'}, status_code=413)
        length = dict(scope['headers']).get(b'content-length', b'0')
        if length.isdigit() and int(length) > MAX_BODY_BYTES:
            return await too_large(scope, receive, send)
        body, more = b'', True
        while more:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            body += message.get('body', b'')
            if len(body) > MAX_BODY_BYTES:  # stop reading: the rest is never buffered
                return await too_large(scope, receive, send)
            more = message.get('more_body', False)
        replayed = False

        async def replay():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {'type': 'http.request', 'body': body, 'more_body': False}

        await self.app(scope, replay, send)


class RateLimit(Middleware):
    """At most API_RATE_LIMIT_PER_MINUTE requests per client IP; over it, 429 with Retry-After, before
    the body is read. /health is exempt (the container's healthcheck), and 0 turns it off. The IP is
    only a key in memory: it is never logged."""

    async def __call__(self, scope, receive, send):
        # Starlette puts the app in scope before its middleware; the limiter is created at startup.
        limiter = getattr(getattr(scope.get('app'), 'state', None), 'rate_limiter', None)
        if scope['type'] != 'http' or limiter is None or scope['path'] == '/health':
            return await self.app(scope, receive, send)
        wait = limiter.take((scope.get('client') or ('',))[0])
        if wait:  # refused before routing: the access log shows this 429 with the route "(sem rota)"
            response = JSONResponse({'detail': f'Muitas requisições deste cliente: tente de novo em {wait} s.'},
                                    status_code=429, headers={'Retry-After': str(wait)})
            return await response(scope, receive, send)
        await self.app(scope, receive, send)


class TrustedHost(Middleware):
    """Starlette's TrustedHostMiddleware rule (the host name, any port), with the list read at startup and
    the API's JSON error; before the rate limit and the body. Not started yet: every request is refused."""

    async def __call__(self, scope, receive, send):
        host = re.sub(r':\d+$', '', dict(scope.get('headers', [])).get(b'host', b'').decode('latin-1').lower())
        if scope['type'] == 'http' and host not in getattr(scope['app'].state, 'allowed_hosts', ()):
            return await JSONResponse({'detail': 'Cabeçalho Host não permitido: chame a API por 127.0.0.1 ou localhost '
                                                 '(no host) ou por api (na rede do compose), ou inclua o nome em '
                                                 'API_ALLOWED_HOSTS.'}, status_code=400)(scope, receive, send)
        await self.app(scope, receive, send)


SECURITY_HEADERS = [(b'x-content-type-options', b'nosniff'), (b'x-frame-options', b'DENY'),
                    (b'referrer-policy', b'no-referrer')]
# The JSON routes load nothing, so nothing is allowed. Not on the docs pages: their HTML loads the
# Swagger UI and ReDoc scripts and styles from cdn.jsdelivr.net, with an inline script.
CONTENT_SECURITY_POLICY = (b'content-security-policy', b"default-src 'none'; frame-ancestors 'none'")
DOCS_PAGES = ('/docs', '/docs/oauth2-redirect', '/redoc')
NO_STORE = (b'cache-control', b'no-store')  # /appointments responses carry the decrypted exam list


class SecurityHeaders(Middleware):
    """The security headers on every response, the 400, 413 and 429 above included, and the API's JSON 500: an
    unhandled exception would reach Starlette's ServerErrorMiddleware, outside every added middleware, as a bare
    500. It is sent here (through RequestLog, with an X-Request-ID) and re-raised, so the server still logs it."""

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        path = scope['path']
        extra = [*SECURITY_HEADERS, *([] if path in DOCS_PAGES else [CONTENT_SECURITY_POLICY]),
                 *([NO_STORE] if path == '/appointments' or path.startswith('/appointments/') else [])]
        started = False

        async def send_with_headers(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
                names = {name.lower() for name, _ in message.get('headers', [])}
                message['headers'] = [*message.get('headers', []), *(item for item in extra if item[0] not in names)]
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        except Exception:
            if not started:
                await JSONResponse({'detail': 'Erro interno ao processar a requisição.'},
                                   status_code=500)(scope, receive, send_with_headers)
            raise


# One JSON line per request on stderr; it replaces uvicorn's access log (which prints the raw path).
ACCESS_LOG = logging.getLogger('api.access')
if not ACCESS_LOG.handlers:  # a handler set before the import (a test, a deployment) is kept
    ACCESS_LOG.addHandler(logging.StreamHandler())  # its default format is the message alone
ACCESS_LOG.setLevel(logging.INFO)  # always: with a handler set earlier, INFO lines would otherwise vanish
ACCESS_LOG.propagate = False
logging.getLogger('uvicorn.access').disabled = True
REQUEST_ID = re.compile(r'[A-Za-z0-9._-]{1,64}')  # anything else is replaced, so the log cannot be forged


class RequestLog(Middleware):
    """request_id (X-Request-ID or a new one, echoed back), method, route, status, ms. Never the body, the query
    or the Idempotency-Key; the route is the template (/appointments/{appointment_id}), never an unknown path."""

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        given = dict(scope['headers']).get(b'x-request-id', b'').decode('latin-1')
        request_id = given if REQUEST_ID.fullmatch(given) else uuid.uuid4().hex
        status, start = 500, time.perf_counter()

        async def send_with_id(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
                message['headers'] = [*message.get('headers', []), (b'x-request-id', request_id.encode('ascii'))]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            # API routes leave their template in scope; /docs and /openapi.json only their endpoint.
            route = getattr(scope.get('route'), 'path', None) or (scope['path'] if 'endpoint' in scope else None)
            ACCESS_LOG.info(json.dumps({'request_id': request_id, 'method': scope['method'],
                                        'route': route or '(sem rota)', 'status': status,
                                        'duration_ms': round((time.perf_counter() - start) * 1000, 1)}))


# Innermost first; RequestLog, outermost, logs the 400, 413 and 429 of the others too.
for middleware in (StartupError, BodyLimit, RateLimit, TrustedHost, SecurityHeaders, RequestLog):
    app.add_middleware(middleware)


def count(number: int, one: str, many: str) -> str:
    return f'{number} {one if number == 1 else many}'


# Pydantic's error type -> the reason in Portuguese, built from the field's limits (ctx), never
# from the rejected value (pydantic's own text for a bad UUID quotes a character of it).
MESSAGES: dict[str, Callable[[dict], str]] = {
    'missing': lambda ctx: 'Campo obrigatório ausente.',
    'extra_forbidden': lambda ctx: 'Campo não permitido: envie só os campos documentados.',
    'string_type': lambda ctx: 'Deve ser um texto.',
    'list_type': lambda ctx: 'Deve ser uma lista.',
    'model_attributes_type': lambda ctx: 'Deve ser um objeto JSON, como {"exams": [...]}.',
    'dict_type': lambda ctx: 'Deve ser um objeto JSON.',
    'json_invalid': lambda ctx: 'JSON inválido: confira aspas, vírgulas e chaves.',
    'too_short': lambda ctx: f"Deve ter pelo menos {count(ctx['min_length'], 'item', 'itens')}.",
    'too_long': lambda ctx: f"Deve ter no máximo {count(ctx['max_length'], 'item', 'itens')}.",
    'string_too_short': lambda ctx: f"Deve ter pelo menos {count(ctx['min_length'], 'caractere', 'caracteres')}.",
    'string_too_long': lambda ctx: f"Deve ter no máximo {count(ctx['max_length'], 'caractere', 'caracteres')}.",
    'string_pattern_mismatch': lambda ctx: 'Formato inválido.',
    'uuid_parsing': lambda ctx: 'Deve ser um UUID, como 3fa85f64-5717-4562-b3fc-2c963f66afa6.',
    'uuid_type': lambda ctx: 'Deve ser um UUID, como 3fa85f64-5717-4562-b3fc-2c963f66afa6.',
}


def portuguese(item: dict) -> str:
    """The reason for one validation error; a type not mapped above keeps pydantic's message."""
    kind, loc = item['type'], tuple(item['loc'])
    if loc == ('header', 'Idempotency-Key'):
        return 'Idempotency-Key inválida: use de 1 a 128 caracteres ASCII visíveis, sem espaço nem acento.'
    if kind == 'string_pattern_mismatch' and loc[-1:] == ('code',):
        return 'Código fora do formato: use FICT- e 3 dígitos, como FICT-001.'
    if kind == 'value_error':  # raised by this API's own validators, already in Portuguese
        text = item['msg'].removeprefix('Value error, ')
        return text[:1].upper() + text[1:] + ('' if text.endswith('.') else '.')
    message = MESSAGES.get(kind)
    return message(item.get('ctx') or {}) if message else item['msg']


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    # Field, reason and type only: the rejected value may contain personal data.
    detail = [{'loc': item['loc'], 'type': item['type'], 'msg': portuguese(item)} for item in error.errors()]
    return JSONResponse({'detail': detail}, status_code=422)


@app.get('/health', operation_id='health', tags=['operação'], summary='Verificar se a API está no ar',
         description='Retorna `ok` e a quantidade de exames do catálogo carregado.')
def health(request: Request) -> dict:
    return {'status': 'ok', 'catalog_size': len(request.app.state.catalog)}


@app.post('/appointments', operation_id='create_appointment', tags=['agendamentos'], status_code=201,
          response_model=Appointment, summary='Criar agendamento',
          description='Valida cada código no catálogo e grava o agendamento. Só o `code` de cada exame conta: o '
                      '`name` é opcional, e a API grava e devolve o nome oficial do catálogo. Código fora do formato '
                      '`FICT-000`, repetido ou inexistente no catálogo retorna 422; cada item de `detail` '
                      'aponta o campo (`loc`) e explica o motivo (`msg`). Corpo acima de 16 KB retorna 413. '
                      'Com `Idempotency-Key`, repetir a requisição com os mesmos códigos devolve o mesmo agendamento.',
          responses={400: HOST_REFUSED,
                     409: {'model': Message, 'description': '`Idempotency-Key` já usada com outros códigos.'},
                     413: {'model': Message, 'description': 'Corpo da requisição grande demais.'},
                     422: {'model': ValidationErrors, 'description': 'Corpo inválido ou código de exame desconhecido.'},
                     429: TOO_MANY_REQUESTS})
def create_appointment(
        request: AppointmentRequest,
        http: Request,
        idempotency_key: Annotated[str | None, Header(
            alias='Idempotency-Key', min_length=1, max_length=128, pattern=r'^[\x21-\x7e]+$',
            description='Opcional. Mesma chave e mesmos códigos (em qualquer ordem, com qualquer `name`) devolvem '
                        'o mesmo agendamento (201) sem criar outro; mesma chave com outros códigos retorna 409. '
                        'Guardada só como HMAC, por `API_IDEMPOTENCY_TTL_HOURS` (padrão 24 h); depois pode ser '
                        'usada de novo. De 1 a 128 caracteres ASCII visíveis.',
            examples=['3f1c9a2e-retry-1'])] = None) -> Appointment:
    state = http.app.state
    # Same shape as the validation errors above, so the documented 422 schema holds.
    unknown = [{'loc': ['body', 'exams', index, 'code'], 'type': 'unknown_exam_code',
                'msg': 'Código de exame desconhecido no catálogo.'}
               for index, exam in enumerate(request.exams) if exam.code not in state.catalog]
    if unknown:
        raise HTTPException(422, detail=unknown)
    # The stored name is always the catalog's: free text from the request is never kept.
    exams = [Exam(code=exam.code, name=state.catalog[exam.code]) for exam in request.exams]
    created = int(now())
    appointment = Appointment(id=str(uuid.uuid4()), status='scheduled', exams=exams,
                              created_at=datetime.fromtimestamp(created, timezone.utc).isoformat(timespec='seconds'))
    # closing() closes the connection; the inner `with connection` commits the insert.
    with WRITE_LOCK, closing(connect(state.db_path)) as connection, connection:
        if idempotency_key:
            connection.execute('BEGIN IMMEDIATE')  # the key check and the insert are one write transaction
            connection.execute('DELETE FROM idempotency_keys WHERE created_at <= ?', (created - state.ttl,))
            row = (*fingerprints(state.database_key, idempotency_key, [exam.code for exam in request.exams]),
                   appointment.id, created)
            seen = connection.execute('SELECT request_hash, appointment_id FROM idempotency_keys WHERE key_hash = ?',
                                      row[:1]).fetchone()
            if seen and not hmac.compare_digest(seen[0], row[1]):
                raise HTTPException(409, detail='Esta Idempotency-Key já foi usada com outros exames. '
                                                'Para um novo agendamento, use uma chave nova.')
            if seen:
                return load_appointment(connection, seen[1], state.cipher)
            connection.execute('INSERT INTO idempotency_keys VALUES (?, ?, ?, ?)', row)
        connection.execute('INSERT INTO appointments VALUES (?, ?, ?, ?)',
                           (appointment.id, appointment.status,
                            encrypt(state.cipher, json.dumps([exam.model_dump() for exam in exams]),
                                    row_fields(appointment.id, appointment.status, appointment.created_at)),
                            appointment.created_at))
    return appointment


@app.get('/appointments/{appointment_id}', operation_id='get_appointment', tags=['agendamentos'],
         response_model=Appointment, summary='Consultar agendamento',
         description='Retorna um agendamento criado anteriormente pelo seu `id` (UUID); '
                     'um `id` que não é UUID retorna 422.',
         responses={400: HOST_REFUSED, 404: {'model': Message, 'description': 'Agendamento não encontrado.'},
                    422: {'model': ValidationErrors, 'description': '`id` não é um UUID.'},
                    429: TOO_MANY_REQUESTS,
                    500: {'model': Message, 'description': 'Registro cifrado ilegível (chave diferente ou dado alterado).'}})
def get_appointment(appointment_id: uuid.UUID, http: Request) -> Appointment:
    with closing(connect(http.app.state.db_path)) as connection:
        return load_appointment(connection, str(appointment_id), http.app.state.cipher)


def load_appointment(connection: sqlite3.Connection, appointment_id: str, cipher: AESGCM) -> Appointment:
    row = connection.execute('SELECT id, status, exams, created_at FROM appointments WHERE id = ?',
                             (appointment_id,)).fetchone()
    if row is None:
        raise HTTPException(404, detail='Agendamento não encontrado.')
    try:
        exams = json.loads(decrypt(cipher, row[2], row_fields(row[0], row[1], row[3])))
    except CryptoError as error:
        raise HTTPException(500, detail=str(error)) from None
    return Appointment(id=row[0], status=row[1], exams=exams, created_at=row[3])
