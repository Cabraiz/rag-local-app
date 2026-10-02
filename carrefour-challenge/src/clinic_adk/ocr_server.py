import asyncio
import io
from pathlib import Path
import subprocess
import threading
from PIL import Image, UnidentifiedImageError
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route
from .catalog import Catalog
from .privacy import sanitize_ocr
from .errors import SafeError
from .safe_logging import setup
from .mcp_guard import ToolEnvelopeGuard
from .file_input import bounded_file

setup()
catalog = Catalog()
server = MCPServer('fictional-clinic-ocr', middleware=[ToolEnvelopeGuard('extract_exams','image_ref')])
slots = threading.BoundedSemaphore(2)
SAMPLES = Path('/samples').resolve()
Image.MAX_IMAGE_PIXELS = 5000000

def extract(image_ref):
    if not isinstance(image_ref, str) or len(image_ref) > 100:
        raise SafeError('IMAGE_REFERENCE_INVALID')
    path = (SAMPLES / image_ref).resolve()
    if not path.is_relative_to(SAMPLES) or path.suffix.lower() not in ('.png', '.jpg', '.jpeg') or not path.is_file():
        raise SafeError('IMAGE_REFERENCE_DENIED')
    if path.stat().st_size > 4000000:
        raise SafeError('IMAGE_SIZE_LIMIT')
    if not slots.acquire(blocking=False):
        raise SafeError('OCR_BUSY')
    try:
        raw = bounded_file(path, 4000000, 'IMAGE_SIZE_LIMIT')
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ('PNG', 'JPEG') or image.width * image.height > 5000000 or image.width < 50 or image.height < 50:
                raise SafeError('IMAGE_FORMAT_OR_DIMENSIONS')
            image.load()
            buffer = io.BytesIO()
            image.convert('RGB').save(buffer, format='PNG')
        result = subprocess.run(['tesseract', 'stdin', 'stdout', '-l', 'por', '--psm', '6'],
                                input=buffer.getvalue(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=8, check=False)
        if result.returncode:
            raise SafeError('OCR_ENGINE_FAILED')
        return sanitize_ocr(result.stdout.decode('utf8', errors='strict'), catalog)
    except SafeError:
        raise
    except (OSError, UnidentifiedImageError, ValueError, UnicodeError, subprocess.TimeoutExpired, Image.DecompressionBombError):
        raise SafeError('OCR_FAILED') from None
    finally:
        slots.release()

@server.tool()
async def extract_exams(image_ref: str) -> dict:
    """Extract fictitious exam labels from a local sample; raw OCR/PII are never returned."""
    try:
        return await asyncio.to_thread(extract, image_ref)
    except SafeError as error:
        return {'ok': False, 'error': error.code}

async def health(request):
    return JSONResponse({'ok': True, 'transport': 'legacy-http-sse', 'engine': 'tesseract-local'})

security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                    allowed_hosts=['ocr:8081', 'localhost:*', '127.0.0.1:*'],
                                    allowed_origins=[])
app = server.sse_app(sse_path='/sse', message_path='/messages/', max_request_body_size=16384, transport_security=security)
app.routes.append(Route('/health', health))
