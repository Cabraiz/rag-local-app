"""One-shot, collision-safe layout migration. Never touches secrets or volumes.

Uses byte-preserving moves and explicit mechanical path corrections. Original
edited bytes are backed up outside the source tree. Receipts remain immutable.
"""
from datetime import datetime, timezone
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path('D:/RAG-Local').resolve()
APP = ROOT / 'app'
STATE = ROOT / '.local/organization'
STAMP = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
RUN = STATE / STAMP
MOVES = {}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add(old, new):
    old, new = (ROOT / old).resolve(), (ROOT / new).resolve()
    if not old.is_relative_to(ROOT) or not new.is_relative_to(ROOT):
        raise ValueError('MOVE_OUTSIDE_WORKSPACE')
    if not old.is_file() or new.exists() or old == new:
        raise ValueError('INVALID_MOVE:' + str(old))
    if old in MOVES or new in MOVES.values():
        raise ValueError('MOVE_COLLISION')
    MOVES[old] = new


def groups(base, assignments):
    found = set()
    for group, names in assignments.items():
        for name in names.split():
            if name in found:
                raise ValueError('DUPLICATE_GROUP:' + name)
            found.add(name)
            add(f'{base}/{name}', f'{base}/{group}/{name}')
    unexpected = {p.name for p in (ROOT / base).glob('*.py')} - found
    if unexpected:
        raise ValueError('UNCLASSIFIED_FILES:' + str(sorted(unexpected)))


def plan():
    groups('app/src/rag_app', {
        'entrypoints': 'api.py bootstrap.py application.py lab_roles.py',
        'domain': 'domain.py assistant_policy.py',
        'runtime': 'process.py config.py resilience.py observability.py safe_logging.py broker.py delivery.py cache.py',
        'persistence': 'ledger.py catalog.py',
        'retrieval': 'corpus.py publication.py documents.py document_parser.py embedding_view.py neural_client.py semantic_policy.py',
        'models': 'adk_workflow.py gemini_grounded.py gemini_lab.py',
        'integrations': 'atlassian_lab.py github_lab.py remote_feed.py feed_server.py feed_client.py integration_status.py',
        '_keep': '__init__.py',
    })
    # The public package remains in place, not inside a compatibility folder.
    MOVES.pop(APP / 'src/rag_app/__init__.py')
    add('app/src/rag_app/schema.sql', 'app/src/rag_app/persistence/schema.sql')
    groups('app/advanced', {
        'models': 'free_model.py',
        'tasks': 'task_lab.py task_qa.py task_report.py task_report_checks.py tool_surface_diagnostic.py',
        'evaluation': 'judge_lab.py judge_qa.py',
        'dependencies': 'dependency_checks.py seal_dependencies.py',
        'checks': 'retry_checks.py',
    })
    for path in (APP / 'advanced/datasets').glob('*'):
        if path.is_file():
            add(path.relative_to(ROOT), 'app/advanced/evaluation/datasets/' + path.name)
    for name in ('requirements.in', 'requirements.lock', 'compile-lock.ps1', 'verify-dependencies.ps1'):
        add('app/advanced/' + name, 'app/advanced/dependencies/' + name)
    add('app/advanced/Dockerfile', 'app/advanced/containers/Dockerfile')
    groups('app/tests', {
        'shared': 'http_fixture.py functional_smoke.py helper_regression.py',
        'rag': 'rag_fixture.py current_semantic_fixture.py grounding_recall_fixture.py current_queue_fixture.py',
        'documents': 'document_fixture.py document_restart_regression.py document_retry_regression.py publication_checks.py qdrant_boot_fixture.py',
        'semantic': 'semantic_fixture.py shared_index_fixture.py calibration_probe.py neural_validation_fixture.py embedding_view_checks.py',
        'assistant': 'assistant_policy_checks.py assistant_policy_smoke.py meal_bill_fixture.py meal_process_fixture.py meal_policy_smoke.py',
        'atlassian': 'atlassian_controls.py diagnose_atlassian_auth.py diagnose_atlassian_fixture.py diagnose_atlassian_remote.py',
        'github': 'github_controls.py github_response_shape.py',
        'mcp': 'feed_checks.py feed_smoke.py remote_feed_probe.py mcp_evidence_checks.py mcp_evidence_gate.py mcp_gate_failure_checks.py',
        'gemini': 'gemini_controls.py gemini_grounded_checks.py gemini_grounded_diagnostic.py gemini_private_diagnostic.py gemini_rag_smoke.py gemini_sdk_contract.py',
        'provider': 'provider_billing_checks.py provider_card_receipt.py provider_usage_checks.py provider_usage_proof.py',
        'security': 'headers_fixture.py profile_image_fixture.py profile_smoke.py roles_smoke.py sdk_logging_fixture.py diagnose_rag_failure.py',
        'observability': 'observability_fixture.py observability_retry_regression.py collector_ports_regression.py telemetry_route_fixture.py tracing_sdk_fixture.py tracing_status_fixture.py',
        'resilience': 'resilience_fixture.py resilience_load.py resilience_qa_cleanup.py resilience_runtime_proof.py reliability_gate.py reliability_profile.py restore_lifecycle_checks.py cache_expiry_checks.py cache_expiry_oracle.py',
        'lifecycle': 'restart_fixture.py delivery_fixture.py',
        'cards': 'card_queue_fixture.py queue_withdrawal_fixture.py segment_fixture.py documentation_fixture.py',
        'receipts': 'calibration_regression_receipt.py queue_regression_receipt.py queue_withdrawal_receipt.py retention_race_receipt.py revocation_compat_receipt.py semantic_regression_receipt.py',
    })


