"""Tool arguments of the wrong type get one clear sentence, not the SDK's validation dump.

The MCP SDK validates a tool's arguments with pydantic before calling it, and a value of
the wrong type (filename=123, query=None) came back as "1 validation error for ...". With
`Annotated[str, or_default('')]` pydantic still checks the value, and the published
schema still says "string", but a value it rejects becomes `fallback`, which the tool
then refuses in Portuguese.
"""
from pydantic import ValidationError, WrapValidator


def or_default(fallback):
    """pydantic's own check of the argument; a value it rejects becomes `fallback`."""
    def check(value, handler):
        try:
            return handler(value)
        except ValidationError:
            return fallback
    return WrapValidator(check)
