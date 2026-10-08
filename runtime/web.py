"""`adk web --no_use_local_storage` (web UI; sessions, artifacts in memory) with Starlette's TrustedHostMiddleware.

A page whose name a DNS rebinding points at 127.0.0.1 sends its own name as Host and gets 400. ADK checks
Host only on a loopback bind, not on the 0.0.0.0 that Docker needs. Usage: python -m runtime.web generated."""
import argparse

import uvicorn
from google.adk.cli.fast_api import get_fast_api_app
from starlette.middleware.trustedhost import TrustedHostMiddleware


def web_app(agents_dir: str, host: str = '127.0.0.1'):
    app = get_fast_api_app(agents_dir=agents_dir, web=True, use_local_storage=False, host=host, bind_host=host)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost'])  # on any port
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog='python -m runtime.web')
    parser.add_argument('agents_dir')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    arguments = parser.parse_args()
    uvicorn.run(web_app(arguments.agents_dir, arguments.host), host=arguments.host, port=arguments.port)
