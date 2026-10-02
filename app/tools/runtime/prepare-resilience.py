# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Generate local credentials without ever returning the secret values."""
import os
from pathlib import Path
import secrets
import subprocess

root=(_workspace_root / "app")/'.local'
root.mkdir(exist_ok=True)
for name in ('rabbitmq_password','cache_signing','grafana_admin'):
    path=root/name
    if not path.exists():
        path.write_text(secrets.token_hex(32),encoding='utf8')
    if os.name=='nt':
        account=os.environ.get('USERDOMAIN','')+'\\'+os.environ['USERNAME']
        subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r',account+':F','SYSTEM:F'],capture_output=True,check=True)
config=root/'rabbitmq.conf'
if not config.exists():
    config.write_text('default_user = rag\ndefault_pass = '+(root/'rabbitmq_password').read_text()+'\ndefault_vhost = rag\nloopback_users.guest = true\nlog.console.level = warning\n',encoding='utf8')
    if os.name=='nt':
        subprocess.run(['icacls',str(config),'/inheritance:r','/grant:r',account+':F','SYSTEM:F'],capture_output=True,check=True)
print('Private resilience credentials prepared; values not displayed.')
