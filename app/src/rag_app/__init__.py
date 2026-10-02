"""RAG local first slice. Production mode is intentionally disabled until Gate B."""

# Keep the stable public modules (rag_app.api, rag_app.process, etc.) while
# separating the physical files by responsibility. Relative imports still use
# this one package; no import hooks, copied modules or duplicate implementations.
from pathlib import Path as _Path

_root = _Path(__file__).resolve().parent
__path__ = [str(_root)] + [str(_root / area) for area in (
    'entrypoints', 'domain', 'runtime', 'persistence', 'retrieval', 'models', 'integrations',
)]
