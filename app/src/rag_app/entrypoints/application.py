from typing import Protocol
from .domain import Identity


class LedgerPort(Protocol):
    def accept(self, identity: Identity, question: str, key: str) -> str: ...
    def read(self, identity: Identity, rid=None): ...
    def cancel(self, identity: Identity, rid): ...
    def health(self): ...
    def resolve(self, identity: Identity, key: str): ...


class RequestService:
    def __init__(self, ledger: LedgerPort):
        self.ledger = ledger

    def submit(self, identity, question, key):
        from .resilience import admit_http
        admit_http(identity)
        rid = self.ledger.accept(identity, question, key)
        return dict(request_id=rid, status_url='/v1/requests/'+rid)

    def status(self, identity, rid=None): return self.ledger.read(identity,rid)
    def cancel(self, identity, rid): return self.ledger.cancel(identity,rid)
    def health(self): return self.ledger.health()
    def resolve(self, identity, key): return self.ledger.resolve(identity,key)
