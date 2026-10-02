# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Billing receipt controls only. Synthetic metadata NEVER approves inference."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import subprocess
import sys

ROOT = _workspace_root
SCRIPT = ROOT / 'app/tests/provider/provider_card_receipt.py'


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--output', required=True)
    cli.add_argument('--baseline', action='store_true')
    args = cli.parse_args()
    target = Path(args.output).resolve()
    assert target.is_relative_to(ROOT / 'eval/runs')
    folder = target.with_suffix('')
    folder.mkdir(exist_ok=False)
    proof = dict(card_id='BUG-123', evidence_type='verified_regression', complete=False,
                 consecutive_passes=0, criteria_passed=[], rounds=[], cloud_calls=0,
                 scope='synthetic receipt validation; not real inference or financial audit',
                 sources_sha256={p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in (SCRIPT, Path(__file__))})
    if args.baseline:
        required = ['real_grounded_graph_enabled', 'food_real_model_reported',
                    'food_canonical_evidence', 'food_replay_same_receipt',
                    'blank_rejected_' + repr('   ')]
        scaffold = dict(passed=True, rounds=[dict(passed=True, checks=required)] * 2,
                        source_sha256={SCRIPT.relative_to(ROOT).as_posix(): hashlib.sha256(SCRIPT.read_bytes()).hexdigest()},
                        fixture=True, scope='SIMULATED RECEIPT CONTROL ONLY; no inference executed')
        fixture = folder / 'SIMULATED-smoke.json'
        fixture.write_text(json.dumps(scaffold))
        run = subprocess.run([sys.executable, str(SCRIPT), '--card', 'RAG-02', '--proof', str(fixture)],
                             capture_output=True, text=True, shell=False, timeout=15,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        proof.update(reproduced=run.returncode == 0, accepted_without_current_billing_proof=run.returncode == 0,
                     simulated_child_receipt=True, child_output_file=str(folder / 'RAG-02-SIMULATED-smoke-receipt.json'))
    else:
        spec = importlib.util.spec_from_file_location('provider_receipt', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for seed in (731906, 942387):
            now = datetime.now(timezone.utc)
            valid = dict(observed_at_utc=(now - timedelta(seconds=20)).isoformat(),
                         project_id='gen-lang-client-0580698701',
                         source='authenticated Google Cloud Console visible UI',
                         url='https://console.cloud.google.com/billing/linkedaccount?project=gen-lang-client-0580698701',
                         no_billing_account_at_observation=True, billing_changes=0, credential_changes=0,
                         inference_calls=0)
            cases = [('fresh', {}, None), ('expired', {'observed_at_utc': (now - timedelta(hours=1)).isoformat()}, 'BILLING_PROOF_NOT_CURRENT'),
                     ('future', {'observed_at_utc': (now + timedelta(seconds=1)).isoformat()}, 'BILLING_PROOF_NOT_CURRENT'),
                     ('wrong_project', {'project_id': 'another-project'}, 'BILLING_PROOF_PROJECT'),
                     ('wrong_source', {'source': 'old document'}, 'BILLING_PROOF_SOURCE'),
                     ('billing_enabled', {'no_billing_account_at_observation': False}, 'BILLING_PROOF_NOT_FREE'),
                     ('truthy_integer', {'no_billing_account_at_observation': 1}, 'BILLING_PROOF_NOT_FREE'),
                     ('wrong_url', {'url': 'https://console.cloud.google.com/'}, 'BILLING_PROOF_PROJECT'),
                     ('naive_time', {'observed_at_utc': now.replace(tzinfo=None).isoformat()}, 'BILLING_PROOF_TIME'),
                     ('changed_billing', {'billing_changes': 1}, 'BILLING_PROOF_SOURCE'),
                     ('missing', None, 'BILLING_PROOF_REQUIRED'), ('bad_json', None, 'BILLING_PROOF_INVALID'),
                     ('after_smoke', {}, 'BILLING_PROOF_NOT_CURRENT'), ('outside_eval', None, 'BILLING_PROOF_OUTSIDE_EVAL')]
            random.Random(seed).shuffle(cases)
            checks = []
            for name, delta, expected in cases:
                path = folder / f'{seed}-{name}.json'
                smoke_at = now - timedelta(seconds=10)
                if delta is not None:
                    path.write_text(json.dumps(valid | delta))
                elif name == 'bad_json':
                    path.write_text('{unfinished')
                elif name == 'missing':
                    path = None
                elif name == 'outside_eval':
                    path = SCRIPT
                if name == 'after_smoke':
                    smoke_at = now - timedelta(seconds=60)
                observed = None
                try:
                    result = module.validate_billing_proof(path, smoke_at.isoformat(), now=now)
                    assert result['project_id'] == valid['project_id']
                    assert result['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
                except ValueError as error:
                    observed = str(error)
                checks.append(dict(name=name, expected=expected, observed=observed, passed=observed == expected))
            # Exercise the actual CLI as well: neither missing observation nor an
            # explicitly simulated inference scaffold may create an approval.
            fixture = folder / f'{seed}-SIMULATED-smoke.json'
            scaffold = dict(passed=True, fixture=True, started_at=(now - timedelta(seconds=10)).isoformat(),
                            rounds=[dict(passed=True, checks=[])] * 2, source_sha256={})
            fixture.write_text(json.dumps(scaffold))
            billing_path = folder / f'{seed}-cli-billing.json'
            billing_path.write_text(json.dumps(valid))
            for name, extra, expected in (
                    ('cli_missing_observation', [], 'BILLING_PROOF_REQUIRED'),
                    ('cli_simulated_inference', ['--billing-proof', str(billing_path)], 'SIMULATED_PROVIDER_PROOF')):
                run = subprocess.run([sys.executable, str(SCRIPT), '--card', 'RAG-02', '--proof', str(fixture), *extra],
                                     capture_output=True, text=True, shell=False, timeout=15,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                child = fixture.with_name('RAG-02-' + fixture.stem + '-receipt.json')
                checks.append(dict(name=name, expected=expected,
                                   passed=run.returncode != 0 and expected in run.stderr and not child.exists()))
            passed = all(c['passed'] for c in checks)
            proof['rounds'].append(dict(seed=seed, checks=checks, passed=passed))
            if not passed:
                break
        proof.update(complete=len(proof['rounds']) == 2 and all(r['passed'] for r in proof['rounds']))
        if proof['complete']:
            proof.update(consecutive_passes=2, criteria_passed=['reproduction', 'two_regression_rounds'],
                         reproduction='eval/runs/provider-billing-baseline-20261002.json')
    target.write_text(json.dumps(proof, indent=2))
    print(json.dumps(dict(receipt=str(target), complete=proof['complete'], reproduced=proof.get('reproduced'))))
    raise SystemExit(0 if proof['complete'] or args.baseline and proof.get('reproduced') else 1)


if __name__ == '__main__':
    main()
