"""Fictional booking ledger; all contracts appear in OpenAPI, no patient PII."""
from contextlib import closing
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from uuid import UUID, uuid4
from typing import Literal
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .catalog import Catalog
from .safe_logging import setup
from .contracts import (AppointmentRequest, AppointmentReceipt, ValidationFailure,
    CatalogError, ConflictError, NotFoundError, LedgerError,
    InvalidEnvelope, BodyTimeout, BodyLimit, JSONRequired)
from .validation import issues
from .envelope import BoundedJSON

setup()
catalog = Catalog()
DB = os.environ.get('CLINIC_DB', '/state/appointments.sqlite3')
def connect():
    connection = sqlite3.connect(DB, timeout=3)
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA synchronous=FULL')
        connection.execute('CREATE TABLE IF NOT EXISTS appointments(request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL)')
    except BaseException:
        connection.close()
        raise
    return connection

def saved_receipt(row):
    """Never trust stored JSON merely because the request key exists."""
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError('duplicate stored field')
            result[key] = item
        return result
    try:
        if len(row[2]) > 4096:
            raise ValueError()
        payload = json.loads(row[2], object_pairs_hook=unique,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError('number')))
        value = AppointmentReceipt.model_validate(payload).model_dump()
        body = {key: value[key] for key in ('request_id', 'exam_codes', 'catalog_version')}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        if (value['request_id'] != row[0] or digest != row[1]
                or value['catalog_version'] != catalog.version
                or any(code not in catalog.by_code for code in value['exam_codes'])):
            raise ValueError()
        return value
    except (ValueError, TypeError, RecursionError):
        raise sqlite3.DataError('INVALID_STORED_RECEIPT') from None

with closing(connect()) as connection:
    connection.commit()

app = FastAPI(title='Clinica ficticia - agendamentos', version='1.0.0', docs_url=None, redoc_url=None,
              description='Demonstracao: dados ficticios, sem pacientes reais ou agendamento clinico real.')
app.add_middleware(BoundedJSON)
app.mount('/static/docs', StaticFiles(directory=Path(__file__).resolve().parents[2]/'static/docs'), name='docs-assets')

@app.get('/docs', include_in_schema=False)
def docs():
    return get_swagger_ui_html(openapi_url=app.openapi_url, title=app.title+' - Swagger',
        swagger_js_url='/static/docs/swagger-ui-bundle.js', swagger_css_url='/static/docs/swagger-ui.css',
        swagger_favicon_url='/static/docs/favicon-32x32.png',
        swagger_ui_parameters={'validatorUrl':None, 'persistAuthorization':False})

@app.exception_handler(RequestValidationError)
async def invalid(request, error):
    return JSONResponse(status_code=422, content={'code': 'INVALID_REQUEST',
                        'errors': issues(error.errors())})

@app.get('/health', tags=['operacao'])
def health():
    return {'ok': True, 'fictional': True, 'catalog_count': len(catalog.entries)}

@app.post('/appointments', response_model=AppointmentReceipt, status_code=201, tags=['agendamentos'],
          responses={200: {'description': 'Replay idempotente', 'model': AppointmentReceipt},
                     400: {'description': 'JSON invalido ou campos duplicados', 'model':InvalidEnvelope},
                     408: {'description': 'Timeout de leitura do corpo', 'model':BodyTimeout},
                     413: {'description': 'Corpo maior que 4096 bytes', 'model':BodyLimit},
                     415: {'description': 'Content-type application/json obrigatorio', 'model':JSONRequired},
                     409: {'description': 'Chave reutilizada com outro corpo', 'model':ConflictError},
                     422: {'description': 'Contrato sanitizado ou catalogo invalido', 'model':ValidationFailure | CatalogError},
                     503: {'description': 'Ledger indisponivel', 'model':LedgerError}},
          openapi_extra={'requestBody':{'content':{'application/json':{'example':{
              'request_id':'00000000-0000-4000-8000-000000000001',
              'exam_codes':['FICT-001','FICT-002','FICT-005'], 'catalog_version':catalog.version}}}}})
def create(value: AppointmentRequest):
    if value.catalog_version != catalog.version or any(code not in catalog.by_code for code in value.exam_codes):
        raise HTTPException(422, detail='CATALOG_VERSION_MISMATCH')
    body = value.model_dump()
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    try:
        with closing(connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            old = connection.execute('SELECT request_id,digest,result FROM appointments WHERE request_id=?', (value.request_id,)).fetchone()
            if old:
                if old[1] != digest:
                    raise HTTPException(409, detail='IDEMPOTENCY_CONFLICT')
                return JSONResponse(status_code=200, content=saved_receipt(old))
            result = {'appointment_id': str(uuid4()), 'request_id': value.request_id, 'status': 'REQUESTED',
                      'exam_codes': value.exam_codes, 'catalog_version': value.catalog_version}
            connection.execute('INSERT INTO appointments VALUES (?,?,?)', (value.request_id, digest, json.dumps(result)))
        return result
    except sqlite3.Error:
        raise HTTPException(503, detail='LEDGER_UNAVAILABLE_RETRY_SAME_KEY') from None

@app.get('/appointments/by-request/{request_id}', response_model=AppointmentReceipt, tags=['agendamentos'],
         responses={404: {'description': 'Solicitacao inexistente', 'model':NotFoundError},
                    422: {'description': 'Identificador invalido, erro sanitizado', 'model':ValidationFailure},
                    503: {'description': 'Ledger indisponivel ou recibo invalido', 'model':LedgerError}})
def by_request(request_id: UUID):
    try:
        with closing(connect()) as connection:
            row = connection.execute('SELECT request_id,digest,result FROM appointments WHERE request_id=?', (str(request_id),)).fetchone()
        if not row:
            raise HTTPException(404, detail='NOT_FOUND')
        return saved_receipt(row)
    except sqlite3.Error:
        raise HTTPException(503, detail='LEDGER_UNAVAILABLE_RETRY_SAME_KEY') from None

@app.get('/appointments/{appointment_id}', response_model=AppointmentReceipt, tags=['agendamentos'],
         responses={404: {'description': 'Agendamento ficticio inexistente', 'model':NotFoundError},
                    422: {'description': 'Identificador invalido, erro sanitizado', 'model':ValidationFailure},
                    503: {'description': 'Ledger indisponivel ou recibo invalido', 'model':LedgerError}})
def read(appointment_id: UUID):
    try:
        with closing(connect()) as connection:
            row = connection.execute("SELECT request_id,digest,result FROM appointments WHERE json_extract(result,'$.appointment_id')=?", (str(appointment_id),)).fetchone()
        if not row:
            raise HTTPException(404, detail='NOT_FOUND')
        return saved_receipt(row)
    except sqlite3.Error:
        raise HTTPException(503, detail='LEDGER_UNAVAILABLE_RETRY_SAME_KEY') from None
