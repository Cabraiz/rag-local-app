# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Scoped deterministic regressions: planning + active ADK guard, no provider calls."""
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = _workspace_root
sys.path.insert(0, str(ROOT / 'app/src'))
from rag_app.assistant_policy import Target, plan_action, lab_request_guard, POLICY_VERSION
from rag_app.adk_workflow import AdkAbstentionWorkflow, AdkRetrievalWorkflow

CREATE = frozenset({'create_card'})
BOTH = frozenset({'create_card', 'move_card'})
KAN = Target('board:2', 'RAG Local Lab', 'KAN', BOTH, ('Task',), ('done:31',))
OTHER = Target('board:3', 'Outro Kanban autorizado', 'ABC', CREATE, ('Bug',))
checks = []


def check(name, passed):
    assert passed, name
    checks.append(name)


def plan(action='create_card', slots=None, caps=BOTH, targets=(KAN,)):
    return plan_action(action, slots or {}, capabilities=caps, targets=targets)


def run():
    check('permission_before_discovery', plan(caps=frozenset(), targets=(KAN, OTHER)).choices == ())
    check('no_permission_denied', plan(caps=frozenset()).stage == 'DENIED')
    check('unknown_action_denied', plan(action='delete_card').stage == 'DENIED')
    check('no_target_denied', plan(targets=()).stage == 'DENIED')
    p = plan(targets=(KAN, OTHER))
    check('ask_which_kanban', p.stage == 'NEEDS_INPUT' and p.message == 'Em qual Kanban?')
    check('only_authorized_choices', p.choices == ((KAN.id, KAN.label), (OTHER.id, OTHER.label)))
    p = plan(targets=(KAN, replace(OTHER, actions=frozenset())))
    check('single_target_not_asked_again', p.target_id == KAN.id and p.missing == ('title', 'issue_type'))
    check('invalid_target_denied', plan(slots={'target_id': 'board:secret'}).stage == 'DENIED')
    check('duplicate_target_denied', plan(targets=(KAN, KAN)).stage == 'DENIED')
    check('empty_title_missing', plan(slots={'title': ' ', 'issue_type': 'Task'}).missing == ('title',))
    check('bool_title_missing', plan(slots={'title': True, 'issue_type': 'Task'}).missing == ('title',))
    p = plan(slots={'title': 'Teste', 'issue_type': 'Inventado'})
    check('real_issue_type_choices', p.choices == (('Task', 'Task'),) and p.stage == 'NEEDS_INPUT')
    check('empty_discovery_metadata_denied', plan(slots={'title': 'Teste', 'issue_type': 'Task'},
          targets=(replace(KAN, issue_types=()),)).stage == 'DENIED')
    p = plan(slots={'title': 'Teste', 'issue_type': 'Task'}, targets=(replace(KAN, required_fields=('description',)),))
    check('jira_required_fields', p.missing == ('description',))
    p = plan(slots={'title': 'Teste', 'issue_type': 'Task', 'role': 'admin', 'project': 'SECRET'})
    check('plan_not_execution', p.stage == 'PLAN_ONLY' and 'Nada foi executado' in p.message)
    check('model_cannot_change_target', p.target_id == KAN.id)
    check('move_missing_fields', plan('move_card').missing == ('issue_id', 'transition'))
    p = plan('move_card', {'issue_id': 'KAN-1', 'transition': 'Inventada'})
    check('actual_transition_choices', p.choices == (('done:31', 'done:31'),))
    check('empty_transition_metadata_denied', plan('move_card', {'issue_id': 'KAN-1', 'transition': 'done:31'},
          targets=(replace(KAN, transitions=()),)).stage == 'DENIED')
    check('issue_outside_project', plan('move_card', {'issue_id': 'ABC-1', 'transition': 'done:31'}).stage == 'DENIED')
    check('invalid_issue_id', plan('move_card', {'issue_id': 'KAN-0', 'transition': 'done:31'}).stage == 'DENIED')
    check('move_plan_only', plan('move_card', {'issue_id': 'KAN-1', 'transition': 'done:31'}).stage == 'PLAN_ONLY')
    for i, question in enumerate((
        'Crie um card no kanban', 'quero criar um card', 'Por favor, mova o card KAN-1',
        'Eu quero que você crie um card no kanban', 'poderia criar uma tarefa no kanban?',
        'mova o KAN-1 para Em andamento',
    )):
        result = lab_request_guard(question)
        check('guard_action_' + str(i), result is not None and result.kind == 'ABSTAIN' and not result.citations
              and 'Nenhum card foi criado ou movido' in result.text)
    for i, question in enumerate(('Quero todos os preços dos alimentos', 'lista completa dos preços dos alimentos')):
        result = lab_request_guard(question)
        check('guard_completeness_' + str(i), result is not None and result.kind == 'ABSTAIN'
              and 'Limites de reembolso não são preços' in result.text)
    for i, question in enumerate((
        'Qual o limite de alimentação durante uma viagem?', 'Como criar um card no kanban?',
        'Como mover um card?', 'Não crie um card, explique o processo.',
        'Qual o título do documento Crie um card?', 'O que diz a política de preços?',
        'Como são definidos todos os preços dos alimentos?',
    )):
        check('knowledge_not_action_' + str(i), lab_request_guard(question) is None)
    async def guarded_workflows():
        with patch('rag_app.corpus.retrieve', side_effect=AssertionError('UNSUPPORTED_RETRIEVAL')) as retrieval:
            for workflow in (AdkAbstentionWorkflow(), AdkRetrievalWorkflow()):
                for question in ('Crie um card no kanban', 'Quero todos os preços dos alimentos'):
                    request = {'id': 'synthetic-guard', 'fence': 1, 'question': question}
                    result = await workflow.run(request)
                    check(type(workflow).__name__ + ':' + question, result.kind == 'ABSTAIN' and not result.citations)
            check('guard_skips_retrieval', retrieval.call_count == 0)
    asyncio.run(guarded_workflows())


if __name__ == '__main__':
    names = ('app/src/rag_app/domain/assistant_policy.py', 'app/src/rag_app/models/adk_workflow.py',
             'app/tests/assistant/assistant_policy_checks.py')
    def frozen():
        return {n: hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names}
    hashes = frozen()
    rounds = []
    for _ in range(2):
        checks = []
        run()
        assert frozen() == hashes, 'SOURCE_CHANGED'
        rounds.append(checks)
    print(json.dumps({'passed': True, 'policy_version': POLICY_VERSION,
        'scope': 'same-executor deterministic planning + ADK guard; not live writes or independent blind audit',
        'round_checks': [len(r) for r in rounds], 'rounds': rounds, 'source_sha256': hashes}))
