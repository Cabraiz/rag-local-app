"""Opt-in, fixed synthetic ADK connectivity probe; not a RAG answer workflow."""
import asyncio
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

MODEL = 'gemini-3.5-flash-lite'
# Authorized lab ceiling, not a Google quota or a monetary hard cap.
# Never reset the durable usage counter when changing this policy.
DAILY_ATTEMPTS = 1000
USAGE_BUSY_TIMEOUT_SECONDS = 5
PROMPT = 'Responda exatamente RAG_LAB_OK. Sem ferramentas e sem texto adicional.'


class ProbeBlocked(RuntimeError):
    pass


def configuration(environ=None):
    env = os.environ if environ is None else environ
    if (env.get('RAG_MODE') != 'lab' or env.get('RAG_GEMINI_PROBE') != 'synthetic_only'
            or env.get('RAG_GEMINI_FREE_CONFIRMED') != 'no_billing'
            or env.get('GOOGLE_GENAI_USE_VERTEXAI', '').lower() != 'false'):
        raise ProbeBlocked('GEMINI_FREE_OPT_IN_REQUIRED')
    # No model/base-url/token-budget input can override these fixed restrictions.
    return Path('/run/secrets/gemini_api_key'), Path('/usage/gemini-probes.sqlite3')


@contextmanager
def usage_connection(path):
    try:
        with closing(sqlite3.connect(path, timeout=USAGE_BUSY_TIMEOUT_SECONDS)) as db, db:
            yield db
    except sqlite3.OperationalError as error:
        code = getattr(error, 'sqlite_errorcode', None)
        if isinstance(code, int) and (code & 255) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise ProbeBlocked('USAGE_COUNTER_BUSY') from None
        raise


def reserve_attempt(path, day=None):
    day = day or datetime.now(timezone.utc).date().isoformat()
    with usage_connection(path) as db:
        db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE IF NOT EXISTS attempts(day TEXT PRIMARY KEY, used INTEGER NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT used FROM attempts WHERE day=?', (day,)).fetchone()
        used = row[0] if row else 0
        validate_counter(used)
        if used >= DAILY_ATTEMPTS:
            raise ProbeBlocked('DAILY_PROBE_LIMIT')
        db.execute('INSERT INTO attempts VALUES (?,1) ON CONFLICT(day) DO UPDATE SET used=used+1', (day,))
    return used + 1


def validate_counter(used):
    if type(used) is not int or not 0 <= used <= DAILY_ATTEMPTS:
        raise ProbeBlocked('INVALID_DAILY_COUNTER')


async def run_adk(api_key):
    from google import genai
    from google.genai import types
    from google.adk.agents import LlmAgent
    from google.adk.models import Gemini
    from google.adk.runners import Runner
    from google.adk.agents.run_config import RunConfig
    from google.adk.sessions import InMemorySessionService

    # Explicit client avoids ambient API keys, Vertex settings and proxy endpoints.
    client = genai.Client(api_key=api_key, vertexai=False, http_options=types.HttpOptions(
        base_url='https://generativelanguage.googleapis.com', api_version='v1beta',
        timeout=15000, retry_options=types.HttpRetryOptions(attempts=1)))
    sessions = InMemorySessionService()
    sid = str(uuid4())
    agent = LlmAgent(name='gemini_connectivity_probe', model=Gemini(model=MODEL, client=client),
        instruction='Esta é uma prova sintética de conectividade. Retorne apenas RAG_LAB_OK.',
        tools=[], generate_content_config=types.GenerateContentConfig(
            temperature=0, max_output_tokens=32, thinking_config=types.ThinkingConfig(
                thinking_level=types.ThinkingLevel.MINIMAL, include_thoughts=False)))
    try:
        await sessions.create_session(app_name='gemini_lab_probe', user_id='synthetic', session_id=sid)
        runner = Runner(app_name='gemini_lab_probe', agent=agent, session_service=sessions)
        result = None
        async for event in runner.run_async(user_id='synthetic', session_id=sid,
                new_message=types.Content(role='user', parts=[types.Part(text=PROMPT)]),
                run_config=RunConfig(max_llm_calls=1)):
            if event.is_final_response() and event.content:
                result = ''.join(p.text or '' for p in event.content.parts or [])
        return result == 'RAG_LAB_OK'
    finally:
        await client.aio.aclose()
        client.close()


async def execute(secret_path, usage_path, invoke=run_adk):
    key = secret_path.read_text(encoding='utf8').strip()
    if not 20 <= len(key) <= 256 or any(c.isspace() for c in key):
        raise ProbeBlocked('INVALID_PRIVATE_KEY_FILE')
    attempt = reserve_attempt(usage_path)
    try:
        passed = await asyncio.wait_for(invoke(key), timeout=20)
        return {'kind': 'gemini_synthetic_probe', 'passed': bool(passed), 'model': MODEL,
                'attempt_today': attempt, 'daily_limit': DAILY_ATTEMPTS, 'vertex': False}
    finally:
        key = ''


def main():
    # No SDK error bodies, credentials or agent event text enter our output.
    logging.disable(logging.CRITICAL)
    try:
        secret_path, usage_path = configuration()
        result = asyncio.run(execute(secret_path, usage_path))
        print(json.dumps(result), flush=True)
        return 0 if result['passed'] else 1
    except Exception as error:
        code = getattr(error, 'code', None)
        # Inspect privately; emit only a bounded reason enum, never SDK text.
        message = str(getattr(error, 'message', '')).lower()
        reason = ('model_or_method_unavailable' if 'model' in message and ('not found' in message or 'not supported' in message)
                  else 'credential_rejected' if 'api key' in message and ('invalid' in message or 'expired' in message)
                  else 'quota_blocked' if code == 429 else 'unspecified')
        print(json.dumps({'kind': 'gemini_synthetic_probe', 'passed': False,
            'error_type': type(error).__name__, 'provider_code': code if isinstance(code, int) else None,
            'provider_reason':reason}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
