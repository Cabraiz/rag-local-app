# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Construct the pinned SDK/ADK graph offline; fake Runner, never remote inference."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch
from google import genai
from google.genai import types
# Resolve ADK's typed Client field before mocking the constructor function.
# Importing ADK for the first time under that patch changes its type annotation.
from google.adk.models import Gemini
from google.adk.runners import Runner
from rag_app import gemini_lab as probe
from rag_app import gemini_grounded as grounded


def run_round(seed):
    names = []
    captured = {}
    real_client = genai.Client
    fake_key = 'synthetic_test_key_not_a_real_credential'

    def client_factory(**kwargs):
        captured['client_options'] = kwargs
        return real_client(**kwargs)  # Construction/closing only, no SDK request.

    class OfflineRunner:
        def __init__(self, **kwargs):
            captured['agent'] = kwargs['agent']

        async def run_async(self, **kwargs):
            captured['run_options'] = kwargs
            yield SimpleNamespace(is_final_response=lambda: True,
                content=types.Content(role='model', parts=[types.Part(text=captured['reply'])]))

    def check(name, passed):
        assert passed, name
        names.append(name)

    with patch('google.genai.Client', client_factory), patch('google.adk.runners.Runner', OfflineRunner):
        captured['reply'] = 'RAG_LAB_OK'
        check('exact_marker_required', asyncio.run(probe.run_adk(fake_key)))
        opts = captured['client_options']
        agent = captured['agent']
        cfg = agent.generate_content_config
        run = captured['run_options']
        check('synthetic_key_only', opts['api_key'] == fake_key)
        check('explicit_developer_api_not_vertex', opts['vertexai'] is False)
        check('endpoint_pinned', opts['http_options'].base_url == 'https://generativelanguage.googleapis.com')
        check('api_version_pinned', opts['http_options'].api_version == 'v1beta')
        check('http_timeout_bounded', opts['http_options'].timeout == 15000)
        check('sdk_retry_disabled', opts['http_options'].retry_options.attempts == 1)
        check('new_project_compatible_fixed_model', agent.model.model == 'gemini-3.5-flash-lite')
        check('model_and_client_linked', agent.model.client is not None)
        check('no_tools', agent.tools == [])
        check('output_token_limit', cfg.max_output_tokens == 32)
        check('thinking_minimal_not_legacy_budget',
              cfg.thinking_config.thinking_level == types.ThinkingLevel.MINIMAL
              and cfg.thinking_config.thinking_budget is None)
        check('no_thought_summary', cfg.thinking_config.include_thoughts is False)
        check('one_llm_call_per_run', run['run_config'].max_llm_calls == 1)
        check('fixed_synthetic_prompt', run['new_message'].parts[0].text == probe.PROMPT)
        check('no_customer_identity', run['user_id'] == 'synthetic')
        captured['reply'] = 'RAG_LAB_OK extra'
        check('extra_text_rejected', not asyncio.run(probe.run_adk(fake_key)))
        captured['reply'] = f'wrong_{seed}'
        check('wrong_marker_rejected', not asyncio.run(probe.run_adk(fake_key)))
        captured['reply'] = '{"answerable":true,"chunk_id":"synthetic-id"}'
        check('grounded_real_sdk_constructs', asyncio.run(grounded.call_model(fake_key,'synthetic payload')) == captured['reply'])
        agent=captured['agent']; cfg=agent.generate_content_config
        check('schema_on_adk_output_contract', agent.output_schema is grounded.EvidenceSelection and cfg.response_schema is None)
        check('provider_schema_supported', 'additionalProperties' not in agent.output_schema.model_json_schema())
        check('no_instruction_state_interpolation', callable(agent.instruction) and agent.instruction(None)==grounded.INSTRUCTION)
        check('grounded_fixed_tokens_and_no_tools', cfg.max_output_tokens==256 and agent.tools==[])
        check('grounded_one_call', captured['run_options']['run_config'].max_llm_calls==1)
    return {'seed': seed, 'checks': len(names), 'passed': True, 'names': names}


if __name__ == '__main__':
    print(json.dumps({'scope': 'offline_sdk_adk_construction_fake_runner_no_network',
                     'independent_blind': False,
                     'rounds': [run_round(93572), run_round(62840)]}))
