"""Bound reads on the opened descriptor; reject pipes/devices without blocking."""
import os
from pathlib import Path
import stat
import tempfile
from .errors import SafeError

def bounded_file(path, limit, code):
    descriptor = None
    try:
        descriptor = os.open(Path(path), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise SafeError(code)
        with os.fdopen(descriptor, 'rb') as stream:
            descriptor = None
            value = stream.read(limit + 1)
        if len(value) > limit:
            raise SafeError(code)
        return value
    except OSError:
        raise SafeError(code) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)

def atomic_artifact(path, value):
    """Replace a directory entry atomically, never open a caller-owned FIFO/link."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.emit-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError:
        raise SafeError('ARTIFACT_WRITE_FAILED') from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
