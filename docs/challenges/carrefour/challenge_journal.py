"""Scoped challenge journal, preserving every unrelated card and receipt."""
import argparse
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT=Path('D:/RAG-Local')
spec=importlib.util.spec_from_file_location('rag_card_queue',ROOT/'app/tools/cards/card_queue.py')
queue=importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)
DEFINITION=ROOT/'docs/challenges/carrefour/cards.json'
BUGS=[
 ('CF-03','Nomes de campos nao autorizados vazam PII na validacao','test_unknown_field_name_is_not_a_pii_leak; test_api_openapi_and_pii_safe_validation'),
 ('CF-08','ADK MCP usa resultado snake_case e campos opcionais sem output','test_real_ocr_through_adk_mcp_sse; test_cli_transpiles_then_runs_actual_generated_agent'),
 ('CF-11','Fronteiras adversariais aceitam valores ou descartam exames indevidamente','test_version_is_integer_not_truthy; test_exam_cannot_hide_in_header_allowlist; test_api_rejects_oversized_body_before_json_processing; test_api_rejects_duplicate_fields; test_untrusted_result_shape_is_always_safe_error')
]
def begin(db):
    running=db.execute("SELECT id FROM cards WHERE status='RUNNING'").fetchone()
    if running and running['id']=='RAG-07':
        queue.block(db,'RAG-07','USER_FOCUS_CARREFOUR: trabalho anterior preservado, nao concluido; usuario pediu todos os CF nesta tarefa.')
    elif running and running['id']!='CF-01':
        raise ValueError('UNRELATED_WRITER_ACTIVE')
    row=db.execute("SELECT status FROM cards WHERE id='CF-01'").fetchone()
    if row['status']=='QUEUED':
        db.execute("UPDATE cards SET status='RUNNING',reason=NULL WHERE id='CF-01'")
        queue.event(db,'CF-01','START','User explicitly selected scoped CF implementation; prior RAG card deferred, not completed.')
def bug(db,index,proof_path):
    row=BUGS[index]
    return queue.append_bug(db,row[0],row[1],row[2]+'; proof: '+str(proof_path))

def approve(db,card,receipt):
    row=db.execute('SELECT status FROM cards WHERE id=?',(card,)).fetchone()
    if row['status']=='DONE':
        raise ValueError('DO_NOT_REWRITE_OLD_APPROVAL')
    active=db.execute("SELECT id FROM cards WHERE status='RUNNING'").fetchone()
    if active and active['id']!=card:
        queue.block(db,active['id'],'SCOPED_DEPENDENCY_REVIEW: temporarily verify child regression first')
    if row['status']=='QUEUED':
        db.execute("UPDATE cards SET status='RUNNING',reason=NULL WHERE id=?",(card,))
        queue.event(db,card,'START','Scoped dependency order selected by current user request')
    elif row['status'] in ('BLOCKED','NEEDS_FIX'):
        queue.resume(db,card,'Current frozen full regression passed twice; dependencies checked')
    queue.complete(db,card,receipt)

def finish(db,report_path):
    report_path=Path(report_path).resolve()
    if not report_path.is_relative_to(ROOT/'eval'):
        raise ValueError('RECEIPT_OUTSIDE_EVAL')
    report=json.loads(report_path.read_text())
    if report['complete'] is not True or report['consecutive_passes']!=2 or len(report['rounds'])!=2:
        raise ValueError('TWO_ACTUAL_ROUNDS_REQUIRED')
    for run in report['rounds']:
        if run['exit_code']!=0: raise ValueError('ROUND_FAILED')
        xml=ET.parse(report_path.parent/run['junit']).getroot()
        suites=[xml] if xml.tag=='testsuite' else list(xml)
        if sum(int(s.attrib.get('tests',0)) for s in suites)<100:
            raise ValueError('MISSING_FULL_SUITE')
        if any(int(s.attrib.get(k,0)) for s in suites for k in ('failures','errors','skipped')):
            raise ValueError('NOT_ALL_TESTS_PASSED')
        if report.get('checks',{}).get('actual_playwright_offline_swagger'):
            browser=run.get('browser',{})
            if browser.get('exit_code')!=0 or browser.get('tests',0)<18:
                raise ValueError('REAL_BROWSER_ROUND_REQUIRED')
            proof=json.loads((report_path.parent/browser['report']).read_text())
            if (proof.get('complete') is not True or proof.get('failures')!=0
                    or proof.get('skipped')!=0 or proof.get('no_response_mocks') is not True
                    or proof.get('all_contexts_external_blocked') is not True
                    or len(proof.get('cases',[]))!=proof.get('tests')
                    or any(c.get('passed') is not True or c.get('external_origins') for c in proof['cases'])):
                raise ValueError('NOT_ALL_BROWSER_TESTS_PASSED')
    for name,digest in report['sources_sha256'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('SOURCE_CHANGED')
    if not all(report['checks'].values()):
        raise ValueError('UNPROVEN_EXTRA_CHECK')
    cards=json.loads(DEFINITION.read_text())['cards']
    bugs=db.execute("SELECT * FROM cards WHERE parent LIKE 'CF-%' AND status!='DONE' ORDER BY seq").fetchall()
    targets=[{'id':row['id'],'evidence_type':row['evidence_type'],'criteria':json.loads(row['criteria'])} for row in bugs]+cards
    completed=[]
    for card in targets:
        for dependency in card.get('dependencies',[]):
            if db.execute('SELECT status FROM cards WHERE id=?',(dependency,)).fetchone()['status']!='DONE':
                raise ValueError('UNVERIFIED_DEPENDENCY:'+dependency)
        value={**report,'card_id':card['id'],'evidence_type':card['evidence_type'],
               'criteria_passed':card['criteria'],'proof_boundary':'same-author scoped regression, not independent audit'}
        path=report_path.parent/(card['id']+'.json')
        path.write_text(json.dumps(value,indent=2))
        approve(db,card['id'],path)
        completed.append(card['id'])
    return completed

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['begin','bug','finish'])
    parser.add_argument('--bug-index',type=int)
    parser.add_argument('--proof')
    args=parser.parse_args()
    with closing(queue.connect()) as db,db:
        db.execute('BEGIN IMMEDIATE')
        if args.action=='begin': result=begin(db)
        elif args.action=='bug': result=bug(db,args.bug_index,args.proof)
        else: result=finish(db,args.proof)
    with closing(queue.connect()) as db:
        projection=queue.projection(db)
    print(json.dumps({'result':result,'journal':projection}))
if __name__=='__main__':main()
