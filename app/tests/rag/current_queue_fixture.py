# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Current-role, isolated real HTTP/SQL/Qdrant file regression; no cloud calls."""
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import time
from uuid import UUID, uuid5

import document_fixture as old
import http_fixture as base

PROJECT = 'rag-local-qa-20261001'
base.BASE = 'http://127.0.0.1:8940'
base.COMPOSE = ['docker', 'compose', '--project-directory', str(APP), '-p', PROJECT]
FILES = ['infrastructure/compose/runtime/compose.yaml', 'infrastructure/compose/runtime/compose.retrieval.yaml', 'infrastructure/compose/runtime/compose.documents.yaml', 'infrastructure/compose/qa/compose.qa.yaml']
for name in FILES:
    base.COMPOSE += ['-f', str(base.ROOT / name)]


def frozen():
    values = base.source_hashes()
    for path in [Path(__file__), base.ROOT/'src/rag_app/retrieval/document_parser.py',
                 *[base.ROOT/name for name in FILES], base.ROOT/'tests/documents/document_fixture.py']:
        values[str(path.relative_to(base.ROOT.parent))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return values


def live():
    import urllib.request
    with urllib.request.urlopen('http://127.0.0.1:8840/health/ready', timeout=5) as response:
        assert response.status == 200, 'LIVE_LAB_NOT_READY'


def script(code):
    prefix = ('import json\nfrom rag_app import corpus, ledger, documents\n'
              'from rag_app.domain import Identity, RequestError\n')
    return json.loads(base.app_python(prefix + code))


def wait_index():
    # API readiness checks SQL; it does not imply the separate index has started.
    end=time.monotonic()+90
    while time.monotonic()<end:
        ready=script("import httpx\ntry:\n r=httpx.get('http://qdrant:6333/readyz',trust_env=False,timeout=3)\n print(json.dumps(r.status_code==200))\nexcept httpx.HTTPError:\n print(json.dumps(False))")
        if ready: return
        time.sleep(1)
    raise AssertionError('QA_INDEX_READINESS_DEADLINE')


def run_round(seed, folder):
    checks = []
    def check(name, condition):
        checks.append(dict(name=name, passed=bool(condition)))
        (folder/f'progress-{seed}.json').write_text(json.dumps(checks, indent=2), encoding='utf8')
        assert condition, name

    bruno = base.http('/v1/lab/session', body={'profile':'bruno'})[1]['token']
    ana = base.http('/v1/lab/session', body={'profile':'ana'})[1]['token']
    marker = 'files'+str(seed)
    # Explicit privileged synthetic seed, NOT a product login/backdoor or public replacement route.
    seed_doc = dict(source_key=marker+'keep', title='Fonte preservada',
                    text='Uma fonte sintetica anterior deve permanecer.', media_type='text/plain')
    value = script('print(json.dumps(corpus.ingest(Identity("demo-a","demo-user"),'+repr([seed_doc])+')))')
    check('privileged_synthetic_seed_ready', value['state']=='READY')
    def catalog():
        status, result = base.http('/v1/lab/corpus', bruno)
        assert status == 200
        return result
    def upload(raw, kind='text/plain', key=None, expected=None, **extra):
        file = dict(source_key=key or marker, title='Arquivo sintetico', media_type=kind,
                    data_base64=base64.b64encode(raw).decode(), valid_until=None)
        file.update(extra)
        body = dict(document=file, expected_generation=catalog()['generation'] if expected is None else expected)
        return base.http('/v1/lab/corpus/files', bruno, body)
    def did(result, key=marker):
        return str(uuid5(UUID(result['release_id']), key))
    def download(document, token=bruno):
        return old.binary('/v1/lab/documents/'+document+'/original', token)

    raw = (marker+' alimentação tem limite de 45 reais por pessoa.\n').encode()
    code, first = upload(raw)
    check('txt_real_ingestion', code==201 and first['state']=='READY')
    first_id = did(first)
    check('earlier_source_preserved', marker+'keep' in {d['source_key'] for d in catalog()['documents']})
    code, payload, headers = download(first_id)
    check('canonical_bytes_sha_and_headers', code==200 and hashlib.sha256(payload).digest()==hashlib.sha256(raw).digest() and old.safe_headers(headers))
    check('client_cannot_publish_files', base.http('/v1/lab/corpus/files', ana,
        dict(expected_generation=catalog()['generation'],document=dict(source_key=marker,title='x',media_type='text/plain',data_base64=base64.b64encode(raw).decode())))[0]==403)
    check('client_cannot_download_operator_original', download(first_id, ana)[0]==403)
    check('unauthenticated_original_denied', download(first_id, None)[0] in (401,403))
    check('legacy_replacement_remains_disabled', base.http('/v1/lab/files', bruno,
        dict(source_key=marker,title='x',media_type='text/plain',data_base64=base64.b64encode(raw).decode()))[0]==409)
    before = catalog()
    check('stale_generation_rejected', upload(raw, expected=before['generation']-1)[0]==409)
    check('conflict_does_not_promote', catalog()==before)
    changed = b'\xef\xbb\xbf'+raw
    code, second = upload(changed)
    second_id = did(second)
    check('same_text_changed_original_versions', code==201 and first['release_id']!=second['release_id'])
    check('historical_bytes_immutable', download(first_id)[1]==raw and download(second_id)[1]==changed)
    markdown = ('# Politica\n'+marker+' markdown prazo: 12 dias uteis.').encode()
    code, md = upload(markdown, 'text/markdown', marker+'md')
    check('markdown_parsed_and_original', code==201 and download(did(md, marker+'md'))[1]==markdown)
    check('original_survives_other_publications', download(did(md))[1]==changed)
    pdf = old.pdf(marker+' pdf valor 99 reais')
    code, pdf_result = upload(pdf, 'application/pdf', marker+'pdf')
    pdf_id = did(pdf_result, marker+'pdf')
    check('pdf_parsed_original_exact', code==201 and download(pdf_id)[1]==pdf)
    current = catalog()
    check('pdf_extracted_text_canonical', any(d['id']==pdf_id and any(marker+' pdf valor 99 reais' in c['quote'] for c in d['chunks']) for d in current['documents']))
    check('all_previous_sources_preserved', {marker+'keep',marker,marker+'md',marker+'pdf'}=={d['source_key'] for d in current['documents']})
    # Current-client workflow, real ADK + lexical/hybrid retrieval, no remote generator.
    status, request = base.http('/v1/requests', ana, {'question':marker+' alimentação limite'}, {'Idempotency-Key':base.key()})
    check('client_request_accepted', status==202)
    result = base.wait_state(request['request_id'], ana, {'SUCCEEDED'})['result']
    check('adk_returns_canonical_evidence', result['kind']=='EXTRACTIVE' and any(c['document_id']==did(pdf_result) and '45 reais' in c['quote'] for c in result['citations']))
    for label, raw_bad, kind in [
        ('scanned',old.pdf(),'application/pdf'), ('encrypted',old.pdf('text',encrypted=True),'application/pdf'),
        ('active',old.pdf('text',active=True),'application/pdf'), ('pages',old.pdf('text',pages=4),'application/pdf'),
        ('malformed',b'%PDF-invalid','application/pdf'), ('utf8',b'\xff','text/plain'),
        ('control',b'bad\x00text','text/plain')]:
        check('reject_'+label, upload(raw_bad, kind, marker+label)[0]==422)
    check('byte_limit', upload(b'x'*8193,key=marker+'huge')[0]==413)
    check('invalid_encoding', upload(b'x',data_base64='!!')[0]==422)
    check('traversal', upload(b'x',key='../escape')[0]==422)
    check('URL_input_rejected', upload(b'x',url='https://example.com')[0]==422)
    check('failed_parses_preserve_corpus', catalog()==current)
    for tenant, actor in [('other-tenant','demo-user'),('demo-a','other-actor')]:
        status = script('try:\n documents.download(Identity('+repr(tenant)+','+repr(actor)+'),'+repr(pdf_id)+')\n print(json.dumps(200))\nexcept RequestError as e:\n print(json.dumps(e.status))')
        check('canonical_sql_isolation_'+tenant+'_'+actor, status==404)
    base.docker('restart','api')
    end = time.monotonic()+45
    while time.monotonic()<end:
        try:
            if base.http('/health/ready')[0]==200: break
        except OSError: pass
        time.sleep(.5)
    check('original_survives_owned_api_restart', download(pdf_id)[1]==pdf)
    check('version_revoke', base.http('/v1/lab/documents/'+first_id+'/revoke',bruno,{})[0]==200)
    check('version_revoke_not_source_revoke', download(first_id)[0]==404 and download(second_id)[1]==changed and download(did(pdf_result))[1]==changed)
    check('source_revoke', base.http('/v1/lab/documents/'+second_id+'/revoke-source',bruno,{})[0]==200)
    check('all_source_versions_revoked', download(first_id)[0]==404 and download(second_id)[0]==404 and download(did(pdf_result))[0]==404)
    check('revoked_source_cannot_resurrect', upload(raw)[0]==409)
    check('no_file_content_in_logs', marker not in base.docker('logs','--no-log-prefix','api','worker').stdout)
    live()
    check('live_lab_preserved', True)
    return dict(seed=seed, checks=checks, passed=True)


def main():
    assert base.BASE.endswith(':8940') and '-p' in base.COMPOSE and PROJECT!='rag-local-v2'
    assert not base.docker('ps','--status','running','-q').stdout.strip(), 'QA_PROJECT_ALREADY_RUNNING'
    live()
    folder = base.ROOT.parent/'eval/runs'/('current-files-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3))
    folder.mkdir(parents=True)
    sources=frozen(); seeds=[secrets.randbits(32) for _ in range(2)]
    contract=dict(project=PROJECT,endpoint=base.BASE,independent_blind=False,cloud_calls=0,seeds=seeds,sources_sha256=sources)
    (folder/'contract.json').write_text(json.dumps(contract,indent=2),encoding='utf8')
    rounds=[]; error=None; images=[]
    try:
        base.docker('up','-d','--wait','--wait-timeout','120')
        wait_index()
        images=base.service_image_ids()
        for seed in seeds:
            assert frozen()==sources and base.service_image_ids()==images, 'SOURCE_OR_IMAGE_CHANGED'
            result=run_round(seed,folder); rounds.append(result)
            (folder/f'round-{seed}.json').write_text(json.dumps(result,indent=2),encoding='utf8')
            print(json.dumps(dict(seed=seed,checks=len(result['checks']),streak=len(rounds))),flush=True)
        assert frozen()==sources and base.service_image_ids()==images, 'SOURCE_OR_IMAGE_CHANGED'
    except Exception as exc:
        error=type(exc).__name__+': '+str(exc)[:500]
    finally:
        stopped=base.docker('stop',check=False).returncode==0
        live()
        complete=not error and len(rounds)==2 and stopped
        receipt=dict(card_id='RAG-05',evidence_type='real_integration',sources_sha256=sources,
            criteria_passed=['formats','versions','revocation','isolation','canonical_download'] if complete else [],
            consecutive_passes=2 if complete else 0,complete=complete,rounds=rounds,error=error,
            images=images,contract=contract,gate_b_passed=False,containers_stopped=stopped,volumes_preserved=True)
        path=folder/'receipt.json';path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
        bug=dict(receipt,card_id='BUG-026',evidence_type='verified_regression',criteria_passed=['reproduction','two_regression_rounds'] if complete else [])
        (folder/'compatibility-receipt.json').write_text(json.dumps(bug,indent=2),encoding='utf8')
    print(json.dumps(dict(receipt=str(path),complete=complete,error=error)),flush=True)
    raise SystemExit(0 if complete else 1)


if __name__=='__main__': main()
