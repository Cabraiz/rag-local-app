# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Candidate ambiguity regression. Fake embed only; no network or calibration fit."""
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=_workspace_root;sys.path.insert(0,str(ROOT/'app/src'))
from rag_app import neural_client as n

def run():
    checks=[]
    def check(name,ok):
        if not ok:raise AssertionError(name)
        checks.append(name)
    rows=[{'document_id':str(i),'ordinal':0,'title':'Alimentação',
           'quote':'O limite de alimentação é 45 reais.'} for i in range(8)]
    def vector(score):return [score,math.sqrt(1-score*score)]+[0.]*382
    vectors=[[1.]+[0.]*383]+[vector(.8-i*.01) for i in range(8)]
    with patch.object(n,'embed',return_value=(vectors,.3,.05)):
        check('ambiguous_extractive_stays_abstained',n.rank('Qual o limite de alimentação?',rows)==[])
        result=n.rank('Qual o limite de alimentação?',rows,for_grounding=True)
        check('grounding_recovers_qualified_candidates',result==rows[:5])
        check('context_cap_five',len(result)==5)
        check('no_rows_fabricated',all(r is rows[i] for i,r in enumerate(result)))
        check('sensitive_question_not_recovered',n.rank('Qual o token secreto?',rows,for_grounding=True)==[])
        check('unrelated_slot_not_recovered',n.rank('Qual o limite de hotel?',rows,for_grounding=True)==[])
    with patch.object(n,'embed',return_value=([[1.]+[0.]*383]+[vector(.2) for _ in rows],.3,.05)):
        check('below_threshold_stays_empty',n.rank('Qual o limite de alimentação?',rows,for_grounding=True)==[])
    with patch.object(n,'embed',return_value=(vectors,.3,.05)):
        check('repeat_is_deterministic',n.rank('Qual o limite de alimentação?',rows,for_grounding=True)==rows[:5])
    return {'passed':True,'checks':checks}

if __name__=='__main__':
    names=['app/src/rag_app/retrieval/neural_client.py','app/src/rag_app/retrieval/corpus.py',
           'app/src/rag_app/retrieval/semantic_policy.py','app/tests/rag/grounding_recall_fixture.py']
    frozen={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in names}
    rounds=[run(),run()]
    assert frozen=={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in names}
    folder=ROOT/'eval/runs'/('grounding-recall-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));folder.mkdir(parents=True)
    receipt={'card_id':'BUG-057','evidence_type':'verified_regression','complete':True,'consecutive_passes':2,
      'criteria_passed':['reproduction','two_regression_rounds'],'sources_sha256':frozen,'rounds':rounds,
      'scope':'candidate ranking offline, not real answer quality','independent_blind':False,
      'reproduction':'eval/runs/meal-policy-20261001T073758Z/checks-073915.json'}
    path=folder/'receipt.json';path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps({'passed':True,'checks':[len(r['checks']) for r in rounds],'receipt':str(path)}))
