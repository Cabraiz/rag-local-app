"""Two real Free Gemini/DeepEval rounds against the unchanged authored dataset."""
import json
from pathlib import Path
import secrets
from uuid import uuid4

from free_model import LabBlocked
from judge_lab import run_round


def main():
    folder = Path('/tasks/proofs')
    folder.mkdir(exist_ok=True)
    run_id = uuid4().hex
    rounds = []
    error = None
    for seed in (secrets.randbits(32), secrets.randbits(32)):
        def progress(value):
            (folder / (run_id + '-judge-progress.json')).write_text(json.dumps(value, indent=2))
        try:
            value = run_round(seed, progress)
            rounds.append(value)
            print(json.dumps(dict(seed=seed, cases=len(value['cases']), streak=len(rounds))), flush=True)
        except Exception as exc:
            code = str(exc) if isinstance(exc, (LabBlocked, AssertionError)) else 'SANITIZED_SDK_ERROR'
            error = dict(type=type(exc).__name__, code=code)
            break
    value = dict(rounds=rounds, error=error, complete=len(rounds) == 2 and error is None,
                 consecutive_passes=2 if len(rounds) == 2 and error is None else 0)
    target = folder / (run_id + '-judge.json')
    target.write_text(json.dumps(value, indent=2))
    print(json.dumps(dict(proof=str(target), complete=value['complete'], error=error)), flush=True)
    raise SystemExit(0 if value['complete'] else 1)


if __name__ == '__main__':
    main()
