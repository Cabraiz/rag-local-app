"""Offline failure-injection controls; never provider/quality approval."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from google.genai.errors import ClientError, ServerError
from pydantic import BaseModel
import free_model as model


class Output(BaseModel):
    text: str


def case(name, errors, *, maximum=4, schema=True, tools=False, interruption=None):
    budget = model.Budget(max_calls=maximum)
    reservations, physical, closes = [], [], []
    pending = list(errors)
    response = SimpleNamespace(candidates=[SimpleNamespace(content=object())])

    class Client:
        def __init__(self, **kwargs):
            assert kwargs['vertexai'] is False
            assert kwargs['http_options'].retry_options.attempts == 1
            self.models = self
        def generate_content(self, **kwargs):
            physical.append(kwargs['model'])
            assert kwargs['model'] == model.MODEL
            if pending:
                error = pending.pop(0)
                if interruption == 'cancel':
                    budget.cancelled.set()
                if interruption == 'deadline':
                    budget.deadline = 0
                raise error
            return response
        def close(self):
            closes.append(True)

    model.LAST_CALL = 0
    outcome = 'OK'
    with patch.object(model, 'configuration', return_value=(SimpleNamespace(read_text=lambda: 'synthetic-not-a-secret'), None)), \
         patch.object(model, 'reserve_attempt', side_effect=lambda _: reservations.append(True)), \
         patch.object(model.genai, 'Client', Client), \
         patch.object(budget.cancelled, 'wait', side_effect=lambda _: budget.cancelled.is_set()):
        try:
            model.invoke(budget, ['synthetic'], schema=Output if schema else None,
                         tools=[model.types.Tool(function_declarations=[])] if tools else None)
        except model.LabBlocked as error:
            outcome = str(error)
    return dict(name=name, outcome=outcome, calls=budget.calls,
                physical=len(physical), reserved=len(reservations), closed=len(closes))


def transient(code):
    return (ClientError if code == 429 else ServerError)(code, {'error': {'code': code, 'message': 'SYNTHETIC'}})


def main():
    rounds = []
    for number in (1, 2):
        controls = [
            ('success', [], {}, 'OK', 1),
            ('503_then_success', [transient(503)], {}, 'OK', 2),
            ('504_then_success', [transient(504)], {}, 'OK', 2),
            ('429_then_success', [transient(429)], {}, 'OK', 2),
            ('500_then_success', [transient(500)], {}, 'OK', 2),
            ('two_503_stop', [transient(503), transient(503)], {}, 'FREE_MODEL_UNAVAILABLE', 2),
            ('400_not_retried', [ClientError(400, {'error': {'code': 400}})], {}, 'FREE_MODEL_UNAVAILABLE', 1),
            ('403_not_retried', [ClientError(403, {'error': {'code': 403}})], {}, 'FREE_MODEL_UNAVAILABLE', 1),
            ('unknown_timeout_not_retried', [TimeoutError()], {}, 'FREE_MODEL_UNAVAILABLE', 1),
            ('tools_never_retried', [transient(503)], {'tools': True}, 'FREE_MODEL_UNAVAILABLE', 1),
            ('unstructured_never_retried', [transient(503)], {'schema': False}, 'FREE_MODEL_UNAVAILABLE', 1),
            ('budget_never_reset', [transient(503)], {'maximum': 1}, 'TASK_MODEL_BUDGET', 1),
            ('cancel_stops_retry', [transient(503)], {'interruption': 'cancel'}, 'TASK_CANCELLED', 1),
            ('deadline_stops_retry', [transient(503)], {'interruption': 'deadline'}, 'TASK_DEADLINE', 1),
        ]
        results = []
        for name, errors, options, expected, expected_calls in controls:
            result = case(name, errors, **options)
            result['passed'] = result['outcome'] == expected and all(
                result[key] == expected_calls for key in ('calls', 'physical', 'reserved', 'closed'))
            results.append(result)
        rounds.append(dict(number=number, checks=results, passed=all(r['passed'] for r in results)))
    complete = all(r['passed'] for r in rounds)
    paths = [Path(__file__).resolve(), Path(__import__('free_model').__file__).resolve()]
    base = Path(__file__).resolve().parents[1]
    sources = {'app/advanced/' + p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in paths}
    proof = dict(card_id='BUG-120', evidence_type='verified_regression', complete=complete,
                 consecutive_passes=2 if complete else 0,
                 criteria_passed=['reproduction', 'two_regression_rounds'] if complete else [],
                 sources_sha256=sources, rounds=rounds, cloud_calls=0, network='none',
                 reproduction='eval/runs/advanced-retry-baseline-20261002.json',
                 scope='offline controlled transient responses; not real model quality or independent audit')
    print(json.dumps(proof))
    raise SystemExit(0 if complete else 1)


if __name__ == '__main__':
    main()
