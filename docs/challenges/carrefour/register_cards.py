"""Append planned challenge cards to the existing local journal; never claim work."""
import hashlib
import importlib.util
import json
from contextlib import closing
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[3]
SOURCE = Path(__file__).with_name('cards.json')


def validate(document):
    if document.get('schema_version') != 1:
        raise ValueError('UNSUPPORTED_SCHEMA')
    policy = document['policy']
    if (policy['paid_actions'] is not False or policy['execution_started'] is not False
            or policy['remote_jira_writes'] is not False
            or policy['generated_agent_framework'] != 'google-adk-only'
            or policy['mcp_transport'] != 'legacy-http-sse-only'):
        raise ValueError('UNAUTHORIZED_SCOPE')
    cards = document['cards']
    ids = [card['id'] for card in cards]
    if ids != [f'CF-{number:02}' for number in range(1, 15)]:
        raise ValueError('INVALID_CARD_IDENTITIES')
    requirements = {row['id']: row for row in document['requirements']}
    if set(requirements) != {f'R{number:02}' for number in range(1, 13)}:
        raise ValueError('MISSING_REQUIREMENTS')
    for index, card in enumerate(cards):
        if (card['status'] != 'QUEUED' or not card['title'] or not card['evidence_type']
                or not card['criteria'] or len(card['criteria']) != len(set(card['criteria']))
                or not card['knowledge'] or not card['expected'] or not card['forbidden']
                or not card['minimum_proof'] or not card['references']):
            raise ValueError('MISSING_ACCEPTANCE_CONTRACT')
        if not set(card['dependencies']) <= set(ids[:index]):
            raise ValueError('INVALID_DEPENDENCY_ORDER')
        if not card['requirement_ids'] or not set(card['requirement_ids']) <= set(requirements):
            raise ValueError('INVALID_REQUIREMENT_REFERENCE')
    for requirement_id, requirement in requirements.items():
        linked = [card['id'] for card in cards if requirement_id in card['requirement_ids']]
        if not linked or linked != requirement['card_ids'] or requirement['page'] not in (1, 2):
            raise ValueError('INCOMPLETE_TRACEABILITY')
    return cards


def load_queue_module():
    spec = importlib.util.spec_from_file_location('existing_card_queue', ROOT / 'app/tools/cards/card_queue.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot(db):
    return [dict(row) for row in db.execute('SELECT * FROM cards ORDER BY seq')]


def digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def append(db, document, queue_module):
    cards = validate(document)
    before = snapshot(db)
    created = []
    for card in cards:
        criteria = json.dumps(card['criteria'])
        old = db.execute('SELECT * FROM cards WHERE id=?', (card['id'],)).fetchone()
        if old:
            if (old['title'] != card['title'] or old['evidence_type'] != card['evidence_type']
                    or json.loads(old['criteria']) != card['criteria']):
                raise ValueError('EXISTING_CARD_CONTRACT_CONFLICT')
            continue
        db.execute('INSERT INTO cards(id,title,evidence_type,criteria,reason) VALUES (?,?,?,?,?)',
                   (card['id'], card['title'], card['evidence_type'], criteria,
                    'PLANNED_ONLY; details: docs/challenges/carrefour/cards.json'))
        queue_module.event(db, card['id'], 'CHALLENGE_CARD_CREATED',
                           'Carrefour PDF backlog; append only; no claim or external write')
        created.append(card['id'])
    before_ids = {row['id'] for row in before}
    after = snapshot(db)
    retained = [row for row in after if row['id'] in before_ids]
    if before != retained:
        raise ValueError('PRIOR_CARD_STATE_CHANGED')
    new_rows = [row for row in after if row['id'] in created]
    if any(row['status'] != 'QUEUED' for row in new_rows):
        raise ValueError('UNAUTHORIZED_EXECUTION')
    if new_rows and before and min(row['seq'] for row in new_rows) <= max(row['seq'] for row in before):
        raise ValueError('FIFO_NOT_PRESERVED')
    return {'created': created, 'total': len(after), 'prior_cards_unchanged': True,
            'prior_snapshot_sha256': digest(before),
            'running_preserved': [row['id'] for row in before if row['status'] == 'RUNNING'],
            'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            'execution_started': False, 'remote_jira_writes': False}


def self_test(document, queue_module):
    with tempfile.TemporaryDirectory(prefix='cf-card-check-', dir=ROOT / 'tmp') as folder:
        if not Path(folder).resolve().is_relative_to((ROOT / 'tmp').resolve()):
            raise ValueError('TEMPORARY_PATH_OUTSIDE_WORKSPACE')
        with closing(queue_module.connect(Path(folder) / 'queue.sqlite3')) as db, db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("INSERT INTO cards(id,title,status,evidence_type,criteria) VALUES ('OLD-01','preserved','RUNNING','existing','[]')")
            first = append(db, document, queue_module)
            assert len(first['created']) == 14 and first['running_preserved'] == ['OLD-01']
            second = append(db, document, queue_module)
            assert second['created'] == [] and second['total'] == 15
            assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 14
        altered = json.loads(json.dumps(document))
        altered['cards'][0]['title'] = 'conflicting title'
        with closing(queue_module.connect(Path(folder) / 'queue.sqlite3')) as db, db:
            db.execute('BEGIN IMMEDIATE')
            try:
                append(db, altered, queue_module)
            except ValueError as error:
                assert str(error) == 'EXISTING_CARD_CONTRACT_CONFLICT'
            else:
                raise AssertionError('CONFLICT_NOT_BLOCKED')
        for mutate in (lambda value: value['cards'][0].update(status='DONE'),
                       lambda value: value['cards'][0].update(dependencies=['CF-14']),
                       lambda value: value['policy'].update(paid_actions=True)):
            invalid = json.loads(json.dumps(document))
            mutate(invalid)
            try:
                validate(invalid)
            except ValueError:
                pass
            else:
                raise AssertionError('INVALID_INPUT_NOT_BLOCKED')
    return {'self_test': 'passed', 'checks': ['traceability', 'append_fifo', 'active_writer_preserved',
            'idempotent_registration', 'no_duplicate_events', 'contract_conflict',
            'no_done_state', 'dependency_order', 'no_paid_scope']}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--register', action='store_true')
    args = parser.parse_args()
    document = json.loads(SOURCE.read_text(encoding='utf8'))
    queue_module = load_queue_module()
    print(json.dumps(self_test(document, queue_module)))
    if args.register:
        with closing(queue_module.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            receipt = append(db, document, queue_module)
        with closing(queue_module.connect()) as db, db:
            receipt['projection'] = queue_module.projection(db)
        print(json.dumps(receipt))


if __name__ == '__main__':
    main()
