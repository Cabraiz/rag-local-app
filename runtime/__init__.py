"""The transpiler's runtime library: what a generated agent.py imports.

Its building blocks (adk) and the booking policy, as callbacks (callbacks), confidence rules
(confianca) and the [s/N] question (confirmacao). A generated file only declares the agent;
the rules live here, the same for every spec, tested on their own (tests/test_confianca.py,
tests/test_runtime.py). Besides Google ADK it needs only `catalogo`, for words(): a query is
compared word for word the way the RAG search wrote it.

The names below are its interface. API_VERSION changes when one of them changes meaning or
signature, or a name is added that generated files use; the transpiler writes it into each
generated file, which checks it on import (require_api). 2: BookingCallbacks.review_list, for a
spec that lists exams without booking. 3: McpToolset, and both toolsets refuse a host outside
ALLOWED_HOSTS, so a file generated before that stops instead of running unchecked.
"""
from .adk import LiveOpenAPIToolset, McpToolset, gemini, guarded
from .callbacks import BookingCallbacks, mcp_payload
from .confianca import BookingPolicy

API_VERSION = 3
__all__ = ['API_VERSION', 'BookingCallbacks', 'BookingPolicy', 'LiveOpenAPIToolset', 'McpToolset', 'gemini',
           'guarded', 'mcp_payload', 'require_api']


def require_api(version: int) -> None:
    """Stop the import of a generated file made for another interface of this library."""
    if version != API_VERSION:
        raise ImportError(f'agent.py foi gerado para a interface {version} do runtime, e este runtime tem a '
                          f'{API_VERSION}: gere de novo com `python -m cli transpile <spec>`')
