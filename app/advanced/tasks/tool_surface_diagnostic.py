"""Inspect only tool names; fail before any inference or external tool call."""
import json
from deepagents import create_deep_agent, HarnessProfile, register_harness_profile
from langchain_core.utils.function_calling import convert_to_openai_tool
from free_model import FreeChatModel, Budget


class Inspected(RuntimeError):
    pass


class Probe(FreeChatModel):
    def bind_tools(self, tools, **kwargs):
        print(json.dumps(dict(tools=[convert_to_openai_tool(t)['function']['name'] for t in tools])), flush=True)
        raise Inspected()


def lookup_evidence(source_id: str) -> str:
    """Read an authorized synthetic source ID."""
    raise AssertionError('Diagnostic must not execute tools')


register_harness_profile('google_genai', HarnessProfile(excluded_tools=frozenset(
    {'ls', 'read_file', 'write_file', 'edit_file', 'delete', 'glob', 'grep', 'execute', 'task'})))
try:
    agent = create_deep_agent(model=Probe(Budget(max_calls=0)), tools=[lookup_evidence])
    agent.invoke({'messages': [{'role': 'user', 'content': 'synthetic'}]}, {'recursion_limit': 12})
except Inspected:
    pass
