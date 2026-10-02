"""Adversarial corpus contracts with controlled adapters, never real integration.

No holdout fitting, providers, Docker or canonical data. POSIX parser limits and
SQL locks are observed with test adapters; they still require real Central gates.
"""
import sys
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'app/workspace.py').is_file())
sys.path.insert(0, str(ROOT / 'app'))
from workspace import bootstrap
bootstrap()

from contextlib import contextmanager, nullcontext, redirect_stdout
import ast
import hashlib
import importlib
import io
import json
import types
import unittest
from unittest.mock import Mock, patch
from uuid import UUID, uuid5

import httpx
from rag_app import corpus, neural_client, publication, semantic_policy
from rag_app.domain import Identity, RequestError
import document_fixture
import rag_fixture
import current_queue_fixture

WHO = Identity('demo-a', 'demo-user')
RID = UUID('11111111-1111-1111-1111-111111111111')


def document():
    return dict(source_key='synthetic', title='Fonte', text='Evidência sintética.',
                media_type='text/plain', valid_until=None)


class Cursor:
    def __init__(self, one=None, rows=()):
        self.one, self.rows = one, list(rows)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows


class Database:
    def __init__(self, dispatch):
        self.dispatch, self.calls = dispatch, []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return self.dispatch(sql, params)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class CorpusContracts(unittest.TestCase):
    def rejected(self, action, code=None, status=None):
        with self.assertRaises(RequestError) as result:
            action()
        if code is not None:
            self.assertEqual(result.exception.code, code)
        if status is not None:
            self.assertEqual(result.exception.status, status)

    def test_bundle_malformed_developer_input_is_typed_rejection(self):
        for value in (None, [], 'invalid', 7, True):
            with self.subTest(value=repr(value)):
                self.rejected(lambda: corpus.validate_bundle([value]), status=422)
        for value in (None, [], 7, True, {}):
            with self.subTest(source_key=repr(value)):
                self.rejected(lambda: corpus.validate_bundle([{**document(), 'source_key': value}]),
                              status=422)

    def test_original_bundle_boundaries(self):
        corpus.validate_bundle([document()])
        for bundle, code in [([], 'INVALID_CORPUS_BUNDLE'),
                             ([document()] * 33, 'INVALID_CORPUS_BUNDLE'),
                             ([document(), document()], 'DUPLICATE_SOURCE_KEY'),
                             ([{**document(), 'text': 'bad\0text'}], 'INVALID_CORPUS_DOCUMENT'),
                             ([{**document(), 'text': '   '}], 'INVALID_CORPUS_DOCUMENT'),
                             ([{**document(), 'text': 'é' * 7000}], 'CORPUS_BYTES_LIMIT'),
                             ([{**document(), 'media_type': 'text/html'}], 'UNSUPPORTED_CORPUS_DOCUMENT'),
                             ([{**document(), 'source_key': '../escape'}], 'UNSUPPORTED_CORPUS_DOCUMENT'),
                             ([{**document(), 'tenant': 'other'}], 'INVALID_CORPUS_DOCUMENT'),
                             ([{**document(), 'valid_until': '2040-01-01'}], 'INVALID_CORPUS_EXPIRY')]:
            with self.subTest(code=code):
                self.rejected(lambda: corpus.validate_bundle(bundle), code)

    def test_escaped_surrogate_input_is_rejected_before_ingestion(self):
        for field in ('title', 'text'):
            with self.subTest(field=field):
                self.rejected(lambda: corpus.validate_bundle([{**document(), field: 'bad\ud800text'}]),
                              'INVALID_CORPUS_DOCUMENT', 422)

    def test_shared_layout_does_not_grow_per_release(self):
        for layout, name in ((corpus.INDEX_LAYOUT, 'rgl_shared_lexical_256_v1'),
                             (neural_client.LAYOUT, 'rgl_shared_neural_384_minilm_faf4aa4_v1')):
            names = {corpus.collection_name(uuid5(RID, str(i)), layout) for i in range(100)}
            self.assertEqual(names, {name})
        self.assertEqual(corpus.collection_name(RID, 'legacy_release_v1'), 'rgl_' + RID.hex)
        self.rejected(lambda: corpus.collection_name(RID, 'unknown'), 'UNKNOWN_INDEX_LAYOUT')

    def index_neural(self, fail_batch=None):
        # A valid additive corpus can contain 44 chunks although RPC caps a batch at 32.
        docs = [{**document(), 'source_key': f'd{i}', 'title': 'A', 'text': 'a' * 521}
                for i in range(22)]
        corpus.validate_bundle(docs)
        chunks = [dict(id=uuid5(RID, f'{i}:{ordinal}'), tenant=WHO.tenant,
                       actor=WHO.actor, title=doc['title'], quote=doc['text'][start:start+600])
                  for i, doc in enumerate(docs)
                  for ordinal, start in enumerate(range(0, len(doc['text']), 520))]
        metadata = dict(result=dict(config=dict(params=dict(vectors=dict(
            dense=dict(size=384, distance='Cosine')))),
            payload_schema={field: {'data_type': 'keyword'}
                            for field in ('tenant', 'actor', 'release_id')}))
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=None)
        client.get.return_value = httpx.Response(200, json=metadata)
        batches = []

        def embed(texts, *, scope):
            self.assertLessEqual(len(texts), 32, 'valid corpus exceeds original RPC batch limit')
            self.assertEqual(scope, (WHO.tenant, WHO.actor, str(RID)))
            batches.append(list(texts))
            if fail_batch == len(batches):
                raise RequestError('EMBEDDING_UNAVAILABLE', 503)
            offset = sum(map(len, batches[:-1]))
            return [[float(offset + i)] + [0.] * 383 for i in range(len(texts))], .3, .05

        with patch.object(corpus, 'release_settings', return_value=(
                'rgl_shared_neural_384_minilm_faf4aa4_v1', 384, True)), \
                patch.object(corpus.httpx, 'Client', return_value=client), \
                patch.object(corpus.resilience, 'guard', side_effect=lambda _: nullcontext()), \
                patch.object(neural_client, 'embed', side_effect=embed), \
                patch.object(corpus.QdrantAdapter, 'call') as call:
            if fail_batch:
                self.rejected(lambda: corpus.QdrantAdapter().index(RID, chunks),
                              'EMBEDDING_UNAVAILABLE', 503)
                call.assert_not_called()
            else:
                corpus.QdrantAdapter().index(RID, chunks)
                self.assertEqual([len(batch) for batch in batches], [32, 12])
                points = call.call_args.args[2]['points']
                self.assertEqual(len(points), 44)
                self.assertEqual([p['vector']['dense'][0] for p in points], list(range(44)))
                self.assertEqual([p['id'] for p in points], [str(c['id']) for c in chunks])
                for point in points:
                    self.assertEqual(set(point['payload']), {'tenant', 'actor', 'release_id', 'chunk_id'})
                    self.assertEqual(point['payload']['tenant'], WHO.tenant)
        return batches

    def test_valid_neural_corpus_batches_without_dropping_chunks(self):
        self.index_neural()

    def test_failed_later_embedding_batch_never_publishes_partial_points(self):
        self.index_neural(fail_batch=2)

    def test_hybrid_search_filters_every_prefetch_and_final_query(self):
        ids = [str(uuid5(RID, 'c1'))]
        with patch.object(corpus, 'release_settings', return_value=('shared', 256, False)), \
                patch.object(corpus.cache, 'get', return_value=None), \
                patch.object(corpus.cache, 'put') as cache_put, \
                patch.object(corpus.QdrantAdapter, 'call', return_value={
                    'result': {'points': [{'id': ids[0]}]}}) as call:
            self.assertEqual(corpus.QdrantAdapter().search(RID, WHO, 'alimentação'), ids)
            body = call.call_args.args[2]
            self.assertEqual(body['query'], {'fusion': 'rrf'})
            for target in [body, *body['prefetch']]:
                self.assertEqual(target['limit'], 16)
                self.assertEqual(target['filter'], {'must': [
                    {'key': key, 'match': {'value': value}} for key, value in
                    (('tenant', WHO.tenant), ('actor', WHO.actor), ('release_id', str(RID)))]})
            cache_put.assert_called_once()

    def test_candidate_ids_never_replace_canonical_authorization(self):
        index = Mock()
        index.search.return_value = [str(uuid5(RID, 'foreign-chunk'))]
        db = Database(lambda sql, params: Cursor())
        request = dict(tenant=WHO.tenant, actor=WHO.actor, source_snapshot=str(RID),
                       question='Qual é o limite de alimentação?')
        with patch.object(corpus.ledger, 'connect', return_value=db), \
                patch.object(corpus, 'release_settings', return_value=('shared', 256, False)):
            self.assertEqual(corpus.retrieve(request, index=index), [])
        sql, params = db.calls[0]
        self.assertEqual(params, (index.search.return_value, WHO.tenant, WHO.actor, str(RID)))
        for guard in ('d.tenant=%s', 'd.actor=%s', 'd.release_id=%s', "r.state='READY'",
                      'NOT d.revoked', 'd.valid_until>clock_timestamp()'):
            self.assertIn(guard, sql)

    def test_index_malformed_ids_and_over_limit_response_fail_closed(self):
        bad_results = [None, {}, {'result': []}, {'result': {'points': None}},
                       {'result': {'points': [{'id': 'invalid'}]}},
                       {'result': {'points': [{'id': str(uuid5(RID, str(i)))} for i in range(17)]}},
                       {'result': {'points': [{'id': str(RID)}, {'id': str(RID)}]}}]
        for response in bad_results:
            with self.subTest(response=str(response)[:80]), \
                    patch.object(corpus, 'release_settings', return_value=('shared', 256, False)), \
                    patch.object(corpus.cache, 'get', return_value=None), \
                    patch.object(corpus.cache, 'put') as put, \
                    patch.object(corpus.QdrantAdapter, 'call', return_value=response):
                self.rejected(lambda: corpus.QdrantAdapter().search(RID, WHO, 'alimentação'),
                              'INDEX_RESPONSE_INVALID', 503)
                put.assert_not_called()

    def test_legacy_version_revocation_preserves_other_versions(self):
        did = uuid5(RID, 'doc')
        db = Database(lambda sql, params: Cursor(one={'id': did, 'source_key': 'synthetic'}))
        with patch.object(corpus, 'require_profile'), patch.object(corpus.ledger, 'connect', return_value=db):
            corpus.revoke(WHO, did)
        updates = [(sql, params) for sql, params in db.calls if 'SET revoked=true' in sql]
        self.assertEqual(len(updates), 1)
        self.assertIn('WHERE id=%s AND NOT revoked', updates[0][0])
        self.assertEqual(updates[0][1], (did,))
        self.assertFalse(any('INSERT INTO corpus_revocations' in sql for sql, _ in db.calls))

    def test_source_revocation_uses_scope_and_durable_tombstone(self):
        did = uuid5(RID, 'doc')
        db = Database(lambda sql, params: Cursor(one={'id': did, 'source_key': 'synthetic'}))
        with patch.object(corpus, 'require_profile'), patch.object(corpus.ledger, 'connect', return_value=db):
            corpus.revoke(WHO, did, all_versions=True)
        expected = (WHO.tenant, WHO.actor, 'synthetic')
        self.assertTrue(any('INSERT INTO corpus_revocations' in sql and params == expected
                            for sql, params in db.calls))
        self.assertTrue(any('WHERE tenant=%s AND actor=%s AND source_key=%s AND NOT revoked' in sql
                            and params == expected for sql, params in db.calls))
        self.assertEqual(db.calls[1][1], (did, WHO.tenant, WHO.actor))

    def test_foreign_revoke_never_mutates(self):
        db = Database(lambda sql, params: Cursor())
        with patch.object(corpus, 'require_profile'), patch.object(corpus.ledger, 'connect', return_value=db):
            self.rejected(lambda: corpus.revoke(WHO, uuid5(RID, 'foreign')), 'NOT_FOUND', 404)
        self.assertFalse(any(sql.startswith(('UPDATE', 'INSERT')) for sql, _ in db.calls))

    @contextmanager
    def ingestion_adapter(self, *, ready_manifest=None, candidate=None, tombstone=False, index=None):
        head = dict(release_id=RID if ready_manifest else None, generation=3)
        def dispatch(sql, params):
            if 'FROM corpus_heads' in sql:
                return Cursor(one=head)
            if 'FROM corpus_revocations' in sql:
                return Cursor(one={'revoked': True} if tombstone else None)
            if 'SELECT manifest_hash' in sql:
                return Cursor(one={'manifest_hash': ready_manifest})
            if "state='BUILDING'" in sql:
                return Cursor(one=dict(id=candidate, parent_generation=3) if candidate else None)
            return Cursor()
        db = Database(dispatch)
        index = index or Mock()
        with patch.object(corpus, 'require_profile'), \
                patch.object(corpus, 'new_embedding', return_value=(corpus.EMBEDDING, corpus.INDEX_LAYOUT)), \
                patch.object(corpus.ledger, 'connect', return_value=db):
            yield db, index

    def test_raw_original_change_creates_version_even_when_parsed_text_is_equal(self):
        raw = b'Synthetic rule.'
        changed = b'\xef\xbb\xbf' + raw
        def originals(value):
            return {'synthetic': dict(original_hash=hashlib.sha256(value).hexdigest(),
                                      original_bytes=len(value), original_media_type='text/plain')}
        bundle = [{**document(), 'text': raw.decode()}]
        with self.ingestion_adapter() as (db, index), patch.object(corpus, 'uuid4', return_value=RID):
            first = corpus.ingest(WHO, bundle, index=index, originals=originals(raw))
            manifest = next(params[3] for sql, params in db.calls if sql.startswith('INSERT INTO corpus_releases'))
            self.assertEqual(first['release_id'], str(RID))
            self.assertFalse(first['replayed'])
        with self.ingestion_adapter(ready_manifest=manifest) as (_, index):
            again = corpus.ingest(WHO, bundle, index=index, originals=originals(raw))
            self.assertTrue(again['replayed'])
            self.assertEqual(again['release_id'], first['release_id'])
            index.index.assert_not_called()
        second_id = uuid5(RID, 'second')
        with self.ingestion_adapter(ready_manifest=manifest) as (db, index), \
                patch.object(corpus, 'uuid4', return_value=second_id):
            second = corpus.ingest(WHO, bundle, index=index, originals=originals(changed))
            second_manifest = next(params[3] for sql, params in db.calls
                                   if sql.startswith('INSERT INTO corpus_releases'))
            self.assertNotEqual(second_manifest, manifest)
            self.assertNotEqual(second['release_id'], first['release_id'])
            doc_insert = next(params for sql, params in db.calls if sql.startswith('INSERT INTO corpus_documents'))
            self.assertEqual(doc_insert[6], hashlib.sha256(raw).hexdigest())

    def test_failed_index_never_promotes_and_retry_reuses_candidate(self):
        index = Mock()
        index.index.side_effect = RequestError('INDEX_UNAVAILABLE', 503)
        with self.ingestion_adapter(index=index) as (db, _), patch.object(corpus, 'uuid4', return_value=RID):
            self.rejected(lambda: corpus.ingest(WHO, [document()], index=index), 'INDEX_UNAVAILABLE', 503)
            self.assertFalse(any(sql.startswith('UPDATE corpus_') for sql, _ in db.calls))
        with self.ingestion_adapter(candidate=RID) as (db, index), patch.object(corpus, 'uuid4') as create:
            retried = corpus.ingest(WHO, [document()], index=index)
            self.assertEqual(retried['release_id'], str(RID))
            self.assertEqual(retried['state'], 'READY')
            create.assert_not_called()
            self.assertFalse(any(sql.startswith('INSERT INTO corpus_releases') for sql, _ in db.calls))
            index.index.assert_called_once()

    def test_durable_source_tombstone_prevents_new_version(self):
        with self.ingestion_adapter(tombstone=True) as (db, index):
            self.rejected(lambda: corpus.ingest(WHO, [document()], index=index), 'SOURCE_REVOKED', 409)
            index.index.assert_not_called()
            self.assertFalse(any(sql.startswith('INSERT INTO corpus_releases') for sql, _ in db.calls))

    def test_additive_publication_preserves_sources_and_originals(self):
        old = 'Fonte imutável com mais de um chunk. ' * 20
        digest = hashlib.sha256(old.encode()).hexdigest()
        rows = [dict(id='old', source_key='keep', title='Existente', revoked=False,
                     content_hash=digest, valid_until=None, original_hash='a'*64,
                     original_media_type='text/plain', original_bytes=8)]
        chunks = [dict(ordinal=i, quote=old[start:start+600])
                  for i, start in enumerate(range(0, len(old), 520))]
        def dispatch(sql, params):
            if 'FROM corpus_heads' in sql:
                return Cursor(one=dict(generation=7, release_id=RID))
            if 'FROM corpus_documents' in sql:
                self.assertEqual(params, (RID, WHO.tenant, WHO.actor))
                return Cursor(rows=rows)
            return Cursor(rows=chunks)
        db = Database(dispatch)
        with patch.object(corpus, 'require_profile'), patch.object(publication.ledger, 'connect', return_value=db), \
                patch.object(corpus, 'ingest', return_value={'state': 'READY'}) as ingest:
            publication.publish(WHO, document(), 7)
        bundle = ingest.call_args.args[1]
        self.assertEqual({d['source_key'] for d in bundle}, {'keep', 'synthetic'})
        self.assertEqual(next(d['text'] for d in bundle if d['source_key'] == 'keep'), old)
        self.assertEqual(ingest.call_args.kwargs['expected_generation'], 7)
        self.assertEqual(ingest.call_args.kwargs['originals']['keep']['original_hash'], 'a'*64)

    def test_stale_additive_generation_never_ingests(self):
        db = Database(lambda sql, params: Cursor(one=dict(generation=8, release_id=RID)))
        with patch.object(corpus, 'require_profile'), patch.object(publication.ledger, 'connect', return_value=db), \
                patch.object(corpus, 'ingest') as ingest:
            self.rejected(lambda: publication.publish(WHO, document(), 7),
                          'CORPUS_PROMOTION_CONFLICT', 409)
            ingest.assert_not_called()

    def test_additive_publication_never_resurrects_revoked_source(self):
        def dispatch(sql, params):
            if 'FROM corpus_heads' in sql:
                return Cursor(one=dict(generation=7, release_id=RID))
            return Cursor(rows=[dict(revoked=True)])
        with patch.object(corpus, 'require_profile'), \
                patch.object(publication.ledger, 'connect', return_value=Database(dispatch)), \
                patch.object(corpus, 'ingest') as ingest:
            self.rejected(lambda: publication.publish(WHO, document(), 7),
                          'REVOKED_CORPUS_REQUIRES_MAINTENANCE', 409)
            ingest.assert_not_called()

    def test_empty_and_conflicting_evidence_abstain_without_citations(self):
        for rows in ([], [dict(document_id='a'), dict(document_id='b')]):
            result = corpus.compose(rows)
            self.assertEqual(result.kind, 'ABSTAIN')
            self.assertEqual(result.citations, ())

    def test_embedding_stale_policy_cache_is_not_trusted(self):
        valid = dict(model_version=neural_client.VERSION, calibration='office_faq_v1',
                     holdout_used=False, calibration_sha256=neural_client.CALIBRATION_SHA256,
                     policy_sha256=hashlib.sha256(Path(semantic_policy.__file__).read_bytes()).hexdigest(),
                     threshold=.3, margin=.05, vectors=[[1.] + [0.] * 383])
        stale = {**valid, 'policy_sha256': '0'*64}
        with patch.object(neural_client.cache, 'get', return_value=stale), \
                patch.object(neural_client.cache, 'put'), \
                patch.object(neural_client.resilience, 'guard', side_effect=lambda _: nullcontext()), \
                patch.object(neural_client, '_embed', return_value=valid) as rpc:
            self.assertEqual(neural_client.embed(['controle'], scope=('tenant', 'actor', 'release')),
                             (valid['vectors'], .3, .05))
            rpc.assert_called_once_with(['controle'])

    def test_semantic_time_approval_and_device_alias_contracts(self):
        policy = 'Para trabalho remoto, solicite autorização ao gestor.'
        self.assertTrue(semantic_policy.eligible('Quando trabalho em casa preciso de aprovação?', policy))
        self.assertFalse(semantic_policy.eligible('Quando começa o atendimento?', policy))
        self.assertFalse(semantic_policy.eligible('Quem aprova?', 'O suporte está disponível de segunda a sexta.'))
        self.assertIn('computador', semantic_policy.retrieval_query('Como peço reparo para a máquina de trabalho?'))
        for q in ('Como reparo a máquina industrial?', 'Conserto da impressora?', 'Reparo do servidor?'):
            self.assertEqual(semantic_policy.retrieval_query(q), q)

    def test_purchase_price_never_uses_reimbursement_cap(self):
        text = 'O limite de estacionamento é 18 reais.'
        rows = [dict(document_id='parking', ordinal=0, title='Estacionamento', quote=text)]
        self.assertFalse(semantic_policy.eligible('Qual é o preço da abobrinha?', text))
        with patch.object(neural_client, 'embed', return_value=(
                [[1.] + [0.] * 383, [.8, .6] + [0.] * 382], .3, .05)):
            self.assertEqual(neural_client.rank('Qual é o preço da abobrinha?', rows), [])
        self.assertTrue(semantic_policy.eligible('Qual é o preço da abobrinha?',
                                                 'A abobrinha custa 12 reais.'))
        self.assertTrue(semantic_policy.eligible('Qual é o preço da refeição?',
                                                 'O preço da refeição é 32 reais.'))
        self.assertTrue(semantic_policy.eligible('Qual o valor do teto de alimentação?',
                                                 'O teto de alimentação é 45 reais.'))
        self.assertFalse(semantic_policy.eligible('Como saber o custo da refeição?',
                                                  'O teto de alimentação é 45 reais.'))
        self.assertFalse(semantic_policy.eligible('Como saber o custo da refeição?',
                                                  'O custo depende do restaurante.'))

    def test_original_readiness_helpers_preserve_deadlines(self):
        with patch.object(rag_fixture, 'script', side_effect=[RuntimeError('unready'),
                     {'result': {'collections': []}}]), patch.object(rag_fixture.time, 'sleep'):
            self.assertEqual(rag_fixture.wait_index_ready('offline')['collections'], 0)
        with patch.object(rag_fixture, 'script', side_effect=RuntimeError('unready')), \
                patch.object(rag_fixture.time, 'monotonic', side_effect=[0, 121]):
            with self.assertRaisesRegex(AssertionError, 'Qdrant readiness failed'):
                rag_fixture.wait_index_ready('offline')
        with patch.object(current_queue_fixture, 'script', side_effect=[False, True]), \
                patch.object(current_queue_fixture.time, 'sleep') as sleep:
            current_queue_fixture.wait_index()
            sleep.assert_called_once_with(1)
        with patch.object(current_queue_fixture, 'script', return_value=False), \
                patch.object(current_queue_fixture.time, 'monotonic', side_effect=[0, 0, 91]), \
                patch.object(current_queue_fixture.time, 'sleep'):
            with self.assertRaisesRegex(AssertionError, 'QA_INDEX_READINESS_DEADLINE'):
                current_queue_fixture.wait_index()

    def test_original_generated_document_helpers_are_valid_python(self):
        source = Path(document_fixture.__file__).read_text(encoding='utf8')
        helpers = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)
                   and ast.unparse(node.func) == 'rag.script']
        self.assertEqual(len(helpers), 2)
        values = dict(second=str(uuid5(RID, 'second')), v2={'release_id': str(RID)})
        for helper in helpers:
            fragment = eval(compile(ast.Expression(helper.args[0]), '<original-helper>', 'eval'),
                            {'__builtins__': {'repr': repr}}, values)
            compile(fragment, '<original-generated-Python>', 'exec')

    def test_current_fixture_waits_for_index_before_ingestion_round(self):
        tree = ast.parse(Path(current_queue_fixture.__file__).read_text(encoding='utf8'))
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        calls = [(ast.unparse(node.func), node.lineno) for node in ast.walk(main) if isinstance(node, ast.Call)]
        waited = next(line for name, line in calls if name == 'wait_index')
        tested = next(line for name, line in calls if name == 'run_round')
        self.assertLess(waited, tested)

    def test_download_headers_are_case_insensitive_and_required(self):
        headers = {'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store',
                   'Content-Disposition': 'attachment; filename="test.txt"'}
        self.assertTrue(document_fixture.safe_headers(headers))
        self.assertTrue(document_fixture.safe_headers({k.upper(): v for k, v in headers.items()}))
        for key in headers:
            self.assertFalse(document_fixture.safe_headers({k: v for k, v in headers.items() if k != key}))

    def test_meal_dataset_rules_and_frozen_calibration(self):
        meal = json.loads((ROOT/'eval/datasets/meal-policy-aurora-v1.json').read_text(encoding='utf8'))
        self.assertEqual(len(meal['documents']), 20)
        self.assertEqual(len({d['source_key'] for d in meal['documents']}), 20)
        self.assertEqual(meal['baseline_limit_brl'], 45)
        self.assertEqual(meal['period'], 'pending_user_decision')
        docs = [{**d, 'media_type': 'text/plain'} for d in meal['documents']]
        corpus.validate_bundle(docs)
        by_key = {d['source_key']: d['text'] for d in docs}
        self.assertIn('12 dias úteis', by_key['meal_lab_deadline'])
        self.assertIn('45 reais', by_key['meal_lab_period'])
        calibration = ROOT/'eval/datasets/semantic-calibration-v1.json'
        self.assertEqual(hashlib.sha256(calibration.read_bytes()).hexdigest(),
                         neural_client.CALIBRATION_SHA256)
        cal = json.loads(calibration.read_text(encoding='utf8'))
        for file in ('semantic-holdout-v1.json', 'semantic-holdout-v2.json'):
            held = json.loads((ROOT/'eval/datasets'/file).read_text(encoding='utf8'))
            self.assertFalse({c['question'] for c in cal['cases']} & {c['question'] for c in held['cases']})