def more_plan():
    add('app/README.md', 'docs/history/application/implementation-notes-20261002.md')
    groups('app/tools', {
        'cards': 'card_queue.py backlog_status.py',
        'revalidation': 'revalidate_local_cards.py revalidate_offline_bugs.py advanced-card-gate.py',
        'receipts': 'derive_guard_receipt.py derive_readiness_receipt.py derive-task-surface-receipt.py finalize-resilience-proof.py reliability-card-receipts.py',
        'ingestion': 'demo_seed.py publish_mcp_evidence.py publish_meal_policy.py download_public_embedding.py',
        'runtime': 'init_lab.py prepare-resilience.py update-reliability-runtime.py',
        'diagnostics': 'diagnose-free-judge.py',
    })
    for path in (APP / 'tools').glob('*.ps1'):
        add(path.relative_to(ROOT), 'app/tools/runtime/' + path.name)
    add('app/save-jira-token.ps1', 'app/tools/credentials/save-jira-token.ps1')
    runtime = {'compose.yaml', 'compose.retrieval.yaml', 'compose.semantic.yaml',
               'compose.integrations.yaml', 'compose.documents.yaml', 'compose.delivery.yaml'}
    resilience = {'compose.resilience.yaml', 'compose.resilience-integrations.yaml'}
    observability = {'compose.observability.yaml', 'compose.grafana.yaml'}
    for path in APP.glob('compose*.yaml'):
        category = ('runtime' if path.name in runtime else 'resilience' if path.name in resilience
                    else 'observability' if path.name in observability else 'qa' if 'qa' in path.name or 'load' in path.name else 'labs')
        add(path.relative_to(ROOT), f'app/infrastructure/compose/{category}/{path.name}')
    for path in APP.glob('Dockerfile*'):
        category = 'backend' if path.name in ('Dockerfile', 'Dockerfile.resilience') else 'integrations'
        add(path.relative_to(ROOT), f'app/infrastructure/images/{category}/{path.name}')
    for name in ('requirements.in', 'requirements.lock', 'resilience-requirements.lock'):
        add('app/' + name, 'app/infrastructure/dependencies/backend/' + name)
    add('app/mcp-requirements.lock', 'app/infrastructure/dependencies/integrations/mcp-requirements.lock')
    add('app/otel-collector.yaml', 'app/monitoring/collector/otel-collector.yaml')
    add('app/rabbitmq-enabled-plugins', 'app/infrastructure/broker/rabbitmq-enabled-plugins')
    for base in ('app', 'eval', 'adk', 'tmp'):
        for path in (ROOT / base).glob('*.log'):
            name = path.name
            category = ('integrations' if name.startswith(('atlassian', 'mcp', 'feed'))
                        else 'gemini' if 'gemini' in name else 'advanced' if 'advanced' in name
                        else 'semantic' if any(v in name for v in ('semantic', 'calibration', 'retrieval'))
                        else 'resilience' if any(v in name for v in ('resilience', 'redis', 'rabbitmq', 'reliability'))
                        else 'monitoring' if any(v in name for v in ('tracing', 'grafana', 'collector', 'otel', 'observability', 'prometheus'))
                        else 'regression')
            stage = ('build' if any(v in name for v in ('build', 'install', 'lock', 'pull', 'deps', 'dependency'))
                     else 'recovery' if 'restore' in name else 'runtime' if any(v in name for v in ('start', 'runtime', 'rollout'))
                     else 'checks')
            add(path.relative_to(ROOT), f'eval/logs/{category}/{stage}/{base}/{name}')
    for name in ('gemini-controls-final.json', 'gemini-controls-35-final.json'):
        add('app/' + name, 'eval/history/gemini/' + name)
    for path in (ROOT / 'eval').glob('*.json'):
        add(path.relative_to(ROOT), 'eval/history/resilience/' + path.name)
    for path in (ROOT / 'eval').glob('*.md'):
        add(path.relative_to(ROOT), 'docs/acceptance/interface/' + path.name)
    docs_plan()


