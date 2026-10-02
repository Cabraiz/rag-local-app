# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Coverage/structural checks and current evidence mapping. No simulated behavior PASS.

This validates the test plan and architecture, not the unimplemented integrations.
The manifest explicitly reports PENDING and global approval stays false.
"""
import ast
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import secrets
import sys
import subprocess

from review_remote_mcp import edges, gateway_safe

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parent.parent
SEGMENTS={'python_adk','rag','mcp_api','vertex_gemini','evaluation','deepagents','production','frontend'}


def images_match(expected,current):
    def normalized(values): return {v.strip().removeprefix('sha256:') for v in values if v.strip()}
    return bool(expected and current) and normalized(expected)==normalized(current)


def current_images(scope):
    if scope=='synthetic_extractive_HTTP_PostgreSQL_Qdrant_ADK_graph':
        command=['docker','compose', '--project-directory', str(APP),'-f',str(PROJECT/'app/infrastructure/compose/runtime/compose.yaml'),'-f',str(PROJECT/'app/infrastructure/compose/runtime/compose.retrieval.yaml'),'images','-q']
    else:
        command=['docker','image','inspect','rag-local-backend:0.1.0','rag-local-frontend:0.1.0','--format','{{.Id}}']
    result=subprocess.run(command,capture_output=True,text=True,timeout=30,shell=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    return result.stdout.strip().splitlines() if result.returncode==0 else []


def catalog(text):
    return list(csv.DictReader(text.splitlines(),delimiter='\t'))


def complete(rows):
    keys=('id','segment','cenario','esperado','proibido','menor_prova','runner','check')
    return bool(rows) and len({r['id'] for r in rows})==len(rows) and \
        {r['segment'] for r in rows}==SEGMENTS and \
        all(all(r.get(k,'').strip() for k in keys) and r['runner'] in
            ('pending','http_fixture','segment_fixture','rag_fixture','static') for r in rows)


def core_boundary(source):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node,ast.Import):
            names=[n.name for n in node.names]
        elif isinstance(node,ast.ImportFrom):
            names=[node.module or '']
        else: continue
        if any(n.split('.')[0] in {'fastapi','google','deepagents','psycopg','sqlalchemy','httpx','qdrant_client'} for n in names):
            return False
        if isinstance(node,ast.ImportFrom) and node.level and node.module not in ('domain',):
            return False
    return True


def checks():
    rows=catalog((ROOT/'segment-cases.tsv').read_text(encoding='utf8'))
    dsl=(ROOT/'application-eraser.txt').read_text(encoding='utf8')
    graph=edges(dsl)
    app=(ROOT/'application.md').read_text(encoding='utf8')
    policy=json.loads((ROOT/'production-policy.json').read_text(encoding='utf8'))
    sources=[(PROJECT/'app/src/rag_app'/name).read_text(encoding='utf8') for name in ('domain.py','application.py')]
    incomplete=[dict(r,menor_prova='') if i==0 else r for i,r in enumerate(rows)]
    duplicate=rows+[rows[0]]
    readback=(ROOT/'application-eraser-segments-readback.txt').read_text(encoding='utf8')
    rag=(ROOT/'rag-python-segments-readback.txt').read_text(encoding='utf8')
    def rag_providers(text):
        connections=set(re.findall(r'^([A-Za-z0-9]+)\s+>\s+([A-Za-z0-9]+)',text,re.M))
        return all(e in connections for e in (('K1','K2'),('K2','K3'),('K3','KGH'),
            ('K3','KAT'),('KGH','K5'),('KAT','K5')))
    return {
        'catalog_has_all_segments_and_complete_oracles':complete(rows),
        'negative_missing_proof_is_detected':not complete(incomplete),
        'negative_duplicate_case_is_detected':not complete(duplicate),
        'core_import_boundary':all(core_boundary(s) for s in sources),
        'negative_sdk_in_core_is_detected':not core_boundary(sources[0]+'\nimport google.adk\n'),
        'gateway_graph':gateway_safe(graph),
        'negative_direct_provider_bypass_detected':not gateway_safe(graph+[('ADK','GH')]),
        'orchestrator_contract': 'Orch [label:' in dsl and ('UseCases','Orch') in graph and ('Orch','Ports') in graph and 'Coordenacao da execucao' in dsl,
        'orchestration_ownership_documented':all(t in app for t in ('WorkflowPort','TaskPort','FinalizeRequest','proposta privada')),
        'eraser_app_readback_matches_local':dsl.replace('\r','').strip()==readback.replace('\r','').strip(),
        'rag_orchestrator_and_providers_aligned':rag_providers(rag) and 'C1 Orquestrador ADK' in rag and 'request worker detem lease/fence' in rag,
        'negative_missing_rag_provider_detected':not rag_providers(rag.replace('K3 > KAT: Leitura autorizada','')),
        'rag_remote_profile_has_no_stdio_substitute':'neste perfil somente remoto online' in rag and 'stdio usa credencial/processo' not in rag,
        'remote_disabled':all(p['enabled'] is False for p in policy['remote_mcp']['providers'].values()),
        'frozen_contract':all(t in (ROOT/'segment-acceptance.md').read_text(encoding='utf8') for t in ('PENDING','Não há auditor independente','zera','produção')),
        'pending_cannot_be_mapped_to_pass':len([r for r in rows if r['runner']=='pending'])>0,
        'image_id_formats_normalized':images_match(['sha256:abc'],['abc']),
        'negative_stale_image_ids_detected':not images_match(['sha256:abc'],['sha256:def']),
    }


def main():
    files=[ROOT/n for n in ('segment-cases.tsv','segment-acceptance.md','review_segments.py',
        'application.md','application-eraser.txt','production-policy.json','review_remote_mcp.py',
        'application-eraser-segments-readback.txt','rag-python-segments-readback.txt')]
    files += [PROJECT/'app/src/rag_app'/n for n in ('domain.py','application.py')]
    frozen={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    folder=PROJECT/'eval/runs'/('segments-design-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3))
    folder.mkdir(parents=True)
    seeds=[secrets.randbits(32),secrets.randbits(32)]
    (folder/'contract.json').write_text(json.dumps(dict(sha256=frozen,seeds=seeds,
        scope='catalog_and_architecture_only',independent_blind=False),indent=2),encoding='utf8')
    rounds=[]; streak=0
    for seed in seeds:
        items=list(checks().items()); random.Random(seed).shuffle(items)
        ok=all(v for _,v in items)
        streak=streak+1 if ok else 0
        rounds.append(dict(seed=seed,checks=[dict(name=k,passed=v) for k,v in items],passed=ok,consecutive_passes=streak))
        if not ok: break
    rows=catalog((ROOT/'segment-cases.tsv').read_text(encoding='utf8'))
    receipts=[]
    for filename in sys.argv[1:]:
        runtime=json.loads(Path(filename).read_text(encoding='utf8'))
        valid=bool(runtime['two_consecutive_real_slice_passes'] and runtime['error'] is None and
            runtime['containers_stopped'] and len(runtime['rounds'])>=2 and
            all(rd.get('passed') is True for rd in runtime['rounds'][-2:]))
        # Reject stale evidence even if a receipt claims PASS.
        for name,digest in runtime['contract']['sha256'].items():
            path=PROJECT/name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest: valid=False
        try:
            if not images_match(runtime['contract']['images'],current_images(runtime['contract']['scope'])): valid=False
        except (OSError,subprocess.TimeoutExpired): valid=False
        receipts.append(dict(path=filename,valid=valid,runtime=runtime))
    valid_runtime=any(r['valid'] for r in receipts)
    behavior=[]
    for row in rows:
        real=row['runner'] in ('http_fixture','segment_fixture','rag_fixture')
        exercised=False
        if real and valid_runtime:
            for evidence in receipts:
                if not evidence['valid']: continue
                per_round=[]
                for rd in evidence['runtime']['rounds'][-2:]:
                    names={c['name']:c['passed'] for c in rd['checks']}
                    names.update({c['name']:c['pass'] for c in rd.get('basic_checks',[])})
                    per_round.append(all(names.get(name) is True for name in row['check'].split('|')))
                exercised=exercised or (all(per_round) and len(per_round)==2)
        behavior.append(dict(row,status='PASS_REAL_SLICE' if exercised else
            'STRUCTURAL_ONLY' if row['runner']=='static' else 'PENDING'))
    unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in frozen.items())
    counts=Counter(r['status'] for r in behavior)
    receipt=dict(rounds=rounds,frozen_unchanged=unchanged,design_two_consecutive_passes=streak>=2 and unchanged,
        catalog_cases=len(rows),segments=dict(Counter(r['segment'] for r in rows)),status_counts=dict(counts),
        evidence=behavior,current_runtime_evidence_valid=valid_runtime,
        runtime_receipts=[dict(path=r['path'],valid=r['valid']) for r in receipts],all_segments_passed=False,
        gate_b_passed=False,independent_blind=False)
    (folder/'receipt.json').write_text(json.dumps(receipt,indent=2,ensure_ascii=False),encoding='utf8')
    print(json.dumps(dict(receipt=str(folder/'receipt.json'),design_streak=streak,cases=len(rows),
        status_counts=dict(counts),failures=[c['name'] for rd in rounds for c in rd['checks'] if not c['passed']],all_segments_passed=False)))
    raise SystemExit(0 if streak>=2 and unchanged else 1)


if __name__=='__main__': main()
