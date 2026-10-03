import hashlib
import json
import re
from pathlib import Path
from .privacy import query_safe
from .errors import SafeError
from .file_input import bounded_file

PATH = Path('/app/data/exams.json')
class Catalog:
    def __init__(self, path=PATH):
        self._load(bounded_file(path, 1000000, 'CATALOG_SIZE_OR_PATH'))

    @classmethod
    def from_bytes(cls, raw):
        """Validate the exact bounded bytes that will be imported."""
        if not isinstance(raw, bytes) or len(raw) > 1000000:
            raise SafeError('CATALOG_SIZE_OR_PATH')
        catalog = cls.__new__(cls)
        catalog._load(raw)
        return catalog

    def _load(self, raw):
        def unique(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise SafeError('CATALOG_DUPLICATE_KEY')
                result[key] = item
            return result
        try:
            value = json.loads(raw, object_pairs_hook=unique,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError('number')))
        except (ValueError, UnicodeError, RecursionError) as error:
            if isinstance(error, SafeError):
                raise
            raise SafeError('CATALOG_SCHEMA') from None
        if (not isinstance(value, dict) or set(value) != {'schema_version', 'fictional', 'notice', 'exams'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or value['fictional'] is not True or not isinstance(value['notice'], str)
                or not 1 <= len(value['notice'].strip()) <= 1000 or not isinstance(value['exams'], list)):
            raise SafeError('CATALOG_SCHEMA')
        self.version = hashlib.sha256(raw).hexdigest()
        self.entries = value['exams']
        if len(self.entries) < 100:
            raise SafeError('CATALOG_TOO_SMALL')
        if len(self.entries) > 1000:
            raise SafeError('CATALOG_TOO_LARGE')
        self.by_name, self.by_code = {}, {}
        for row in self.entries:
            if (not isinstance(row, dict) or set(row) != {'code', 'name', 'aliases', 'evidence'}
                    or not isinstance(row['code'], str) or not re.fullmatch(r'FICT-[0-9]{3}', row['code'])
                    or not isinstance(row['aliases'], list) or len(row['aliases']) > 20
                    or not isinstance(row['evidence'], str) or not 1 <= len(row['evidence'].strip()) <= 2000):
                raise SafeError('CATALOG_SCHEMA')
            name_key = query_safe(row['name'])
            expected_evidence = (f"Ficha ficticia {row['code'][5:]}: {row['name']}; "
                                 'identificador de demonstracao, sem significado clinico oficial.')
            if row['evidence'] != expected_evidence:
                raise SafeError('CATALOG_EVIDENCE_REFERENCE')
            if row['code'] in self.by_code or name_key in self.by_name:
                raise SafeError('CATALOG_DUPLICATE')
            self.by_code[row['code']] = row
            for name in [row['name'], *row['aliases']]:
                key = query_safe(name)
                if key in self.by_name:
                    raise SafeError('CATALOG_AMBIGUOUS_ALIAS')
                self.by_name[key] = row

    def retrieve(self, names):
        if not isinstance(names, list) or not 1 <= len(names) <= 20:
            raise SafeError('INVALID_EXAM_LIST')
        found, unresolved, abstentions = [], [], []
        for index, name in enumerate(names):
            key = query_safe(name)
            row = self.by_name.get(key)
            if not row:
                unresolved.append(index)
                # Prefix matches classify uncertainty only. They never resolve a code
                # or add aliases/equivalences to the fictional catalog.
                candidates = {item['code'] for label, item in self.by_name.items()
                              if label.startswith(key)}
                reason = ('ambiguous' if len(candidates) > 1 else
                          'low_confidence' if candidates else 'not_found')
                abstentions.append({'index': index, 'reason': reason})
            elif row['code'] not in {r['code'] for r in found}:
                found.append({k: row[k] for k in ('name', 'code', 'evidence')})
        return {'ok': not unresolved, 'exams': [] if unresolved else found,
                'unresolved_indices': unresolved, 'abstentions': abstentions,
                'catalog_version': self.version, 'catalog_count': len(self.entries)}
