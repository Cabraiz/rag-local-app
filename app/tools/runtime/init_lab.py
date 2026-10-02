# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Generate secrets once; never display them or overwrite existing credentials."""
from pathlib import Path
import secrets

folder = (_workspace_root / "app")/'.local'
folder.mkdir(exist_ok=True)
for name in ('db_admin','db_app','jwt_lab'):
    path = folder/name
    if not path.exists():
        with path.open('x',encoding='utf8') as file:
            file.write(secrets.token_urlsafe(48)+'\n')
print('Lab secret files ready. Values are not printed. Production remains disabled.')
