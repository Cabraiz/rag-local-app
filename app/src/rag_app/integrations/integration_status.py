"""Local wiring metadata only; never authenticates or calls external providers.

These flags describe this application version, not the health of a remote account.
The isolated Atlassian probe is deliberately not imported or run by the RAG API.
"""
from pathlib import Path


def read():
    return {
        'mode': 'lab', 'verification': 'local_wiring_only', 'external_calls': 0,
        'providers': [
            {'id': 'jira', 'name': 'Jira / Atlassian',
             'adapter_present': Path(__file__).with_name('atlassian_lab.py').is_file(),
             'rag_connected': False, 'online_health': 'not_checked',
             'reason': 'ISOLATED_READONLY_PROBE_NOT_WIRED',
             'manual_url': 'https://rag-local-lab-mateus.atlassian.net/jira/software/projects/KAN/boards/2'},
            {'id': 'github', 'name': 'GitHub', 'adapter_present': Path(__file__).with_name('github_lab.py').is_file(),
             'rag_connected': False, 'online_health': 'not_checked',
             'reason': 'ISOLATED_READONLY_PROBE_NOT_WIRED',
             'manual_url': 'https://github.com/Cabraiz/rag-mcp-lab'}]}
