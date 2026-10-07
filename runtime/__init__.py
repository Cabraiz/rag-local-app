"""The transpiler's runtime library: what a generated agent.py imports.

Its building blocks (adk) and the booking policy, as callbacks (callbacks), confidence rules
(confianca), the [s/N] question (confirmacao), the addresses the agent may reach (rede), its own
calls to the MCP servers (servidores) and the run's report (relatorio). A generated file only
declares the agent; the rules live here, the same for every spec, tested on their own
(tests/test_confianca.py, tests/test_runtime.py). Besides Google ADK (and the MCP SDK it brings) it
needs only `catalogo`, for words(): a query is compared word for word the way the RAG search wrote it.

The names below are its interface. API_VERSION changes when one of them changes meaning or
signature, or a name is added that generated files use; the transpiler writes it into each
generated file, which checks it on import (require_api). 2: BookingCallbacks.review_list, for a
spec that lists exams without booking. 3: McpToolset, and both toolsets refuse a host outside
ALLOWED_HOSTS, so a file generated before that stops instead of running unchecked. 4: the agent
starts itself under `adk run` / `adk web`: BookingCallbacks takes the servers' URLs and has
start_order, before_model and report, so a file generated before that, which would run there
with no image and no address check, stops. 5: gemini(model, fallback=...), the reserve model per
request, and BookingCallbacks.model_failed, which ends a step the model could not answer in one line.
"""
from .adk import LiveOpenAPIToolset, McpToolset, gemini, guarded
from .callbacks import BookingCallbacks, image_token, mcp_payload
from .confianca import BookingPolicy

API_VERSION = 5
__all__ = ['API_VERSION', 'BookingCallbacks', 'BookingPolicy', 'LiveOpenAPIToolset', 'McpToolset', 'gemini',
           'guarded', 'image_token', 'mcp_payload', 'require_api']


def require_api(version: int) -> None:
    """Stop the import of a generated file made for another interface of this library."""
    if version != API_VERSION:
        raise ImportError(f'agent.py foi gerado para a interface {version} do runtime, e este runtime tem a '
                          f'{API_VERSION}: gere de novo com `python -m cli transpile <spec>`')
