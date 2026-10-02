from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route
from .catalog import Catalog
from .errors import SafeError
from .safe_logging import setup
from .mcp_guard import ToolEnvelopeGuard

setup()
catalog = Catalog()
server = MCPServer('fictional-clinic-exam-rag', middleware=[ToolEnvelopeGuard('lookup_exams','exam_names')])

@server.tool()
def lookup_exams(exam_names: list[str]) -> dict:
    """Retrieve canonical fictional exam codes and evidence; never invent codes."""
    try:
        return catalog.retrieve(exam_names)
    except SafeError as error:
        return {'ok': False, 'error': error.code}

async def health(request):
    return JSONResponse({'ok': True, 'transport': 'legacy-http-sse', 'catalog_count': len(catalog.entries),
                         'catalog_version': catalog.version, 'mock_catalog': True})

security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                    allowed_hosts=['rag:8082', 'localhost:*', '127.0.0.1:*'],
                                    allowed_origins=[])
app = server.sse_app(sse_path='/sse', message_path='/messages/', max_request_body_size=16384, transport_security=security)
app.routes.append(Route('/health', health))
