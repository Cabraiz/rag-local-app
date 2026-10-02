"""Export fictional examples and schemas inside the solution container."""
import hashlib
import json
from pathlib import Path
import shutil
import httpx2
from clinic_adk.compiler import AgentSpec, parse_spec, emit

def main():
    root=Path('/artifacts/package')
    root.mkdir(exist_ok=True)
    samples=('request.png','variant.png','unknown.png','injection.png','pii_as_exam.png','blank.png','corrupt.png')
    for name in samples:
        shutil.copyfile(Path('/samples')/name,root/name)
    (root/'agent.schema.json').write_text(json.dumps(AgentSpec.model_json_schema(),indent=2,ensure_ascii=False))
    for name in ('agent.json','agent-variant.json'):
        shutil.copyfile(Path('/app/examples')/name,root/name)
        spec=parse_spec((Path('/app/examples')/name).read_bytes())
        (root/(name+'.py')).write_text(emit(spec))
    with httpx2.Client(timeout=5,trust_env=False) as client:
        response=client.get('http://api:8080/openapi.json')
        response.raise_for_status()
        (root/'openapi.json').write_text(json.dumps(response.json(),indent=2))
    exported=set(samples)|{'agent.schema.json','agent.json','agent-variant.json',
        'agent.json.py','agent-variant.json.py','openapi.json'}
    # Hash only the known outputs, never the old manifest or unrelated files.
    hashes={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sorted(exported)}
    (root/'manifest.json').write_text(json.dumps({'fictional':True,'sha256':hashes},indent=2))
    print(json.dumps({'ok':True,'exported':len(hashes),'path':str(root)}))

if __name__=='__main__':
    main()
