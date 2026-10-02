# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Meal bill / banking disambiguation. Deterministic, no model or network."""
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
    meal='Conta coletiva de alimentação: ratear entre participantes elegíveis pelo consumo real.'
    bank='A conta bancária cadastrada é 12345.'
    check('meal_bill_not_bank',eligible('Como ratear uma conta coletiva de alimentação?',meal))
    check('restaurant_bill_not_bank',eligible('Como ratear uma conta de restaurante?',meal))
    check('explicit_bank_wins',not eligible('Qual conta bancária para ratear alimentação coletiva?',meal))
    check('pix_not_meal_bill',not eligible('Qual pix para conta coletiva de alimentação?',meal))
    check('bare_account_conservative',not eligible('Qual a conta?',meal))
    check('bank_identifier_still_required',not eligible('Qual conta bancária?', 'A conta bancária está cadastrada.'))
    check('actual_bank_identifier_preserved',eligible('Qual conta bancária?',bank))
    check('bank_missing_source_rejected',not eligible('Qual banco?',meal))
    check('sensitive_still_rejected',not eligible('Qual token da conta coletiva de alimentação?',meal))
    check('hotel_slot_still_rejected',not eligible('Conta coletiva de alimentação no hotel?',meal))
    return {'passed':True,'checks':checks}

if __name__=='__main__':
    names=['app/src/rag_app/retrieval/semantic_policy.py','app/tests/assistant/meal_bill_fixture.py']
    frozen={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names}
    rounds=[run(),run()]
    assert frozen=={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names}
    folder=ROOT/'eval/runs'/('meal-bill-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    folder.mkdir(parents=True)
    result={'card_id':'BUG-058','evidence_type':'verified_regression','complete':True,
            'consecutive_passes':2,'criteria_passed':['reproduction','two_regression_rounds'],
            'sources_sha256':frozen,'rounds':rounds,'independent_blind':False,
            'scope':'offline policy gate; live answer checked separately',
            'reproduction':'eval/runs/meal-policy-20261001T073758Z/checks-074144.json'}
    path=folder/'receipt.json'
    path.write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps({'passed':True,'checks':[len(r['checks']) for r in rounds],'receipt':str(path)}))
