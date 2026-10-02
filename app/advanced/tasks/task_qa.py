"""Executable scoped SDK checks, including real tool calls and cancellation."""
import json
import logging
from pathlib import Path
import secrets
import sqlite3
from uuid import uuid4

logging.disable(logging.CRITICAL)
from google.genai import types
from free_model import Budget,FreeChatModel,LabBlocked,invoke
from task_lab import DeepAgentsTaskAdapter


def run_round(seed,progress):
    checks=[]
    def check(name,ok):
        checks.append(dict(name=name,passed=bool(ok)))
        progress(dict(seed=seed,checks=checks,complete=False))
        assert ok,name
    # Import both actual SDKs in the isolated image, without their paid defaults.
    import deepagents,deepeval
    check('both_real_sdks_import',deepagents is not None and deepeval is not None)
    def execute(command:str)->str:
        """Forbidden shell command."""
        return command
    budget=Budget()
    try:FreeChatModel(budget).bind_tools([execute]);denied=False
    except LabBlocked as e:denied=str(e)=='TASK_TOOL_SURFACE_NOT_AUTHORIZED'
    check('unauthorized_tool_rejected_before_model',denied and budget.calls==0)
    budget=Budget(max_calls=0)
    try:budget.reserve();denied=False
    except LabBlocked as e:denied=str(e)=='TASK_MODEL_BUDGET'
    check('model_budget_no_call',denied and budget.calls==0)
    budget=Budget(max_reads=0)
    try:budget.read();denied=False
    except LabBlocked as e:denied=str(e)=='TASK_TOOL_BUDGET'
    check('tool_budget_no_read',denied and budget.reads==0)
    budget=Budget()
    try:invoke(budget,[types.Content(role='user',parts=[types.Part(text='x'*60001)])]);denied=False
    except LabBlocked as e:denied=str(e)=='TASK_CONTEXT_BYTES'
    check('context_limit_before_model',denied and budget.calls==0)
    budget=Budget();budget.cancelled.set()
    try:DeepAgentsTaskAdapter('/tasks/checkpoints.sqlite',budget).compare(str(uuid4()));denied=False
    except LabBlocked as e:denied=str(e)=='TASK_CANCELLED'
    check('pre_cancelled_task_no_read_or_model',denied and budget.calls==0 and budget.reads==0)
    task_id=str(uuid4());budget=Budget(max_reads=6)
    result=DeepAgentsTaskAdapter('/tasks/checkpoints.sqlite',budget).compare(task_id)
    check('real_model_task_completed',result['status']=='SUCCEEDED' and 1<=budget.calls<=4)
    check('actual_restricted_tool_reads',result['model_tool_reads']>=2 and budget.reads<=6)
    check('canonical_report_two_distinct_sources',len(set(result['source_ids']))==2 and len(result['quotes'])==2 and all(q['source_id'] in result['source_ids'] and q['quote'] for q in result['quotes']))
    with sqlite3.connect('/tasks/checkpoints.sqlite') as db:
        n=db.execute('SELECT count(*) FROM checkpoints WHERE thread_id=?',(task_id,)).fetchone()[0]
    check('checkpoint_persists_after_adapter_closed',n>0 and bool(result['checkpoint_id']))
    cancel_id=str(uuid4());cancel_budget=Budget(max_reads=6)
    try:DeepAgentsTaskAdapter('/tasks/checkpoints.sqlite',cancel_budget,cancel_on_read=True).compare(cancel_id);cancelled=False
    except LabBlocked as e:cancelled=str(e)=='TASK_CANCELLED'
    check('real_mid_task_cancellation',cancelled and cancel_budget.cancelled.is_set() and cancel_budget.calls>=1)
    check('cancelled_task_did_not_continue',cancel_budget.calls<=2 and cancel_budget.reads<=2)
    return dict(seed=seed,checks=checks,passed=True,positive={k:v for k,v in result.items() if k!='quotes'},cancellation_calls=cancel_budget.calls)


def main():
    folder=Path('/tasks/proofs');folder.mkdir(exist_ok=True)
    run_id=uuid4().hex
    seeds=[secrets.randbits(32) for _ in range(2)];rounds=[];error=None
    for seed in seeds:
        def progress(value):(folder/(run_id+'-progress.json')).write_text(json.dumps(value,indent=2))
        try:
            item=run_round(seed,progress);rounds.append(item)
            print(json.dumps(dict(seed=seed,checks=len(item['checks']),streak=len(rounds))),flush=True)
        except Exception as e:
            error=dict(type=type(e).__name__,code=str(e) if isinstance(e,LabBlocked) else 'SANITIZED_SDK_ERROR')
            break
    value=dict(rounds=rounds,error=error,complete=len(rounds)==2 and not error,consecutive_passes=len(rounds) if not error else 0)
    target=folder/(run_id+'-task.json');target.write_text(json.dumps(value,indent=2))
    print(json.dumps(dict(proof=str(target),complete=value['complete'],error=error)),flush=True)
    raise SystemExit(0 if value['complete'] else 1)


if __name__=='__main__':main()
