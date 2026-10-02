from . import ledger
from .application import RequestService
from .config import enforce_lab
import os


def request_service():
    enforce_lab()
    return RequestService(ledger)


def process_services(role):
    enforce_lab()
    if role != 'worker':
        return ledger, None
    from .adk_workflow import AdkAbstentionWorkflow, AdkRetrievalWorkflow
    return ledger, AdkRetrievalWorkflow() if os.environ.get('RAG_RETRIEVAL')=='extractive_lab' else AdkAbstentionWorkflow()
