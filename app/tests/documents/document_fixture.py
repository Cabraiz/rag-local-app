# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Frozen synthetic end-to-end file contract; same-author adversarial, not independent QA."""
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import secrets
import time
import urllib.error
import urllib.request
from uuid import UUID, uuid5

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, DecodedStreamObject, NameObject, NumberObject
import rag_fixture as rag
import http_fixture as base

base.COMPOSE += ['-f', str(base.ROOT/'infrastructure/compose/runtime/compose.documents.yaml')]
ACTIVE_FOLDER = None


def upload_value(token,value):
    # Same immutable manifest, fixed 3-attempt protocol, only an explicit retryable index error.
    # Never accepts a wrong version, authentication failure, parse error or arbitrary 503.
    for attempt in range(3):
        response=base.http('/v1/lab/files',token,value)
        if response[0]!=503 or response[1].get('code')!='INDEX_UNAVAILABLE':
            return response
        if attempt<2:
            time.sleep(2)
    return response


def frozen():
    result = rag.frozen()
    for path in (base.ROOT/'tests/documents/document_fixture.py', base.ROOT/'infrastructure/compose/runtime/compose.documents.yaml',
                 base.ROOT.parent/'docs/architecture/contracts/document-contract.json',
                 base.ROOT/'infrastructure/dependencies/backend/requirements.in', base.ROOT/'pyproject.toml'):
        result[str(path.relative_to(base.ROOT.parent))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def pdf(text=None, encrypted=False, active=False, pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=300, height=200)
        if text:
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
                DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(('BT /F1 10 Tf 10 100 Td ('+text+') Tj ET').encode('ascii'))
            page[NameObject('/Contents')] = writer._add_object(stream)
    if encrypted:
        writer.encrypt('synthetic-fixture-password')
    if active:
        writer._root_object[NameObject('/OpenAction')] = DictionaryObject({NameObject('/S'): NameObject('/JavaScript'), NameObject('/JS'): NameObject('/synthetic')})
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def binary(path, token=None):
    request = urllib.request.Request(base.BASE+path, headers={'Authorization':'Bearer '+token} if token else {})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers)


def safe_headers(headers):
    values={name.lower():value for name,value in headers.items()}
    return (values.get('x-content-type-options')=='nosniff'
            and values.get('cache-control')=='no-store'
            and values.get('content-disposition','').startswith('attachment;'))


def wait_api_ready():
    end=time.monotonic()+30
    while time.monotonic()<end:
        try:
            code,value=base.http('/health/ready')
            if code==200 and value.get('mode')=='lab':
                return
        except (OSError,ValueError):
            pass
        time.sleep(.3)
    raise AssertionError('API restart readiness exceeded 30 seconds')


