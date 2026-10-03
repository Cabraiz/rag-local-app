"""Import the versioned fictional fixture without overwriting an existing catalog.

The JSON file itself is the persisted store. No database, vector service or
clinical equivalences are implied. Only a single immutable snapshot is imported.
"""
import argparse
import json
import os
from pathlib import Path
import tempfile

from .catalog import Catalog, PATH
from .errors import SafeError
from .file_input import bounded_file


def import_catalog(source, destination):
    raw = bounded_file(source, 1000000, 'CATALOG_SIZE_OR_PATH')
    seed = Catalog.from_bytes(raw)
    destination = Path(destination)
    temporary = None
    created = False
    try:
        # link() publishes a fully written snapshot only if destination is absent.
        # Unlike replace(), a concurrent seed cannot overwrite another writer.
        if not os.path.lexists(destination):
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.catalog-',
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination)
                created = True
            except FileExistsError:
                pass
        persisted = Catalog(destination)
        if (persisted.entries != seed.entries or
                bounded_file(destination, 1000000, 'CATALOG_SIZE_OR_PATH') != raw):
            raise SafeError('CATALOG_IMPORT_CONFLICT')
        return {'ok': True, 'status': 'created' if created else 'unchanged',
                'catalog_count': len(persisted.entries), 'catalog_version': persisted.version}
    except OSError:
        raise SafeError('CATALOG_IMPORT_FAILED') from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description='Importar catálogo fictício imutável; sem overwrite.')
    parser.add_argument('--source', default=str(PATH))
    parser.add_argument('--destination', required=True)
    args = parser.parse_args()
    try:
        value = import_catalog(args.source, args.destination)
    except SafeError as error:
        print(json.dumps({'ok': False, 'error': error.code}))
        return 2
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
