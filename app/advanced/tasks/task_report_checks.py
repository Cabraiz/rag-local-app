"""Controlled adapter-result regressions; no model/SQL/network approval."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from free_model import Budget, LabBlocked
import task_lab

IDS = ['source-a', 'source-b']


def exercise(raw, tool_raw=None):
    budget = Budget(max_reads=6)
    messages = [SimpleNamespace(type='tool', name='lookup_evidence', status='success',
                                content=json.dumps(dict(source_id=id_, quote='Synthetic approved quote')))
                for id_ in IDS]
    if tool_raw is not None:
        messages[0].content = tool_raw
    messages.append(SimpleNamespace(type='ai', content=raw))

    class Agent:
        def invoke(self, *_):
            return {'messages': messages}
        def get_state(self, *_):
            return SimpleNamespace(values={'messages': messages}, config={'configurable': {'checkpoint_id': 'synthetic'}})

    class Database:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def execute(self, query, params):
            assert params[0] in IDS
            return SimpleNamespace(fetchone=lambda: {'content_hash': 'synthetic'}, fetchall=lambda: [])

    with patch.object(task_lab, 'evidence', return_value=(task_lab.Identity('demo-a', 'demo-user'), 'synthetic', dict.fromkeys(IDS, {}))), \
         patch.object(task_lab, 'create_deep_agent', return_value=Agent()), \
         patch.object(task_lab.ledger, 'connect', return_value=Database()), \
         patch.object(task_lab, 'reconstruct', return_value='Synthetic approved quote'):
        try:
            result = task_lab.DeepAgentsTaskAdapter(':memory:', budget).compare('synthetic')
            outcome = 'SUCCEEDED' if result['source_ids'] == IDS else 'WRONG_RESULT'
        except LabBlocked:
            outcome = 'TYPED_REJECTION'
        except Exception as error:
            outcome = type(error).__name__
    return dict(outcome=outcome, calls=budget.calls, reads=budget.reads)


def main():
    good = json.dumps({'source_ids': IDS})
    cases = [('json', good, True, None),
             ('single_json_fence', '```json\n' + good + '\n```', True, None),
             ('whitespace', '\n ' + good + '\t', True, None),
             ('empty', '', False, None), ('unfinished_json', '{"source_ids":[', False, None),
             ('outside_text', 'Here is the result: ' + good, False, None),
             ('two_json_objects', good + good, False, None),
             ('wrong_language', '```js\n' + good + '\n```', False, None),
             ('nested_fences', '```json\n```json\n' + good + '\n```\n```', False, None),
             ('extra_key', json.dumps({'source_ids': IDS, 'execute': 'forbidden'}), False, None),
             ('unknown_id', json.dumps({'source_ids': ['source-a', 'outside']}), False, None),
             ('duplicate_ids', json.dumps({'source_ids': ['source-a', 'source-a']}), False, None),
             ('unhashable_id', json.dumps({'source_ids': [{'id': 'source-a'}, 'source-b']}), False, None),
             ('wrong_root', json.dumps(IDS), False, None),
             ('null_list', '{"source_ids":null}', False, None),
             ('duplicate_json_key', '{"source_ids":["outside"],"source_ids":["source-a","source-b"]}', False, None),
             ('size_limit', ' ' * 8193 + good, False, None),
             ('tool_invalid_json', good, False, 'Error: synthetic tool failure'),
             ('tool_unknown_id', good, False, '{"source_id":"outside","quote":"Synthetic"}'),
             ('tool_unhashable_id', good, False, '{"source_id":[],"quote":"Synthetic"}')]
    rounds = []
    for number in (1, 2):
        results = []
        for name, raw, accepted, tool_raw in cases:
            result = exercise(raw, tool_raw)
            result.update(name=name, passed=result['outcome'] == ('SUCCEEDED' if accepted else 'TYPED_REJECTION')
                          and result['calls'] == 0 and result['reads'] == (2 if accepted else 0))
            results.append(result)
        rounds.append(dict(number=number, checks=results, passed=all(r['passed'] for r in results)))
    complete = all(r['passed'] for r in rounds)
    files = [Path(__file__), Path(task_lab.__file__)]
    parser = Path(__file__).parent / 'task_report.py'
    if parser.exists():
        files.append(parser)
    sources = {'app/advanced/' + p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    print(json.dumps(dict(card_id='BUG-122', evidence_type='verified_regression', complete=complete,
                         consecutive_passes=2 if complete else 0,
                         criteria_passed=['reproduction', 'two_regression_rounds'] if complete else [],
                         sources_sha256=sources, rounds=rounds, cloud_calls=0, network='none',
                         reproduction='eval/runs/task-report-baseline-20261002.json',
                         actual_failure='eval/runs/advanced-task-20261002T130909Z/receipt.json',
                         scope='controlled schema/ACL guard; mock data, not real DeepAgents task approval')))
    raise SystemExit(0 if complete else 1)


if __name__ == '__main__':
    main()
