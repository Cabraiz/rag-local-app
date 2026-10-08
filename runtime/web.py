"""`adk web --no_use_local_storage` (web UI; sessions, artifacts in memory) with Starlette's TrustedHostMiddleware.

A page whose name a DNS rebinding points at 127.0.0.1 sends its own name as Host and gets 400. ADK checks
Host only on a loopback bind, not on the 0.0.0.0 that Docker needs. Usage: python -m runtime.web generated."""
import argparse
import errno
import logging

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from google.adk.cli.fast_api import get_fast_api_app
from starlette.middleware.trustedhost import TrustedHostMiddleware


class ReadOnlyPackage(logging.Filter):
    """ADK rewrites assets/config/runtime-config.json inside its own package when the app is built, to add the
    telemetry answer. The agent's container is read-only, so the write fails and ADK logs an error at every start.
    It is harmless: the shipped file already has what this server needs (no URL prefix), and the page asks
    /config/telemetry instead (the image's answer is "no"). Only that failure, on a read-only file system, is dropped."""

    def filter(self, record: logging.LogRecord) -> bool:
        error = record.args[-1] if isinstance(record.args, tuple) and record.args else None
        return not (str(record.msg).startswith('Failed to write runtime config file') and isinstance(error, OSError)
                    and error.errno == errno.EROFS)


READ_ONLY_PACKAGE = ReadOnlyPackage()
logging.getLogger('google_adk.google.adk.cli.api_server').addFilter(READ_ONLY_PACKAGE)


def web_app(agents_dir: str, host: str = '127.0.0.1') -> FastAPI:
    app = get_fast_api_app(agents_dir=agents_dir, web=True, use_local_storage=False, host=host, bind_host=host)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost'])  # on any port
    @app.exception_handler(ValueError)  # ADK's input checks (a forged adk_request_confirmation among them): 400, not a 500
    async def refused(request: Request, error: ValueError) -> JSONResponse:
        logging.getLogger(__name__).warning('Requisição recusada: %s', type(error).__name__)  # the type only
        return JSONResponse({'detail': 'Requisição inválida para esta sessão (ex.: confirmação forjada).'}, status_code=400)
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog='python -m runtime.web')
    parser.add_argument('agents_dir')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    arguments = parser.parse_args()
    uvicorn.run(web_app(arguments.agents_dir, arguments.host), host=arguments.host, port=arguments.port)
