# Generated deterministically by clinic_adk.compiler; do not edit.
from google.adk import Workflow
from google.adk.workflow import START
from clinic_adk.runtime import Runtime

SPEC_SHA256 = '117400a7dd808afb6de6198285482bae40faa3f08327c0144f9f4b28e0b14955'

def build_agent(runtime):
    async def variant_extract_order(node_input):
        return await runtime.step('ocr', node_input)

    async def variant_retrieve_codes(node_input):
        return await runtime.step('retrieve', node_input)

    async def variant_validate_codes(node_input):
        return await runtime.step('validate', node_input)

    async def variant_submit_request(node_input):
        return await runtime.step('schedule', node_input)

    async def variant_format_receipt(node_input):
        return await runtime.step('format', node_input)

    return Workflow(name='alternative_clinic_flow', edges=[(START, variant_extract_order, variant_retrieve_codes, variant_validate_codes, variant_submit_request, variant_format_receipt)], max_concurrency=1, timeout=45)

root_agent = build_agent(Runtime())
