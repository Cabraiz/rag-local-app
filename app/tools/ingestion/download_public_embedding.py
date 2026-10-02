# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Download only pinned public ONNX/tokenizer assets; never reads auth or documents."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT=(_workspace_root / "app")
DEST=ROOT/'semantic/model'
REPO='qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q'
REV='faf4aa4225822f3bc6376869cb1164e8e3feedd0'
FILES={'README.md':('git','e2ebbcd42a609cdd56c946747a18f9c5fecbf454',855),
    'config.json':('git','5b496dbbbe502a10e2d64525481c6f444d125403',673),
    'model_optimized.onnx':('sha256','634d0f66c29dc934c8fa72b8a4fe91dd4d420a22f1d82a241058d4316e659a99',235052644),
    'special_tokens_map.json':('git','b1879d702821e753ffe4245048eee415d54a9385',964),
    'tokenizer.json':('sha256','fa685fc160bbdbab64058d4fc91b60e62d207e8dc60b9af5c002c5ab946ded00',17083009),
    'tokenizer_config.json':('git','6af3e8bba20e4425103afb1ae3dee3cacdbe7afb',1416)}

def digest(path,kind):
    result=hashlib.sha256() if kind=='sha256' else hashlib.sha1()
    if kind=='git': result.update(('blob '+str(path.stat().st_size)+'\0').encode())
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1048576),b''): result.update(chunk)
    return result.hexdigest()

def main():
    DEST.mkdir(parents=True,exist_ok=True)
    assert DEST.resolve().is_relative_to(ROOT.resolve())
    manifest={}
    for name,(kind,expected,size) in FILES.items():
        target=DEST/name
        assert target.resolve().is_relative_to(DEST.resolve())
        if not target.exists():
            part=target.with_suffix(target.suffix+'.part')
            if part.exists(): raise RuntimeError('Existing_partial_asset_requires_review')
            used=0
            # No cookies, auth headers, proxy configuration or machine credentials.
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open('https://huggingface.co/'+REPO+'/resolve/'+REV+'/'+name,timeout=30) as response,part.open('xb') as stream:
                while chunk:=response.read(1048576):
                    used+=len(chunk)
                    if used>size: raise RuntimeError('Public_asset_size_mismatch')
                    stream.write(chunk)
            if used!=size or digest(part,kind)!=expected: raise RuntimeError('Public_asset_hash_mismatch')
            part.replace(target)
        if target.stat().st_size!=size or digest(target,kind)!=expected: raise RuntimeError('Existing_public_asset_mismatch')
        manifest[name]=dict(bytes=size,sha256=digest(target,'sha256'))
        print(json.dumps({'verified_public_asset':name,'bytes':size}),flush=True)
    (DEST/'manifest.json').write_text(json.dumps(dict(repository=REPO,revision=REV,license='Apache-2.0',files=manifest),indent=2),encoding='utf8')

if __name__=='__main__': main()