class DocumentContracts(unittest.TestCase):
    rejected = CorpusContracts.rejected
    @classmethod
    def setUpClass(cls):
        fcntl = types.ModuleType('fcntl')
        fcntl.LOCK_EX = 2
        fcntl.flock = Mock(side_effect=AssertionError('REAL_POSIX_STORAGE_NOT_AVAILABLE'))
        resource = types.ModuleType('resource')
        resource.RLIMIT_AS, resource.RLIMIT_CPU, resource.RLIMIT_FSIZE = range(3)
        resource.setrlimit = Mock()
        with patch.dict(sys.modules, {'fcntl': fcntl, 'resource': resource}):
            cls.documents = importlib.import_module('rag_app.documents')
            cls.parser = importlib.import_module('rag_app.document_parser')
        cls.resource_adapter = resource

    def parsed(self, raw, media_type):
        result = io.StringIO()
        with patch.object(self.parser.sys, 'stdin', types.SimpleNamespace(buffer=io.BytesIO(raw))), \
                patch.object(self.parser.sys, 'argv', ['parser', media_type]), redirect_stdout(result):
            self.parser.main()
        return json.loads(result.getvalue())['text']

    def test_parser_txt_markdown_and_text_pdf(self):
        raw = 'Política sintética: café, açúcar e 45 reais.\n'.encode()
        self.assertEqual(self.parsed(raw, 'text/plain'), raw.decode())
        self.assertEqual(self.parsed(b'\xef\xbb\xbf' + raw, 'text/plain'), raw.decode())
        self.assertEqual(self.parsed(b'# Synthetic\nA rule.', 'text/markdown'), '# Synthetic\nA rule.')
        self.assertIn('Synthetic document', self.parsed(document_fixture.pdf('Synthetic document'),
                                                      'application/pdf'))
        self.resource_adapter.setrlimit.assert_any_call(self.resource_adapter.RLIMIT_AS,
                                                        (268435456, 268435456))
        self.resource_adapter.setrlimit.assert_any_call(self.resource_adapter.RLIMIT_CPU, (2, 2))
        self.resource_adapter.setrlimit.assert_any_call(self.resource_adapter.RLIMIT_FSIZE, (0, 0))

    def test_parser_rejects_abuse_and_unsupported_formats(self):
        cases = [(b'', 'text/plain'), (b'  \n', 'text/plain'), (b'x'*8193, 'text/plain'),
                 (b'bad\xff', 'text/plain'), (b'bad\x00text', 'text/plain'),
                 (b'<html>not supported</html>', 'text/html'), (b'not PDF', 'application/pdf'),
                 (document_fixture.pdf(), 'application/pdf'),
                 (document_fixture.pdf('synthetic', encrypted=True), 'application/pdf'),
                 (document_fixture.pdf('synthetic', active=True), 'application/pdf'),
                 (document_fixture.pdf('synthetic', pages=4), 'application/pdf')]
        for raw, kind in cases:
            with self.subTest(kind=kind, size=len(raw)), self.assertRaises(Exception):
                self.parsed(raw, kind)

    def test_additive_file_keeps_raw_metadata_out_of_untrusted_document(self):
        import base64
        raw = b'\xef\xbb\xbfSynthetic rule.'
        value = dict(source_key='synthetic', title='Original', media_type='text/plain',
                     data_base64=base64.b64encode(raw).decode(), valid_until=None,
                     original_hash='untrusted')
        digest = hashlib.sha256(raw).hexdigest()
        with patch.object(self.documents, 'require_profile'), \
                patch.object(self.documents, 'parse', return_value='Synthetic rule.'), \
                patch.object(self.documents, 'store', return_value=digest) as store, \
                patch.object(publication, 'publish', return_value={'state': 'READY'}) as publish:
            self.documents.upload_additive(WHO, value, 7)
        store.assert_called_once_with(raw)
        self.assertEqual(set(publish.call_args.args[1]),
                         {'source_key', 'title', 'valid_until', 'text', 'media_type'})
        self.assertEqual(publish.call_args.args[2], 7)
        self.assertEqual(publish.call_args.kwargs['original'], dict(
            original_hash=digest, original_media_type='text/plain', original_bytes=len(raw)))

    def test_canonical_download_scope_and_exact_original_bytes(self):
        raw = b'Synthetic original bytes.\n'
        digest = hashlib.sha256(raw).hexdigest()
        folder = ROOT / '.local/orchestration/synthetic-originals'
        folder.mkdir(parents=True, exist_ok=True)
        (folder/digest).write_bytes(raw)
        row = dict(original_hash=digest, original_bytes=len(raw), original_media_type='text/plain')
        db = Database(lambda sql, params: Cursor(one=row))
        with patch.object(self.documents, 'ROOT', folder), \
                patch.object(self.documents, 'require_profile'), \
                patch.object(self.documents.ledger, 'connect', return_value=db):
            self.assertEqual(self.documents.download(WHO, RID), (raw, 'text/plain'))
            for changed in ({**row, 'original_hash': '../escape'},
                            {**row, 'original_bytes': len(raw)+1},
                            {**row, 'original_media_type': 'text/html'}):
                with patch.object(db, 'dispatch', return_value=Cursor(one=changed)):
                    self.rejected(lambda: self.documents.download(WHO, RID),
                                  'DOCUMENT_INTEGRITY_FAILURE', 503)
            with patch.object(db, 'dispatch', return_value=Cursor()):
                self.rejected(lambda: self.documents.download(WHO, RID), 'NOT_FOUND', 404)
        sql, params = db.calls[0]
        self.assertEqual(params, (RID, WHO.tenant, WHO.actor))
        for guard in ("r.state='READY'", 'NOT d.revoked',
                      'd.valid_until>clock_timestamp()', 'FOR SHARE OF d'):
            self.assertIn(guard, sql)


if __name__ == '__main__':
    unittest.main(argv=[__file__], verbosity=2)
