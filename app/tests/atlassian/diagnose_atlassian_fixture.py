# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Offline only: synthetic fixture, emits parameter names/types, never values."""
import asyncio
from unittest.mock import patch
from atlassian_controls import round_checks
from rag_app import atlassian_lab as lab

original = lab.authorize_rpc


def inspect_rpc(message):
    if message.get('method') == 'tools/call':
        params = message.get('params', {})
        print({'parameter_types': {k: type(v).__name__ for k, v in params.items()},
               'metadata_keys': list(params.get('_meta', {}) or {})}, flush=True)
    original(message)


if __name__ == '__main__':
    with patch.object(lab, 'authorize_rpc', inspect_rpc):
        asyncio.run(round_checks(45052))
