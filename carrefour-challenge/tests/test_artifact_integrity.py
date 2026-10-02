"""A validation check must protect the exact bytes executed, not a later reread."""
import asyncio
import hashlib
import importlib.util
from pathlib import Path
import types
from uuid import uuid4
import pytest
from clinic_adk.cli import execute
from clinic_adk.compiler import parse_spec,emit
from clinic_adk.errors import SafeError
import clinic_adk.cli as cli_module
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

def test_replacing_artifact_after_validation_cannot_execute_new_code(tmp_path,monkeypatch):
    from clinic_adk.runtime import Runtime
    spec=parse_spec(Path('/app/examples/agent.json').read_bytes())
    source=emit(spec).encode()
    path=tmp_path/'race.py'
    path.write_bytes(source)
    original_module=types.ModuleType
    original_factory=importlib.util.module_from_spec
    called=[]
    def poison(*args,**kwargs):
        name=args[0] if isinstance(args[0],str) else args[0].name
        if name!='generated_clinic_agent':
            return original_module(*args,**kwargs) if isinstance(args[0],str) else original_factory(*args,**kwargs)
        called.append(True)
        path.write_text('raise RuntimeError("UNVERIFIED_SOURCE_EXECUTED")\n')
        if isinstance(args[0],str):
            return original_module(*args,**kwargs)
        return original_factory(*args,**kwargs)
    monkeypatch.setattr(cli_module,'ModuleType',poison,raising=False)
    monkeypatch.setattr(importlib.util,'module_from_spec',poison)
    async def offline_step(self,kind,node_input):
        self.stages.append(kind)
        return {'result':{'unit_race_probe':True}} if kind=='format' else {}
    monkeypatch.setattr(Runtime,'step',offline_step)
    value=asyncio.run(execute(spec,path,'request.png',str(uuid4())))
    assert called and value['unit_race_probe']
    assert value['generated_source_sha256']==hashlib.sha256(source).hexdigest()
    assert path.read_bytes()!=source

def test_oversize_artifact_does_not_use_unbounded_read(tmp_path,monkeypatch):
    spec=parse_spec(Path('/app/examples/agent.json').read_bytes())
    path=tmp_path/'large.py'
    with path.open('wb') as stream:
        stream.truncate(10_000_000)
    original=Path.read_bytes
    def guarded(self):
        if self==path:
            raise AssertionError('UNBOUNDED_READ_USED')
        return original(self)
    monkeypatch.setattr(Path,'read_bytes',guarded)
    with pytest.raises(SafeError):
        asyncio.run(execute(spec,path,'request.png',str(uuid4())))