def run_round(seed):
    checks=[]
    def check(name, ok):
        checks.append(dict(name=name, passed=bool(ok)))
        (ACTIVE_FOLDER/f'progress-{seed}.json').write_text(json.dumps(checks,indent=2),encoding='utf8')
        if not ok:
            raise AssertionError(name)
    a=base.http('/v1/lab/session',body={'tenant':'demo-a'})[1]['token']
    b=base.http('/v1/lab/session',body={'tenant':'demo-b'})[1]['token']
    marker='file'+str(seed)
    def upload(raw, kind='text/plain', key=None, **extra):
        value=dict(source_key=key or marker,title='Documento sintetico',media_type=kind,
                   data_base64=base64.b64encode(raw).decode(),valid_until=None)
        value.update(extra)
        return upload_value(a,value)
    def did(value, key):
        return str(uuid5(UUID(value['release_id']), key))
    def original(document, token=a):
        return binary('/v1/lab/documents/'+document+'/original',token)
    def answer(query):
        code,value=base.http('/v1/requests',a,{'question':query},{'Idempotency-Key':base.key()})
        assert code==202
        return base.wait_state(value['request_id'],a,{'SUCCEEDED'})

    raw=(marker+' orçamento: café e açúcar.\n').encode('utf8')
    code,value=upload(raw)
    check('utf8_txt_real_ingest',code==201 and value['state']=='READY')
    first=did(value,marker)
    code,again=upload(raw)
    check('same_original_same_release',code==201 and again['release_id']==value['release_id'] and again['replayed'])
    code,payload,headers=original(first)
    check('canonical_original_exact_bytes',code==200 and payload==raw)
    check('safe_download_headers',safe_headers(headers))
    result=answer(marker)['result']
    check('adk_exact_citation_from_parsed_file',result['kind']=='EXTRACTIVE' and result['citations'][0]['document_id']==first and result['citations'][0]['quote']==raw.decode())
    check('unauthenticated_download_denied',original(first,None)[0] in (401,403))
    check('foreign_tenant_download_denied',original(first,b)[0]==404)
    check('foreign_tenant_revoke_denied',base.http('/v1/lab/documents/'+first+'/revoke-source',b,{})[0]==404)
    changed=b'\xef\xbb\xbf'+raw
    code,v2=upload(changed)
    check('same_text_changed_original_new_release',code==201 and v2['release_id']!=value['release_id'])
    second=did(v2,marker)
    check('historical_original_remains_immutable',original(first)[1]==raw and original(second)[1]==changed)
    base.docker('restart','api')
    wait_api_ready()
    check('original_survives_container_restart',original(second)[1]==changed)
    check('legacy_version_revoke_commits',base.http('/v1/lab/documents/'+first+'/revoke',a,{})[0]==200)
    check('legacy_version_revoke_does_not_revoke_other_versions',original(first)[0]==404 and original(second)[1]==changed)
    code,v3=upload(raw)
    check('legacy_version_revoke_allows_explicit_new_version',code==201 and v3['release_id'] not in (value['release_id'],v2['release_id']))
    third=did(v3,marker)
    count=rag.script("with ledger.connect() as db:\n row=db.execute('SELECT original_hash,original_bytes FROM corpus_documents WHERE id=%s',("+repr(second)+",)).fetchone()\n print(json.dumps(row))")
    check('canonical_blob_metadata',count==dict(original_hash=hashlib.sha256(changed).hexdigest(),original_bytes=len(changed)))
    collection=rag.script("print(json.dumps(corpus.QdrantAdapter().call('POST','/collections/'+corpus.collection("+repr(v2['release_id'])+")+ '/points/scroll',{'limit':16,'with_payload':True})))")
    check('vector_payload_has_no_document_content',all(set(p['payload'])=={'chunk_id','tenant','actor','release_id'} for p in collection['result']['points']))
    check('source_revoke_commits',base.http('/v1/lab/documents/'+second+'/revoke-source',a,{})[0]==200)
    check('all_historical_versions_download_revoked',original(first)[0]==404 and original(second)[0]==404 and original(third)[0]==404)
    check('read_revalidates_existing_citations',answer(marker)['result']['kind']=='ABSTAIN')
    check('revoked_source_cannot_be_reuploaded',upload(raw)[0]==409)
    check('plain_text_ingest_cannot_bypass_revocation',base.http('/v1/lab/corpus/releases',a,{'documents':[dict(source_key=marker,title='Documento',text=marker,media_type='text/plain') ]})[0]==409)
    markdown=('# Politica\n'+marker+' markdown orçamento: 88 reais.').encode()
    code,md=upload(markdown,'text/markdown',marker+'md')
    check('markdown_real_ingest_and_original',code==201 and original(did(md,marker+'md'))[1]==markdown)
    check('markdown_rag_cites_literal_data',answer(marker+' markdown')['result']['kind']=='EXTRACTIVE')
    pdf_raw=pdf(marker+' pdf valor 99 reais')
    code,pdf_release=upload(pdf_raw,'application/pdf',marker+'pdf')
    check('pdf_real_ingest_and_original',code==201 and original(did(pdf_release,marker+'pdf'))[1]==pdf_raw)
    check('pdf_actual_text_retrieved',answer(marker+' pdf')['result']['kind']=='EXTRACTIVE')
    expired=upload(b'expired synthetic','text/plain',marker+'expired',valid_until='2000-01-01T00:00:00Z')
    check('expired_original_not_downloadable',expired[0]==201 and original(did(expired[1],marker+'expired'))[0]==404)
    check('scanned_pdf_requires_ocr_not_hallucinated',upload(pdf(),'application/pdf',marker+'scan')[0]==422)
    check('encrypted_pdf_rejected',upload(pdf('synthetic',encrypted=True),'application/pdf',marker+'enc')[0]==422)
    check('active_pdf_rejected',upload(pdf('synthetic',active=True),'application/pdf',marker+'active')[0]==422)
    check('too_many_pdf_pages_rejected',upload(pdf('synthetic',pages=4),'application/pdf',marker+'pages')[0]==422)
    check('malformed_pdf_rejected',upload(b'%PDF-invalid','application/pdf',marker+'badpdf')[0]==422)
    check('invalid_utf8_rejected',upload(b'\xff\xff','text/plain',marker+'badutf')[0]==422)
    check('control_character_rejected',upload(b'bad\x00text','text/plain',marker+'badcontrol')[0]==422)
    check('unsupported_media_type_rejected',upload(b'<html>','text/html',marker+'html')[0]==422)
    check('path_traversal_source_rejected',upload(b'synthetic','text/plain','../escape')[0]==422)
    check('URL_input_not_supported',upload(b'synthetic','text/plain',marker+'url',url='https://example.com')[0]==422)
    check('bad_base64_rejected',upload(b'synthetic',data_base64='!!')[0]==422)
    check('raw_byte_limit_enforced',upload(b'x'*8193,key=marker+'huge')[0]==413)
    # Controlled corruption only of our own newly created synthetic object. Restore in finally.
    code,unique=upload((marker+' integrity').encode(),key=marker+'integrity')
    check('integrity_fixture_created',code==201)
    unique_doc=did(unique,marker+'integrity')
    digest=hashlib.sha256((marker+' integrity').encode()).hexdigest()
    try:
        base.app_python("from pathlib import Path; Path('/objects/'+"+repr(digest)+").write_bytes(b'corrupted')")
        check('corrupted_original_fails_closed',original(unique_doc)[0]==503)
    finally:
        base.app_python("from pathlib import Path; Path('/objects/'+"+repr(digest)+").write_bytes("+repr((marker+' integrity').encode())+")")
    check('restored_fixture_download_integrity',original(unique_doc)[1]==(marker+' integrity').encode())
    logs=base.docker('logs','--no-log-prefix','api','worker').stdout
    check('no_uploaded_content_in_logs',marker not in logs)
    return dict(seed=seed,checks=checks,passed=True)


