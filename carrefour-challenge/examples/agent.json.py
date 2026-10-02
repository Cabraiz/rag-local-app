# Generated deterministically by clinic_adk.compiler; do not edit.
from google.adk import Workflow
from google.adk.workflow import START
from clinic_adk.runtime import Runtime

SPEC_SHA256 = '9c7c589191d256dd7718b13713725332738c7f294295cc7f8f815018434cff9d'

def build_agent(runtime):
    async def extract_order(node_input):
        return await runtime.step('ocr', node_input)

    async def retrieve_codes(node_input):
        return await runtime.step('retrieve', node_input)

    async def validate_codes(node_input):
        return await runtime.step('validate', node_input)

    async def submit_request(node_input):
        return await runtime.step('schedule', node_input)

    async def format_receipt(node_input):
        return await runtime.step('format', node_input)

    return Workflow(name='clinic_scheduling', edges=[(START, extract_order, retrieve_codes, validate_codes, submit_request, format_receipt)], max_concurrency=1, timeout=45)

root_agent = build_agent(Runtime())
