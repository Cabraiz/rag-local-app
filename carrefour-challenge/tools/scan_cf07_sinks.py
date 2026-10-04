"""Literal/Unicode-normalized fictional canary scan, without echoing matches."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import unicodedata

CANARIES = ('Pessoa Canario ZQX', 'Doutora Canario QRS', '123.456.789-00',
            'canario.zqx@example.invalid', '(11) 90000-1234',
            'Rua Canario Ficticia 987', '29/02/2000', 'Ma\u0301rcia Canario WUV',
            'Pessoa Sentinela ZQX', 'pessoa.sentinela@example.invalid',
            '90000-1234', 'Doutor Ficticio QRS')


def normalized(value):
    # Also cover escaped Unicode inside nested JSON strings/event payloads.
    value = re.sub(r'\\u([0-9a-fA-F]{4})', lambda match: chr(int(match[1], 16)), value)
    return ''.join(c for c in unicodedata.normalize('NFKD', value.casefold()) if c.isalnum())


def scan(text):
    candidates = [text]
    # Docker prefixes JSON log records with service names. Decode these records
    # before Unicode scanning instead of treating JSON escaping as redaction.
    for line in text.splitlines():
        for start in (0, line.find('{'), line.find('[')):
            if start < 0:
                continue
            try:
                value = json.loads(line[start:])
                candidates.append(json.dumps(value, ensure_ascii=False))
            except (ValueError, TypeError):
                pass
    hits = []
    for index, canary in enumerate(CANARIES):
        if any(canary in value or normalized(canary) in normalized(value) for value in candidates):
            hits.append(index)
    return {'pii_absent': not hits, 'matched_canary_indexes': hits,
            'sha256': hashlib.sha256(text.encode()).hexdigest(), 'canary_count': len(CANARIES)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('files', nargs='+')
    args = parser.parse_args()
    rows = {str(Path(name)): scan(Path(name).read_text(encoding='utf8')) for name in args.files}
    print(json.dumps(rows))
    return 0 if all(value['pii_absent'] for value in rows.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
