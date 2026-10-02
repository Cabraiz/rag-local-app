# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Redis PTTL: -2 is an expired scan snapshot; -1 is NEVER permitted."""


def valid_pttl(milliseconds):
    return type(milliseconds) is int and (milliseconds == -2 or 0 <= milliseconds <= 300000)
