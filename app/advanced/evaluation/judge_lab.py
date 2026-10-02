"""Explicit Free judge with DeepEval; no default paid model or cloud report upload."""
import asyncio
import json
import logging
from pathlib import Path
import random
import time

logging.disable(logging.CRITICAL)
from deepeval.models import DeepEvalBaseLLM
from deepeval.metrics import FaithfulnessMetric
from deepeval.test_case import LLMTestCase
from google.genai import types
from free_model import Budget,invoke,LabBlocked,MODEL


class FreeJudge(DeepEvalBaseLLM):
    def __init__(self,budget):
        self.budget=budget;self.schemas=[]
        super().__init__(model=MODEL)
    def load_model(self):return self
    def get_model_name(self):return MODEL
    def generate(self,prompt,schema=None):
        if 'expected_pass' in prompt:raise LabBlocked('JUDGE_GOLD_LABEL_LEAK')
        response=invoke(self.budget,[types.Content(role='user',parts=[types.Part(text=prompt)])],schema=schema)
        self.schemas.append(schema.__name__ if schema else 'text')
        if schema:return schema.model_validate_json(response.text)
        return response.text
    async def a_generate(self,prompt,schema=None):
        return await asyncio.to_thread(self.generate,prompt,schema)


def run_round(seed,progress):
    data=json.loads((Path(__file__).parent/'datasets/judge-v1.json').read_text())
    assert data['threshold']==1.0
    results=[];checks=[]
    for split in ('calibration','holdout'):
        order=list(data[split]);random.Random(seed).shuffle(order)
        for case in order:
            budget=Budget(max_calls=4,deadline=time.monotonic()+90)
            model=FreeJudge(budget)
            metric=FaithfulnessMetric(model=model,threshold=data['threshold'],strict_mode=True,
                async_mode=False,include_reason=False)
            metric.measure(LLMTestCase(input=case['question'],actual_output=case['answer'],retrieval_context=[case['context']]))
            observed=metric.is_successful()
            result=dict(id=case['id'],split=split,expected=case['expected_pass'],observed=observed,
                score=metric.score,calls=budget.calls,schemas=model.schemas,passed=observed==case['expected_pass'])
            results.append(result)
            progress(dict(seed=seed,cases=results,complete=False))
            assert budget.calls>0 and result['passed'],'JUDGE_CASE_'+case['id']
        assert all(r['passed'] for r in results if r['split']==split),'SPLIT_FAILED'
    checks.append(dict(name='calibration_and_frozen_holdout',passed=True))
    checks.append(dict(name='no_false_accepts_in_authored_set',passed=all(not r['observed'] for r in results if not r['expected'])))
    assert all(c['passed'] for c in checks)
    return dict(seed=seed,cases=results,checks=checks,passed=True,threshold=1.0,holdout_fitting=False,model=MODEL)
