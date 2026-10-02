# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""One bounded real judge case; expose reason enums only, never SDK bodies."""
import argparse
import json
import re
import time
from google.genai import models
import free_model
from free_model import Budget
from judge_lab import FreeJudge
from deepeval.metrics import FaithfulnessMetric
from deepeval.test_case import LLMTestCase

parser = argparse.ArgumentParser()
parser.add_argument('--timeout-ms', type=int, choices=[15000, 30000], default=15000)
parser.add_argument('--temperature', type=float, choices=[0.0, 1.0], default=0.0)
diagnostic_options = parser.parse_args()
actual_client = free_model.genai.Client


def bounded_client(*a, **kw):
    kw['http_options'] = kw['http_options'].model_copy(update={'timeout': diagnostic_options.timeout_ms})
    return actual_client(*a, **kw)


free_model.genai.Client = bounded_client
original = models.Models.generate_content


def inspected(self, *args, **kwargs):
    try:
        config=kwargs.get('config')
        if config is not None:
            kwargs['config']=({**config,'temperature':diagnostic_options.temperature} if isinstance(config,dict)
                              else config.model_copy(update={'temperature':diagnostic_options.temperature}))
        return original(self, *args, **kwargs)
    except Exception as error:
        code = getattr(error, 'code', None)
        message = str(getattr(error, 'message', '')).lower()
        reason = ('quota' if code == 429 else 'high_demand' if any(s in message for s in ('high demand','overloaded'))
                  else 'deadline' if any(s in message for s in ('deadline','timed out')) else 'schema' if 'schema' in message
                  else 'credential' if 'api key' in message else 'provider')
        print(json.dumps({'provider_error': isinstance(code,int),
                          'type': re.sub('[^A-Za-z0-9_]', '', type(error).__name__)[:80],
                          'attribute': re.sub('[^A-Za-z0-9_]', '', getattr(error,'name','') or '')[:80],
                          'config_type': type(kwargs.get('config')).__name__,
                          'code': code if isinstance(code, int) else None,
                          'reason': reason}), flush=True)
        raise


models.Models.generate_content = inspected
budget = Budget(max_calls=4, deadline=time.monotonic()+90)
metric = FaithfulnessMetric(model=FreeJudge(budget), threshold=1.0,
                           strict_mode=True, async_mode=False, include_reason=False)
try:
    metric.measure(LLMTestCase(input='Qual o limite?', actual_output='45 reais.',
                              retrieval_context=['O limite aprovado e 45 reais.']))
    print(json.dumps({'passed': metric.is_successful(), 'calls': budget.calls,
                      'timeout_ms': diagnostic_options.timeout_ms,'temperature':diagnostic_options.temperature}), flush=True)
except Exception as error:
    print(json.dumps({'passed': False, 'type': type(error).__name__,
                      'calls': budget.calls}), flush=True)
    raise SystemExit(1)
