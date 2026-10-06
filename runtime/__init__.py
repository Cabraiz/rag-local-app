"""The transpiler's runtime library: what a generated agent.py imports.

Generic, for any spec: adk (the Gemini model and the MCP and OpenAPI toolsets), rede (the hosts ALLOWED_HOSTS
allows and the addresses each name is pinned to) and web (`adk web` behind a Host check). These need only
Google ADK and the MCP SDK it brings.

The booking domain, an App plugin a spec may declare: plugin (BookingPlugin), made of the callbacks (callbacks),
confidence rules (confianca), the order's record (pedido), what of the person's message reaches the model
(entrada), the final confirmation of the list (confirmacao), its own calls to the MCP servers (servidores), the
check of the whole order (reconcilia) and the run's report (relatorio). They also need `catalogo`, for words() (a
query is compared word for word the way the RAG search wrote it), and `leitura`, the OCR's reply. Nothing
here imports them: a generated file imports the plugin only when its spec declares it.

A generated file only declares the agent; the rules live here, the same for every spec, tested on their own
(tests/test_confianca.py, tests/test_runtime.py). The names below are its interface. API_VERSION changes when one
of them changes meaning or signature, or a name generated files use is added or removed; the transpiler writes it
into each generated file, which checks it on import (require_api). 6: no `guarded`, the BookingPlugin puts its rule
on untrusted data before each instruction itself.
"""
import warnings

# ADK's "[EXPERIMENTAL] feature ..." notices (ResumabilityConfig, FallbackModel...) would fill `adk run`'s
# console: the agent's folder imports this library first, and they are silenced here (cli.py does too).
warnings.filterwarnings('ignore', message=r'\[EXPERIMENTAL\]', category=UserWarning)

from .adk import LiveOpenAPIToolset, McpToolset, gemini  # noqa: E402  (after the filter)

API_VERSION = 6
__all__ = ['API_VERSION', 'LiveOpenAPIToolset', 'McpToolset', 'gemini', 'require_api']


def require_api(version: int) -> None:
    """Stop the import of a generated file made for another interface of this library."""
    if version != API_VERSION:
        raise ImportError(f'agent.py foi gerado para a interface {version} do runtime, e este runtime tem a '
                          f'{API_VERSION}: gere de novo com `python -m cli transpile <spec>`')