def docs_plan():
    for path in (ROOT / 'docs/architecture').iterdir():
        if not path.is_file():
            continue
        name = path.name
        if name in ('cards.json', 'backlog-resolution-20261002.json', 'backlog-results-20261002.md', 'card-loop-acceptance.md', 'queue-current-contract-20261001.md'):
            dest = 'docs/cards'
        elif name.endswith('.py'):
            dest = 'docs/audits/scripts'
        elif name.startswith('validation-') or name == 'receipt-eraser.json':
            dest = 'docs/history/architecture-checks'
        elif name.endswith(('.txt', '.png', '.jpg')):
            dest = 'docs/diagrams/rag' if name.startswith('rag-') else 'docs/diagrams/application'
        elif name.endswith(('.tsv', '.json')):
            dest = 'docs/architecture/contracts'
        elif 'acceptance' in name:
            category = ('integrations' if name.startswith(('atlassian', 'remote-mcp', 'account-'))
                        else 'rag' if name.startswith(('retrieval', 'meal', 'unified'))
                        else 'models' if name.startswith(('gemini', 'advanced')) else 'operations')
            dest = 'docs/acceptance/' + category
        elif name.endswith('-runtime.md') or name == 'online-setup.md':
            dest = 'docs/operations'
        elif name.startswith('reliability-'):
            dest = 'docs/quality/reliability'
        elif name == 'application-v1.md':
            dest = 'docs/history/design'
        else:
            dest = 'docs/architecture/decisions'
        add(path.relative_to(ROOT), dest + '/' + name)


def maintained():
    for base in ('app', 'docs', 'frontend', 'carrefour-challenge'):
        for path in (ROOT / base).rglob('*'):
            if not path.is_file() or any(v in path.parts for v in ('.local', '.venv', '__pycache__', 'model', 'data')):
                continue
            if 'history' in MOVES.get(path, path).parts or 'diagrams' in MOVES.get(path, path).parts:
                continue
            if path.suffix.lower() in ('.py', '.ps1', '.md', '.yaml', '.yml', '.toml', '.txt', '.json', '.js', '.html', '.css') or path.name.startswith('Dockerfile'):
                yield path


def bootstrap_header():
    return '''# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

'''


