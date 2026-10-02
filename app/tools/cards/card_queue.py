# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Local, one-writer card journal. No model calls, remote actions or auto-dispatch."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from workspace import resolve_source

ROOT = _workspace_root
STATE = ROOT / '.local' / 'card-execution'
DB = STATE / 'queue.sqlite3'


def now():
    return datetime.now(timezone.utc).isoformat()


def connect(path=DB):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    db.executescript('''CREATE TABLE IF NOT EXISTS cards(
      seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,title TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'QUEUED',evidence_type TEXT NOT NULL,
      criteria TEXT NOT NULL,parent TEXT,fingerprint TEXT UNIQUE,reason TEXT,receipt TEXT);
      CREATE UNIQUE INDEX IF NOT EXISTS one_writer ON cards(status) WHERE status='RUNNING';
      CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,card TEXT NOT NULL,kind TEXT NOT NULL,detail TEXT NOT NULL);''')
    return db


def event(db, card, kind, detail):
    db.execute('INSERT INTO events(at,card,kind,detail) VALUES (?,?,?,?)',
               (now(), card, kind, detail[:1000]))


def initialize(db):
    seed = json.loads((ROOT / 'docs/cards/cards.json').read_text(encoding='utf8'))
    for card in seed['cards']:
        db.execute('INSERT OR IGNORE INTO cards(id,title,evidence_type,criteria) VALUES (?,?,?,?)',
                   (card['id'], card['title'], card['evidence_type'], json.dumps(card['criteria'])))


def claim(db):
    if db.execute("SELECT 1 FROM cards WHERE status='RUNNING'").fetchone():
        raise ValueError('WRITER_ALREADY_ACTIVE')
    row = db.execute("SELECT * FROM cards WHERE status='QUEUED' ORDER BY seq LIMIT 1").fetchone()
    if row:
        db.execute("UPDATE cards SET status='RUNNING',reason=NULL WHERE id=?", (row['id'],))
        event(db, row['id'], 'START', 'FIFO')
    return row['id'] if row else None


def block(db, card, reason):
    if not reason.strip():
        raise ValueError('BLOCK_REASON_REQUIRED')
    changed = db.execute("UPDATE cards SET status='BLOCKED',reason=? WHERE id=? AND status='RUNNING'",
                         (reason[:1000], card)).rowcount
    if changed != 1:
        raise ValueError('CARD_NOT_RUNNING')
    event(db, card, 'BLOCKED', reason)


def resume(db, card, reason):
    if db.execute("SELECT 1 FROM cards WHERE status='RUNNING'").fetchone():
        raise ValueError('WRITER_ALREADY_ACTIVE')
    if not reason or not reason.strip():
        raise ValueError('RESUME_REASON_REQUIRED')
    if db.execute("SELECT 1 FROM cards WHERE parent=? AND status NOT IN ('DONE','WITHDRAWN_USER_PAUSE')",(card,)).fetchone():
        raise ValueError('OPEN_CHILD_BUG')
    changed=db.execute("UPDATE cards SET status='RUNNING',reason=NULL WHERE id=? AND status IN ('BLOCKED','NEEDS_FIX')",(card,)).rowcount
    if changed!=1:
        raise ValueError('CARD_NOT_RESUMABLE')
    event(db,card,'RESUME',reason)


def review(db):
    invalidated=[]
    for row in db.execute("SELECT id,receipt FROM cards WHERE status='DONE'").fetchall():
        try:
            receipt=json.loads(Path(row['receipt']).read_text(encoding='utf8'))
            unchanged=all(hashlib.sha256(resolve_source(name, root=ROOT).read_bytes()).hexdigest()==digest
                          for name,digest in receipt['sources_sha256'].items())
        except (OSError,ValueError,KeyError,TypeError):
            unchanged=False
        if not unchanged:
            db.execute("UPDATE cards SET status='NEEDS_FIX',reason='SOURCE_CHANGED_RESET_STREAK' WHERE id=?",(row['id'],))
            event(db,row['id'],'APPROVAL_INVALIDATED','Changed or missing frozen source; historical receipt retained')
            invalidated.append(row['id'])
    # Propagate a stale child approval through every already-approved ancestor.
    # Iterate because ancestors may appear before children in FIFO order.
    while True:
        parents=db.execute("SELECT id FROM cards c WHERE status='DONE' AND EXISTS (SELECT 1 FROM cards child WHERE child.parent=c.id AND child.status NOT IN ('DONE','WITHDRAWN_USER_PAUSE'))").fetchall()
        if not parents:
            break
        for row in parents:
            db.execute("UPDATE cards SET status='NEEDS_FIX',reason='CHILD_APPROVAL_INVALIDATED_RESET_STREAK' WHERE id=?",(row['id'],))
            event(db,row['id'],'APPROVAL_INVALIDATED','Child approval invalidated; historical receipt retained')
            invalidated.append(row['id'])
    return invalidated


