"""Reject malformed tool envelopes before SDK validation can reflect input."""
import json
from mcp.types import CallToolResult, TextContent

class ToolEnvelopeGuard:
    def __init__(self, tool, argument):
        self.tool, self.argument = tool, argument

    def failure(self):
        return CallToolResult(is_error=True, content=[
            TextContent(type='text', text=json.dumps({'ok':False,'error':'MCP_INVALID_TOOL_ENVELOPE'}))])

    async def __call__(self, ctx, call_next):
        if ctx.method != 'tools/call':
            return await call_next(ctx)
        params = ctx.params
        if (not isinstance(params, dict) or set(params) - {'name','arguments','_meta'}
                or params.get('name') != self.tool):
            return self.failure()
        args = params.get('arguments')
        if not isinstance(args, dict) or set(args) != {self.argument}:
            return self.failure()
        value = args[self.argument]
        if self.argument == 'image_ref':
            valid = isinstance(value, str) and 1 <= len(value) <= 100
        else:
            valid = (isinstance(value, list) and 1 <= len(value) <= 20
                     and all(isinstance(item, str) and 1 <= len(item) <= 120 for item in value))
        if not valid:
            return self.failure()
        try:
            return await call_next(ctx)
        except Exception:
            return self.failure()