def rewrite(path, old, content):
    # Rewrite only maintained files, never historical JSON receipts or SQL data.
    original = content
    rel = old.relative_to(ROOT).as_posix()
    mapping = {p.relative_to(ROOT).as_posix(): n.relative_to(ROOT).as_posix() for p, n in MOVES.items()}
    # Preserve the public Python module names; physical module paths change only.
    for before, after in sorted(mapping.items(), key=lambda item: -len(item[0])):
        if before.startswith(('app/', 'docs/')):
            content = content.replace(before, after).replace(before.replace('/', '\\'), after.replace('/', '\\'))
    # Compose and image filenames in app-root-relative script/config expressions.
    if path.suffix in ('.py', '.ps1', '.md', '.yaml', '.yml'):
        for before, after in mapping.items():
            if before.startswith('app/compose'):
                filename, replacement = Path(before).name, after[len('app/'):]
                for q in ("'", '"'):
                    content = content.replace(q + filename + q, q + replacement + q)
                if path.suffix == '.md':
                    content = re.sub(r'(?<![/\\\w.-])' + re.escape(filename) + r'(?![\w.-])', replacement, content)
    # Ensure every compose invocation preserves its original app project root.
    if path.suffix in ('.py', '.ps1') and rel.startswith(('app/tests/', 'app/tools/')):
        content = re.sub(r"(['\"]compose['\"]\s*,)(?!\s*['\"]--project-directory)", r"\1 '--project-directory', str(APP)," if path.suffix == '.py' else r"\1 '--project-directory', 'D:\\RAG-Local\\app',", content)
    if path.suffix == '.py' and rel.startswith(('app/tests/', 'app/tools/', 'docs/audits/', 'docs/architecture/')):
        # Constant-depth roots are replaced by stable named anchors, not deeper parents.
        content = re.sub(r'Path\(__file__\)\.resolve\(\)\.parents\[2\]', '_workspace_root', content)
        content = re.sub(r'Path\(__file__\)\.resolve\(\)\.parents\[1\]', '(_workspace_root / "app")', content)
        content = re.sub(r"Path\(__file__\)\.resolve\(\)\.parents\[1\]\s*/\s*['\"]tools/card_queue.py['\"]", '_named_file(_workspace_root / "app/tools", "card_queue.py")', content)
        # APP is needed by compose constructors without changing each script's ROOT contract.
        content = bootstrap_header() + 'APP = _workspace_root / "app"\n\n' + content
    if path.suffix == '.md':
        # Resolve links from the ORIGINAL document location before rewriting relative paths.
        def link(match):
            label, target = match.group(1), match.group(2)
            if target.startswith(('https:', 'http:', '#', 'mailto:')):
                return match.group(0)
            clean, sep, anchor = target.strip('<>').partition('#')
            candidate = (old.parent / clean).resolve()
            destination = MOVES.get(candidate, candidate)
            if candidate in MOVES or old != path:
                relative = os.path.relpath(destination, path.parent).replace('\\', '/')
                return '[' + label + '](' + relative + (sep + anchor if sep else '') + ')'
            return match.group(0)
        # Link text is handled using original targets to avoid duplicate prefix rewrites.
        content = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', link, original)
        for before, after in mapping.items():
            content = content.replace('D:\\RAG-Local\\' + before.replace('/', '\\'), 'D:\\RAG-Local\\' + after.replace('/', '\\'))
        for before, after in mapping.items():
            if before.startswith('app/compose'):
                content = re.sub(r'(?<![/\\\w.-])' + re.escape(Path(before).name) + r'(?![\w.-])', after[len('app/'):], content)
    return content


def main():
    # The lists are literal reviewed classification, not a count-based partition.
    plan()
    more_plan()
    if '--dry-run' in sys.argv:
        counts = {}
        for old, new in MOVES.items():
            counts[str(new.parent.relative_to(ROOT))] = counts.get(str(new.parent.relative_to(ROOT)), 0) + 1
        print(json.dumps(dict(moves=len(MOVES), destination_folders=len(counts), over_limit={p: n for p, n in counts.items() if n >= 20}, missing_sources=[])))
        return
    RUN.mkdir(parents=True, exist_ok=False)
    before = {p: p.read_bytes() for p in maintained()}
    # No secrets, .local runtime, models, DB or proof runs enter this source snapshot.
    records = [dict(**{'from': p.relative_to(ROOT).as_posix(), 'to': n.relative_to(ROOT).as_posix()}, sha256=sha(p), bytes=p.stat().st_size) for p, n in MOVES.items()]
    (RUN / 'planned.json').write_text(json.dumps(records, indent=2), encoding='utf8')
    for old, new in MOVES.items():
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
        if sha(new) != next(v['sha256'] for v in records if v['from'] == old.relative_to(ROOT).as_posix()):
            raise RuntimeError('MOVE_HASH_MISMATCH')
    edited = []
    for old, raw in before.items():
        path = MOVES.get(old, old)
        if old.name == 'reorganize_workspace.py' or old.name == 'workspace.py':
            continue
        text = raw.decode('utf-8-sig')
        updated = rewrite(path, old, text)
        if updated != text:
            backup = RUN / 'originals' / old.relative_to(ROOT)
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(raw)
            path.write_text(updated, encoding='utf8', newline='\n')
            edited.append(dict(path=path.relative_to(ROOT).as_posix(), original=old.relative_to(ROOT).as_posix(), before=hashlib.sha256(raw).hexdigest(), after=sha(path)))
    manifest = dict(created_utc=datetime.now(timezone.utc).isoformat(), root=str(ROOT), moves=records, mechanically_edited=edited,
                    preserved='secrets, installed dependencies, model weights, receipts, databases and Docker volumes',
                    backup=str(RUN))
    (ROOT / 'docs/organization/relocations.json').write_text(json.dumps(manifest, indent=2), encoding='utf8')
    (RUN / 'applied.json').write_text(json.dumps(manifest, indent=2), encoding='utf8')
    print(json.dumps(dict(moved=len(records), edited=len(edited), backup=str(RUN))))


if __name__ == '__main__':
    main()