def append_bug(db, parent, title, proof):
    if not db.execute('SELECT 1 FROM cards WHERE id=?', (parent,)).fetchone():
        raise ValueError('UNKNOWN_PARENT')
    if not title.strip() or not proof.strip():
        raise ValueError('REPRODUCTION_OR_PROOF_REQUIRED')
    fingerprint = hashlib.sha256((parent + '\n' + title.strip() + '\n' + proof.strip()).encode()).hexdigest()
    old = db.execute('SELECT id FROM cards WHERE fingerprint=?', (fingerprint,)).fetchone()
    if old:
        return old['id']
    seq = db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM cards').fetchone()[0]
    card = 'BUG-' + str(seq).zfill(3)
    db.execute('INSERT INTO cards(id,title,evidence_type,criteria,parent,fingerprint,reason) VALUES (?,?,?,?,?,?,?)',
               (card, title[:500], 'verified_regression', json.dumps(['reproduction', 'two_regression_rounds']),
                parent, fingerprint, proof[:1000]))
    event(db, card, 'BUG_APPENDED_LAST', proof)
    # A bug linked to a formerly approved card invalidates its final approval.
    db.execute("UPDATE cards SET status='NEEDS_FIX' WHERE id=? AND status='DONE'", (parent,))
    return card


def complete(db, card, receipt_path):
    path = Path(receipt_path).resolve()
    if not path.is_relative_to((ROOT / 'eval').resolve()):
        raise ValueError('RECEIPT_OUTSIDE_EVAL')
    receipt = json.loads(path.read_text(encoding='utf8'))
    row = db.execute("SELECT * FROM cards WHERE id=? AND status='RUNNING'", (card,)).fetchone()
    if not row:
        raise ValueError('CARD_NOT_RUNNING')
    if db.execute("SELECT 1 FROM cards WHERE parent=? AND status NOT IN ('DONE','WITHDRAWN_USER_PAUSE')", (card,)).fetchone():
        raise ValueError('OPEN_CHILD_BUG')
    if (receipt.get('card_id') != card or receipt.get('evidence_type') != row['evidence_type']
            or receipt.get('consecutive_passes') != 2 or receipt.get('complete') is not True
            or not set(json.loads(row['criteria'])) <= set(receipt.get('criteria_passed', []))):
        raise ValueError('SCOPED_TWO_ROUND_PROOF_REQUIRED')
    if not receipt.get('sources_sha256'):
        raise ValueError('FROZEN_SOURCES_REQUIRED')
    for name, digest in receipt['sources_sha256'].items():
        source = resolve_source(name, root=ROOT)
        if not source.is_relative_to(ROOT) or source.is_relative_to(STATE.parent):
            raise ValueError('INVALID_PROOF_SOURCE')
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise ValueError('SOURCE_CHANGED_RESET_STREAK')
    db.execute("UPDATE cards SET status='DONE',receipt=?,reason=NULL WHERE id=?", (str(path), card))
    event(db, card, 'DONE_SCOPED', str(path.relative_to(ROOT)))


def projection(db):
    rows = [dict(row) for row in db.execute('SELECT * FROM cards ORDER BY seq')]
    for row in rows:
        row['criteria'] = json.loads(row['criteria'])
        row.pop('fingerprint')
    events = [dict(row) for row in db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 1')]
    running = next((row['id'] for row in rows if row['status'] == 'RUNNING'), None)
    done = sum(row['status'] == 'DONE' for row in rows)
    withdrawn = sum(row['status'] == 'WITHDRAWN_USER_PAUSE' for row in rows)
    stamp = events[0]['at'] if events else now()
    first=db.execute("SELECT min(at) AS at FROM events WHERE kind='START'").fetchone()['at']
    value = {'status': 'running' if running else 'waiting', 'startedAt': first or stamp,
             'lastProgressAt': stamp, 'currentCard': running, 'progressCurrent': done + withdrawn,
             'progressTotal': len(rows), 'completedCards': done, 'withdrawnCards': withdrawn,
             'all_cards_complete': bool(rows) and done + withdrawn == len(rows),
             'cards': rows, 'generatedAt': now()}
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = STATE / 'progress.tmp'
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    os.replace(tmp, STATE / 'progress.json')
    return {'current': running, 'done': done, 'withdrawn': withdrawn, 'total': len(rows),
            'blocked': sum(row['status'] == 'BLOCKED' for row in rows)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['init', 'next', 'block', 'bug', 'complete', 'status','resume','review','checkpoint'])
    parser.add_argument('--card')
    parser.add_argument('--reason')
    parser.add_argument('--title')
    parser.add_argument('--proof')
    parser.add_argument('--receipt')
    args = parser.parse_args()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if args.command == 'init': initialize(db)
        elif args.command == 'next': print(json.dumps({'started': claim(db)}))
        elif args.command == 'block': block(db, args.card, args.reason)
        elif args.command == 'bug': print(json.dumps({'appended': append_bug(db, args.card, args.title, args.proof)}))
        elif args.command == 'complete': complete(db, args.card, args.receipt)
        elif args.command == 'resume': resume(db,args.card,args.reason)
        elif args.command == 'review': print(json.dumps({'invalidated':review(db)}))
        elif args.command == 'checkpoint':
            if not args.reason or not db.execute("SELECT 1 FROM cards WHERE id=? AND status='RUNNING'",(args.card,)).fetchone():
                raise ValueError('ACTIVE_CARD_AND_MATERIAL_CHECKPOINT_REQUIRED')
            event(db,args.card,'CHECKPOINT',args.reason)
    with connect() as db:
        print(json.dumps(projection(db)))


if __name__ == '__main__':
    main()
