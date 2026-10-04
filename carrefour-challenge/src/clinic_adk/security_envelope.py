"""Opt-in strict result decoder; the owner's default runtime stays unchanged."""
import json
from .errors import SafeError
from .mcp_guard import unique_fields


def decode_profile_result(value):
    if hasattr(value, 'model_dump'):
        value = value.model_dump(by_alias=True)
    if not isinstance(value, dict) or value.get('isError') or value.get('is_error'):
        raise SafeError('MCP_INVALID_RESULT')
    if 'ok' in value:
        if any(key in value for key in ('content', 'structuredContent', 'structured_content')):
            raise SafeError('MCP_INVALID_RESULT')
        payloads = [value]
    else:
        payloads = []
        for key in ('structuredContent', 'structured_content'):
            if key in value and value[key] is not None:
                if not isinstance(value[key], dict):
                    raise SafeError('MCP_INVALID_RESULT')
                payloads.append(value[key])
        if 'content' in value:
            content = value['content']
            if not isinstance(content, list):
                raise SafeError('MCP_INVALID_RESULT')
            if content:
                if (len(content) != 1 or not isinstance(content[0], dict)
                        or content[0].get('type') != 'text' or not isinstance(content[0].get('text'), str)
                        or len(content[0]['text']) > 16000):
                    raise SafeError('MCP_INVALID_RESULT')
                try:
                    payloads.append(json.loads(content[0]['text'], object_pairs_hook=unique_fields,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('number'))))
                except (ValueError, TypeError, RecursionError):
                    raise SafeError('MCP_INVALID_RESULT') from None
    if not payloads:
        raise SafeError('MCP_INVALID_RESULT')
    canonical = []
    for payload in payloads:
        try:
            encoded = json.dumps(payload, allow_nan=False, sort_keys=True)
        except (ValueError, TypeError, RecursionError):
            raise SafeError('MCP_INVALID_RESULT') from None
        if len(encoded) > 16000 or not isinstance(payload, dict) or payload.get('ok') is not True:
            raise SafeError('MCP_TOOL_FAILED')
        canonical.append(encoded)
    if len(set(canonical)) != 1:
        raise SafeError('MCP_INVALID_RESULT')
    return payloads[0]
