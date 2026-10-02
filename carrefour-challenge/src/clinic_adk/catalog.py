import hashlib
import json
from pathlib import Path
from .privacy import normalize, query_safe
from .errors import SafeError

PATH = Path('/app/data/exams.json')
class Catalog:
    def __init__(self, path=PATH):
        raw = Path(path).read_bytes()
        value = json.loads(raw)
        self.version = hashlib.sha256(raw).hexdigest()
        self.entries = value['exams']
        if len(self.entries) < 100:
            raise SafeError('CATALOG_TOO_SMALL')
        self.by_name, self.by_code = {}, {}
        for row in self.entries:
            if set(row) != {'code', 'name', 'aliases', 'evidence'}:
                raise SafeError('CATALOG_SCHEMA')
            if row['code'] in self.by_code or normalize(row['name']) in self.by_name:
                raise SafeError('CATALOG_DUPLICATE')
            self.by_code[row['code']] = row
            for name in [row['name'], *row['aliases']]:
                key = normalize(name)
                if key in self.by_name:
                    raise SafeError('CATALOG_AMBIGUOUS_ALIAS')
                self.by_name[key] = row

    def retrieve(self, names):
        if not isinstance(names, list) or not 1 <= len(names) <= 20:
            raise SafeError('INVALID_EXAM_LIST')
        found, unresolved = [], []
        for index, name in enumerate(names):
            row = self.by_name.get(query_safe(name))
            if not row:
                unresolved.append(index)
            elif row['code'] not in {r['code'] for r in found}:
                found.append({k: row[k] for k in ('name', 'code', 'evidence')})
        return {'ok': not unresolved, 'exams': found, 'unresolved_indices': unresolved,
                'catalog_version': self.version, 'catalog_count': len(self.entries)}
