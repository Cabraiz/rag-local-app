"""Explicit deployment adapters; owner component sources remain untouched.

Start MCPs through ocr_app/rag_app and the CLI through secure_cli. Bindings are
local to that dedicated process, before serving requests, never per-request.
"""
import json
import stat
import google.auth
from google.auth.exceptions import DefaultCredentialsError
from . import runtime
from .catalog import Catalog
from .errors import SafeError
from .mcp_guard import MessageEnvelopeGuard, unique_fields, enable_profile_tools
from .security_envelope import decode_profile_result
from .security import catalog_text, image_reference, tool_arguments, tool_payload, ocr_text
from .privacy import query_safe

_upstream_call = runtime.mcp_call


def offline_credential_discovery(*args, **kwargs):
    """No ADC filesystem or cloud metadata discovery in this offline profile.

    ADK probes ADC even for local MCP tools. Report its documented absent-
    credentials condition before the SDK can consult host files or metadata.
    No credential object, token or synthetic authentication is supplied.
    """
    raise DefaultCredentialsError('OFFLINE_CREDENTIAL_DISCOVERY_DENIED')


google.auth.default = offline_credential_discovery
enable_profile_tools()
runtime.decode_tool = decode_profile_result


class SecureCatalog(Catalog):
    def _load(self, raw):
        # CF05 owns schema/evidence validation. Its file and from_bytes factories
        # dispatch here, so security validates those exact already-bounded bytes.
        super()._load(raw)
        source = json.loads(raw, object_pairs_hook=unique_fields)
        catalog_text(source['notice'])
        for row in self.entries:
            for text in (row['name'], row['evidence'], *row['aliases']):
                catalog_text(text)


async def secure_mcp_call(provider, arguments, timeout=12):
    if not isinstance(provider, str) or provider not in runtime.TOOLS:
        raise SafeError('MCP_PROVIDER_DENIED')
    tool_arguments(runtime.TOOLS[provider], arguments)
    result = await _upstream_call(provider, arguments, timeout=timeout)
    value = tool_payload(provider, result)
    catalog = SecureCatalog()
    if provider == 'ocr':
        for name in value['exam_names']:
            row = catalog.by_name.get(query_safe(name))
            if not row or row['name'] != name:
                raise SafeError('OCR_UNAUTHORIZED_OUTPUT')
    else:
        if value['catalog_count'] != len(catalog.entries) or value['catalog_version'] != catalog.version:
            raise SafeError('RAG_INCOMPLETE_OR_STALE')
        expected = {catalog.by_name[query_safe(name)]['code'] for name in arguments['exam_names']
                    if query_safe(name) in catalog.by_name}
        seen = set()
        for row in value['exams']:
            canonical = catalog.by_code.get(row['code'])
            if (not canonical or row['code'] in seen
                    or any(row[k] != canonical[k] for k in ('name', 'evidence'))):
                raise SafeError('EXAM_EVIDENCE_MISMATCH')
            seen.add(row['code'])
        if seen != expected:
            raise SafeError('EXAM_EVIDENCE_MISMATCH')
    return value


def install_runtime():
    """Choose the hardened bindings once, before CLI imports/constructs agents."""
    runtime.Catalog = SecureCatalog
    runtime.mcp_call = secure_mcp_call


def guarded_extract(component, original, image_ref):
    image_reference(image_ref)
    path = component.SAMPLES / image_ref
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise SafeError('IMAGE_REFERENCE_DENIED')
    except OSError:
        raise SafeError('IMAGE_REFERENCE_DENIED') from None
    return original(image_ref)


def mcp_app(provider):
    if provider == 'ocr':
        from . import ocr_server as component
        original = component.extract
        component.extract = lambda image_ref: guarded_extract(component, original, image_ref)
        sanitizer = component.sanitize_ocr
        component.sanitize_ocr = lambda raw, catalog: ocr_text(raw, catalog, sanitizer)
    elif provider == 'rag':
        from . import rag_server as component
    else:
        raise SafeError('MCP_PROVIDER_DENIED')
    component.catalog = SecureCatalog()
    return MessageEnvelopeGuard(component.app)


ocr_app = mcp_app('ocr')
rag_app = mcp_app('rag')
