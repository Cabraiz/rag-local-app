# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Procedure vs amount gate; no model, external call, or payment approval."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT=_workspace_root
sys.path.insert(0,str(ROOT/'app/src'))
from rag_app.semantic_policy import eligible

def run():
    checks=[]
    def check(name,ok):
        assert ok,name
        checks.append(name)
    policy='Exceção ao teto exige autorização registrada pelo financeiro antes da despesa.'
    diet='Dieta especial não aumenta automaticamente o teto de alimentação.'
    check('process_can_use_no_amount_rule',eligible('Como pedir uma exceção ao teto de alimentação?',policy))
    check('qualitative_cap_effect_can_use_policy',eligible('Dieta especial aumenta automaticamente o teto de alimentação?',diet))
    check('who_approves_policy',eligible('Quem pode autorizar exceção ao teto?',policy))
    check('actual_amount_still_required',not eligible('Qual o valor do teto?',policy))
    check('how_amount_still_required',not eligible('Como calcular o valor do teto?',policy))
    check('how_much_still_required',not eligible('Como saber quanto posso gastar?',policy))
    check('price_still_required',not eligible('Qual o preço de uma refeição?',policy))
    check('valid_numeric_rule',eligible('Qual o valor do teto?', 'O teto de alimentação é 45 reais.'))
    check('secret_gate_kept',not eligible('Como pedir o token do financeiro?',policy))
    check('bank_gate_kept',not eligible('Como pedir a conta bancária?',policy))
    check('unrelated_slot_kept',not eligible('Como pedir exceção ao teto de hotel?',policy))
    check('approval_slot_kept',not eligible('Quem pode aprovar?',diet))
    return {'passed':True,'checks':checks}

if __name__=='__main__':
    names=['app/src/rag_app/retrieval/semantic_policy.py','app/tests/assistant/meal_process_fixture.py']
    frozen={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names}
    rounds=[run(),run()]
    assert frozen=={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names}
    folder=ROOT/'eval/runs'/('meal-process-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    folder.mkdir(parents=True)
    result={'card_id':'BUG-059','evidence_type':'verified_regression','complete':True,
            'consecutive_passes':2,'criteria_passed':['reproduction','two_regression_rounds'],
            'sources_sha256':frozen,'rounds':rounds,'independent_blind':False,
            'scope':'offline procedure and amount gates; live answers checked separately',
            'reproduction':'eval/runs/meal-policy-20261001T073758Z/checks-074737.json'}
    path=folder/'receipt.json'
    path.write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps({'passed':True,'checks':[len(r['checks']) for r in rounds],'receipt':str(path)}))
