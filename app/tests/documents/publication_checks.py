# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Deterministic boundary checks, no DB writes or external network."""
import hashlib
import sys
from pathlib import Path
sys.path.insert(0,str((_workspace_root / "app")/'src'))
from rag_app.publication import reconstruct
from rag_app.lab_roles import resource_identity
from rag_app.domain import RequestError

count=0
for length in (1,80,519,520,521,599,600,601,1040,1041,8192):
    text=('áβ\nDoc\t'*1200)[:length]
    chunks=[{'ordinal':i,'quote':text[start:start+600]} for i,start in enumerate(range(0,len(text),520))]
    digest=hashlib.sha256(text.encode()).hexdigest()
    assert reconstruct(chunks,digest)==text;count+=1
    broken=[dict(c) for c in chunks];broken[0]['quote']='X'+broken[0]['quote'][1:]
    try:reconstruct(broken,digest);raise AssertionError('CORRUPTION_ACCEPTED')
    except RequestError as error:assert error.code=='CORPUS_TEXT_INTEGRITY_FAILED';count+=1
for claims in ({'sub':'demo-user','role':'operator','tenant':'demo-a'},
               {'sub':'ana','role':'operator','tenant':'demo-a'},
               {'sub':'bruno','role':'operator','tenant':'demo-b'},
               {'sub':'bruno','tenant':'demo-a'}):
    try:resource_identity(claims);raise AssertionError('PROFILE_ESCALATION')
    except ValueError:count+=1
for sub,role,other in [('ana','client','operator'),('bruno','operator','client')]:
    claims=dict(sub=sub,role=role,tenant='demo-a')
    assert resource_identity(claims,role).actor=='demo-user';count+=1
    try:resource_identity(claims,other);raise AssertionError('ROLE_ESCALATION')
    except PermissionError:count+=1
print({'passed':True,'checks':count,'scope':'text reconstruction and fixed lab roles'})