def main():
    global ACTIVE_FOLDER
    if base.docker('ps','--status','running','-q').stdout.strip():
        raise SystemExit('Project already running: refuse ownership')
    folder=base.ROOT.parent/'eval/runs'/('documents-real-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3))
    folder.mkdir(parents=True); ACTIVE_FOLDER=folder
    sources=frozen(); seeds=[secrets.randbits(32) for _ in range(2)]
    contract=dict(scope='synthetic_HTTP_PostgreSQL_Qdrant_ADK_original_volume_PDF_parser',independent_blind=False,
                  seeds=seeds,sources_sha256=sources,oracle_frozen_before_inputs=True)
    (folder/'contract.json').write_text(json.dumps(contract,indent=2),encoding='utf8')
    rounds=[]; streak=0; error=None
    try:
        base.docker('up','-d','--wait','--wait-timeout','120')
        rag.wait_index_ready('documents_initial'); pinned=rag.images(); contract['images']=pinned
        for seed in seeds:
            assert frozen()==sources and rag.images()==pinned,'Changed sources/images reset streak'
            result=run_round(seed); rounds.append(result); streak+=1
            (folder/f'round-{seed}.json').write_text(json.dumps(result,indent=2),encoding='utf8')
            print(json.dumps(dict(seed=seed,checks=len(result['checks']),streak=streak)),flush=True)
        assert frozen()==sources and rag.images()==pinned,'Changed sources/images reset streak'
    except Exception as exc:
        error=str(exc); streak=0
    finally:
        stopped=base.docker('stop',check=False)
        receipt=dict(card_id='RAG-05',evidence_type='real_integration',contract=contract,sources_sha256=sources,
            rounds=rounds,error=error,consecutive_passes=streak,complete=streak==2 and not error,
            criteria_passed=['formats','versions','revocation','isolation','canonical_download'] if streak==2 else [],
            gate_b_passed=False,containers_stopped=stopped.returncode==0,volumes_preserved=True)
        (folder/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps(dict(receipt=str(folder/'receipt.json'),error=error,streak=streak)),flush=True)
    raise SystemExit(0 if streak==2 and not error else 1)


if __name__=='__main__':
    main()
