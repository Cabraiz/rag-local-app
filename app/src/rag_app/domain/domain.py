from dataclasses import dataclass
from typing import Protocol

TERMINALS = frozenset({'SUCCEEDED', 'FAILED_FINAL', 'EXPIRED', 'CANCELLED'})


@dataclass(frozen=True)
class Identity:
    tenant: str
    actor: str


@dataclass(frozen=True)
class Citation:
    document_id: str
    chunk_id: str
    release_id: str
    content_hash: str
    acl_epoch: int
    quote: str


@dataclass(frozen=True)
class Proposal:
    kind: str
    text: str
    citations: tuple[Citation, ...] = ()
    model: str | None = None


class WorkflowPort(Protocol):
    async def run(self, request: dict) -> Proposal: ...


class RequestError(Exception):
    def __init__(self, code: str, status: int):
        self.code, self.status = code, status


def key_timestamp(key: str, server_now: float) -> int:
    from uuid import UUID
    try:
        timestamp, nonce = key.split('.', 1)
        issued = int(timestamp)
        if str(UUID(nonce)) != nonce or issued > server_now+60 or server_now-issued > 604800:
            raise ValueError()
        return issued
    except (ValueError, AttributeError):
        raise RequestError('INVALID_OR_EXPIRED_IDEMPOTENCY_KEY', 409) from None
