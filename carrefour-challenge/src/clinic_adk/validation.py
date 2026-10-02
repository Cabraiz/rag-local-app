"""Bounded, non-reflective diagnostics: untrusted keys are data too."""
FIELDS = frozenset(('body','path','query','schema_version','name','framework','transport',
    'model_mode','timeout_seconds','stages','kind','request_id','exam_codes',
    'catalog_version','appointment_id','status'))

def issues(rows):
    result = []
    for row in rows[:30]:
        parts = []
        for part in row.get('loc', ())[:8]:
            if type(part) is int:
                parts.append(str(min(max(part, 0), 100)))
            else:
                parts.append(part if part in FIELDS else '[extra]')
        result.append({'field': '.'.join(parts), 'type': row['type']})
    return result
