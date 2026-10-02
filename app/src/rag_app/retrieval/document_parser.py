"""One isolated, bounded parse. No network, filenames, credentials or diagnostics in output."""
import io
import json
import resource
import sys


def main():
    resource.setrlimit(resource.RLIMIT_AS, (268435456, 268435456))
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    raw = sys.stdin.buffer.read(8193)
    if not raw or len(raw) > 8192:
        raise ValueError()
    kind = sys.argv[1]
    if kind in ('text/plain', 'text/markdown'):
        text = raw.decode('utf-8-sig')
    elif kind == 'application/pdf':
        from pypdf import PdfReader
        if not raw.startswith(b'%PDF-'):
            raise ValueError()
        reader = PdfReader(io.BytesIO(raw), strict=True)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 3:
            raise ValueError()
        root = reader.trailer['/Root']
        if any(k in root for k in ('/OpenAction', '/AA', '/AcroForm', '/Names')):
            raise ValueError()
        parts = []
        for page in reader.pages:
            if '/AA' in page or page.get('/Annots'):
                raise ValueError()
            parts.append(page.extract_text() or '')
            if sum(len(p) for p in parts) > 8192:
                raise ValueError()
        text = '\n'.join(parts)
    else:
        raise ValueError()
    if not text.strip() or len(text) > 8192 or any(ord(c) < 32 and c not in '\n\r\t' for c in text):
        raise ValueError()
    sys.stdout.write(json.dumps({'text': text}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        sys.exit(2)
