"""Tool arguments of the wrong type get one clear sentence, not the SDK's validation dump.

The MCP SDK validates a tool's arguments with pydantic before calling it, and a value of
the wrong type (filename=123, query=None) came back as "1 validation error for ...". With
`Annotated[str, or_default('')]` pydantic still checks the value, and the published
schema still says "string", but a value it rejects becomes `fallback`, which the tool
then refuses in Portuguese. What a peer sends never reaches a log either (quiet_logs).
"""
import logging

from pydantic import ValidationError, WrapValidator


def or_default(fallback):
    """pydantic's own check of the argument; a value it rejects becomes `fallback`."""
    def check(value, handler):
        try:
            return handler(value)
        except ValidationError:
            return fallback
    return WrapValidator(check)


def quiet_logs(*tools: str) -> None:
    """Log records keep their fixed text, numbers, tool names and known words; any other value (a file name, a peer's
    id, a message quoting them) becomes '…', an exception its type. The SSE warnings, quoting the peer, are off."""
    make, known = logging.getLogRecordFactory(), {*tools, 'GET', 'POST', '1.1', '/sse', '/health', 'http'}

    def record(*args, **kwargs) -> logging.LogRecord:
        made = make(*args, **kwargs)
        made.args = tuple(arg if isinstance(arg, int | float) or isinstance(arg, str) and arg in known else '…'
                          for arg in made.args) if isinstance(made.args, tuple) else dict.fromkeys(made.args or ())
        error = f' ({made.exc_info[0].__name__})' if made.exc_info and made.exc_info[0] else ''
        made.msg, made.exc_info = f'{made.msg if isinstance(made.msg, str) else type(made.msg).__name__}{error}', None
        return made
    logging.setLogRecordFactory(record)
    logging.getLogger('mcp.server.sse').setLevel(logging.ERROR)
