"""Runtime controls using synthetic failures, not SQL/container certification."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "app"))
from workspace import bootstrap
bootstrap()

import ast
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib
import io
import json
import logging
import os
import random
import runpy
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from rag_app import broker, cache, delivery, ledger, observability, process, resilience, safe_logging
from rag_app.domain import Identity, Proposal, RequestError
from cache_expiry_oracle import valid_pttl


class Result:
    def __init__(self, row=None, rows=None):
        self.row, self.rows = row, rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


def connected(db):
    @contextmanager
    def connect(*args, **kwargs):
        yield db
    return connect


class RuntimeContracts(unittest.TestCase):
    def test_consumer_closes_connection_after_channel_failure(self):
        conn = Mock()
        with patch.object(broker, "connection", return_value=conn), \
                patch.object(broker, "channel", side_effect=RuntimeError("SYNTHETIC_CHANNEL_FAILURE")):
            with self.assertRaises(RuntimeError):
                broker.Consumer()
        conn.close.assert_called_once_with()

    def test_sdk_repeated_setup_removes_added_raw_handler(self):
        for namespace in ("google.adk", "google_adk"):
            safe_logging.setup()
            raw, structured = io.StringIO(), io.StringIO()
            root = logging.getLogger(namespace)
            raw_handler = logging.StreamHandler(raw)
            root.addHandler(raw_handler)
            try:
                safe_logging.setup()
                with redirect_stderr(structured):
                    root.error("SYNTHETIC_PRIVATE_REPEATED_SETUP")
                self.assertEqual(raw.getvalue(), "")
                self.assertNotIn("SYNTHETIC_PRIVATE", structured.getvalue())
                self.assertEqual(json.loads(structured.getvalue())["severity"], "ERROR")
            finally:
                root.removeHandler(raw_handler)

    def test_fixture_project_directory_and_ownership(self):
        import http_fixture as base
        import current_queue_fixture as qa
        for name, expected in (("delivery_fixture", "rag-local-qa-delivery-20261001"),
                               ("observability_fixture", "rag-local-qa-observability-20261001")):
            importlib.reload(base)
            importlib.reload(qa)
            fixture = importlib.import_module(name)
            importlib.reload(fixture)
            self.assertEqual(base.COMPOSE[base.COMPOSE.index("--project-directory") + 1], str(ROOT / "app"))
            self.assertEqual(base.COMPOSE[base.COMPOSE.index("-p") + 1], expected)

    def test_restart_fixture_refuses_default_canonical_runtime(self):
        import http_fixture as base
        importlib.reload(base)
        folder = Path("D:/RAG-Local/eval/runs/SYNTHETIC_OFFLINE_OWNERSHIP")
        original_is_file = Path.is_file
        def synthetic_contract(path):
            if path == folder / "contract.json":
                return True
            return original_is_file(path)
        with patch.object(Path, "is_file", synthetic_contract), \
                patch.object(sys, "argv", ["restart_fixture", "--inside-owned-fixture", str(folder)]), \
                patch.object(base, "docker", side_effect=AssertionError("CANONICAL_DOCKER_ATTEMPTED")) as docker, \
                patch.object(base, "http", side_effect=AssertionError("CANONICAL_HTTP_ATTEMPTED")) as http:
            with self.assertRaises(SystemExit):
                runpy.run_path(str(ROOT / "app/tests/lifecycle/restart_fixture.py"), run_name="__main__")
        docker.assert_not_called(); http.assert_not_called()

    def test_restart_fixture_runs_only_matching_isolated_contract(self):
        import http_fixture as base
        with patch.dict(os.environ, {"RAG_MODE": "lab"}):
            from rag_app.api import LabIdentity
        importlib.reload(base)
        folder = ROOT / ".local/orchestration/controlled-restart" / str(uuid4())
        folder.mkdir(parents=True)
        endpoint, project = "http://127.0.0.1:8940", "rag-local-qa-offline-synthetic"
        (folder / "contract.json").write_text(json.dumps({"isolated_project": project, "endpoint": endpoint}), encoding="utf8")
        rid = str(uuid4())
        def http(path, token=None, body=None, headers=None):
            if path == "/v1/lab/session":
                self.assertEqual(LabIdentity.model_validate(body).profile, "ana")
                return 200, {"token": "synthetic-token"}
            if path == "/v1/requests":
                return 202, {"request_id": rid}
            if path == "/v1/requests/" + rid:
                return 200, {"state": "ACCEPTED"}
            if path == "/health/ready":
                return 200, {}
            self.fail("UNEXPECTED_SYNTHETIC_RESTART_HTTP")
        compose = ["docker", "compose", "--project-directory", str(ROOT / "app"), "-p", project]
        with patch.object(base, "COMPOSE", compose), patch.object(base, "BASE", endpoint), \
                patch.object(sys, "argv", ["restart_fixture", "--inside-owned-fixture", str(folder)]), \
                patch.object(base, "http", side_effect=http), patch.object(base, "docker") as docker, \
                patch.object(base, "key", return_value="synthetic-key"), \
                patch.object(base, "wait_state", return_value={"result": {"kind": "ABSTAIN"}}), \
                patch.object(base, "app_python", return_value='{"a":1,"o":1}'), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ended:
                runpy.run_path(str(ROOT / "app/tests/lifecycle/restart_fixture.py"), run_name="__main__")
        self.assertEqual(ended.exception.code, 0)
        proof = json.loads((folder / "restart-checks.json").read_text(encoding="utf8"))
        self.assertEqual(len(proof["rounds"]), 2)
        self.assertTrue(all(round_["passed"] for round_ in proof["rounds"]))
        self.assertEqual(sum(c.args == ("restart", "postgres") for c in docker.call_args_list), 2)

    def test_restart_fixture_refuses_mismatched_project_contract(self):
        import http_fixture as base
        importlib.reload(base)
        folder = ROOT / ".local/orchestration/controlled-restart" / str(uuid4())
        folder.mkdir(parents=True)
        (folder / "contract.json").write_text(json.dumps({"isolated_project": "rag-local-qa-another-owner",
                                                         "endpoint": "http://127.0.0.1:8940"}), encoding="utf8")
        compose = ["docker", "compose", "--project-directory", str(ROOT / "app"), "-p", "rag-local-qa-offline-synthetic"]
        with patch.object(base, "COMPOSE", compose), patch.object(base, "BASE", "http://127.0.0.1:8940"), \
                patch.object(sys, "argv", ["restart_fixture", "--inside-owned-fixture", str(folder)]), \
                patch.object(base, "docker", side_effect=AssertionError("FOREIGN_DOCKER_ATTEMPTED")) as docker, \
                patch.object(base, "http", side_effect=AssertionError("FOREIGN_HTTP_ATTEMPTED")) as http:
            with self.assertRaises(SystemExit):
                runpy.run_path(str(ROOT / "app/tests/lifecycle/restart_fixture.py"), run_name="__main__")
        docker.assert_not_called(); http.assert_not_called()

    def test_http_burst_refill_actor_tenant_and_capacity(self):
        with patch.dict(os.environ, {"RAG_RESILIENCE": "local_lab"}), \
                patch.object(resilience, "_rates", {}), patch.object(resilience.time, "monotonic", return_value=100) as clock:
            who = Identity("tenant-a", "actor-a")
            for _ in range(32):
                resilience.admit_http(who)
            with self.assertRaises(RequestError) as error:
                resilience.admit_http(who)
            self.assertEqual((error.exception.code, error.exception.status), ("REQUEST_RATE_LIMIT", 429))
            resilience.admit_http(Identity("tenant-a", "actor-b"))
            resilience.admit_http(Identity("tenant-b", "actor-a"))
            clock.return_value = 101
            for _ in range(3):
                resilience.admit_http(who)
            with self.assertRaises(RequestError):
                resilience.admit_http(who)
            resilience._rates.clear()
            resilience._rates.update({("t", str(n)): (0, 101) for n in range(1024)})
            with self.assertRaises(RequestError) as error:
                resilience.admit_http(who)
            self.assertEqual(error.exception.code, "API_CAPACITY_LIMIT")
            clock.return_value = 162
            resilience.admit_http(who)
            self.assertEqual(len(resilience._rates), 1)

    def test_admission_limits_utf8_bytes_storage_and_atomic_reservations(self):
        who = Identity("synthetic-tenant", "actor")
        for partitioned in (False, True):
            count_limit, byte_limit = (12500, 4194304) if partitioned else (100000, 33554432)
            for pending, tenant_pending, total_bytes, tenant_bytes, size, expected in (
                (count_limit, 0, 0, 0, 1, "ADMISSION_LIMIT"),
                (0, 1024, 0, 0, 1, "ADMISSION_LIMIT"),
                (0, 0, byte_limit - 4, 0, 1, "ADMISSION_BYTES_LIMIT"),
                (0, 0, 0, 8388608 - 4, 1, "ADMISSION_BYTES_LIMIT"),
                (0, 0, 0, 0, 268435457, "LEDGER_STORAGE_ADMISSION_CLOSED"),
                (count_limit - 1, 1023, byte_limit - 5, 8388608 - 5, 268435456, None)):
                calls = []
                def execute(sql, params=None):
                    calls.append((sql, params))
                    if sql.startswith("SELECT extract"):
                        return Result(row={"now": Decimal("1000")})
                    if sql.startswith("SELECT id,payload_hash"):
                        return Result()
                    if sql.startswith("SELECT pending FROM tenant_queue"):
                        return Result(row={"pending": tenant_pending})
                    if sql.startswith("SELECT pending_bytes FROM tenant_queue"):
                        return Result(row={"pending_bytes": tenant_bytes})
                    if sql.startswith("SELECT pending_bytes FROM admission"):
                        return Result(row={"pending_bytes": total_bytes})
                    if sql.startswith("SELECT pg_database_size"):
                        return Result(row={"size": size})
                    return Result()
                db = Mock(); db.execute.side_effect = execute
                with self.subTest(partitioned=partitioned, expected=expected), \
                        patch.dict(os.environ, {"RAG_DELIVERY": "local_lab", "RAG_RETRIEVAL": "disabled",
                                                "RAG_RESILIENCE": "local_lab" if partitioned else "disabled"}), \
                        patch.object(ledger, "connect", side_effect=connected(db)), \
                        patch.object(ledger, "lock_admission", return_value=pending), patch.object(ledger, "key_timestamp"):
                    if expected:
                        with self.assertRaises(RequestError) as error:
                            ledger.accept(who, "café", "synthetic-key")
                        self.assertEqual(error.exception.code, expected)
                        self.assertEqual(error.exception.status, 503 if expected.startswith("LEDGER_") else 429)
                        self.assertFalse(any(sql.startswith(("INSERT INTO requests", "INSERT INTO jobs", "INSERT INTO audit", "INSERT INTO outbox", "UPDATE admission", "UPDATE tenant_queue")) for sql, _ in calls))
                    else:
                        ledger.accept(who, "café", "synthetic-key")
                        self.assertEqual(sum(sql.startswith("INSERT INTO requests") for sql, _ in calls), 1)
                        self.assertEqual(sum(sql.startswith("INSERT INTO jobs") for sql, _ in calls), 1)
                        self.assertEqual(sum(sql.startswith("INSERT INTO audit") for sql, _ in calls), 1)
                        self.assertEqual(sum(sql.startswith("INSERT INTO outbox") for sql, _ in calls), 1)
                        updates = [p for sql, p in calls if sql.startswith(("UPDATE admission", "UPDATE tenant_queue"))]
                        self.assertEqual(len(updates), 2)
                        self.assertTrue(all(p[0] == 5 for p in updates))

    def test_dependency_bulkhead_and_circuit_reject_before_action(self):
        now = datetime.now(timezone.utc)
        for opened, probe, slots, expected in ((30, None, 0, "DEPENDENCY_CIRCUIT_OPEN"),
                                             (-1, 30, 0, "DEPENDENCY_PROBE_BUSY"),
                                             (None, None, 4, "DEPENDENCY_CONCURRENCY_LIMIT")):
            state = {"now": now, "open_until": now + timedelta(seconds=opened) if opened is not None else None,
                     "probe_until": now + timedelta(seconds=probe) if probe is not None else None, "generation": 0}
            calls = []
            def execute(sql, params=None):
                calls.append(sql)
                return Result(row={"n": slots} if sql.startswith("SELECT count") else state if sql.startswith("SELECT *") else None)
            db = Mock(); db.execute.side_effect = execute
            with patch.dict(os.environ, {"RAG_RESILIENCE": "local_lab"}), \
                    patch.object(ledger, "connect", side_effect=connected(db)), self.assertRaises(RequestError) as error:
                with resilience.guard("embedding"):
                    self.fail("REJECTED_DEPENDENCY_INVOKED")
            self.assertEqual(error.exception.code, expected)
            self.assertEqual(error.exception.status, 503)
            self.assertFalse(any(sql.startswith("INSERT INTO dependency_slots") for sql in calls))
        with patch.dict(os.environ, {"RAG_RESILIENCE": "local_lab"}), \
                patch.object(ledger, "connect", side_effect=AssertionError("UNKNOWN_DEPENDENCY_SQL")), self.assertRaises(ValueError):
            with resilience.guard("untrusted-provider"):
                self.fail("UNKNOWN_DEPENDENCY_INVOKED")

    def test_bounded_retry_delays_and_transient_classification(self):
        import httpx
        import psycopg
        for attempt in (0, 1, 2, 3, 10, 100000):
            self.assertTrue(1 <= resilience.delay(attempt, random.Random(attempt)) <= 60)
            self.assertTrue(5 <= resilience.broker_reconnect_delay(attempt, random.Random(attempt)) <= 30)
        for error in (TimeoutError(), psycopg.OperationalError(), RequestError("SYNTHETIC", 503),
                      ExceptionGroup("synthetic", [ValueError(), TimeoutError()])):
            self.assertTrue(resilience.transient(error))
        for error in (ValueError(), RequestError("SYNTHETIC", 400), RequestError("SYNTHETIC", 401)):
            self.assertFalse(resilience.transient(error))
        for code in (401, 403, 422, 429, 500, 599):
            response = httpx.Response(code, request=httpx.Request("GET", "https://synthetic.invalid"))
            self.assertEqual(resilience.transient(httpx.HTTPStatusError("synthetic", request=response.request, response=response)),
                             code == 429 or code >= 500)

    def test_signed_cache_is_scoped_bounded_expiring_and_disposable(self):
        store = {}
        redis = Mock()
        redis.get.side_effect = lambda key: store.get(key)
        redis.set.side_effect = lambda key, value, **kwargs: store.__setitem__(key, value)
        with patch.dict(os.environ, {"RAG_RESILIENCE": "local_lab"}), \
                patch.object(cache, "client", return_value=redis), patch.object(cache, "secret", return_value="synthetic-signing-key"), \
                patch.object(cache, "event") as event:
            a = cache.key("retrieval", ["tenant-a", "actor"], {"question": "café"})
            b = cache.key("retrieval", ["tenant-b", "actor"], {"question": "café"})
            self.assertNotEqual(a, b)
            self.assertNotIn("café", a)
            cache.put(a, {"ids": ["synthetic"]})
            self.assertEqual(redis.set.call_args.kwargs["ex"], 300)
            self.assertEqual(cache.get(a), {"ids": ["synthetic"]})
            store[b] = store[a]
            self.assertIsNone(cache.get(b))
            value = json.loads(store[a]); value["payload"]["ids"] = ["forged"]
            store[a] = json.dumps(value)
            self.assertIsNone(cache.get(a))
            for invalid in (b"bad-json", json.dumps({"payload": [], "mac": 1}), b"X" * 1048577):
                store[a] = invalid
                self.assertIsNone(cache.get(a))
            redis.get.side_effect = TimeoutError("synthetic-cache-down")
            self.assertIsNone(cache.get(a))
            redis.set.reset_mock()
            cache.put(a, "X" * 1048577)
            redis.set.assert_not_called()
            self.assertIn((("cache_unavailable_or_invalid",), {}), [(c.args, c.kwargs) for c in event.call_args_list])

    def test_metric_buffer_requeues_failed_flush_and_whitelists_names(self):
        db = Mock()
        with patch.object(cache, "_counts", {"cache_hit": 3}), patch.object(ledger, "connect", side_effect=TimeoutError()):
            self.assertFalse(cache.flush_events())
            self.assertEqual(cache._counts, {"cache_hit": 3})
            with patch.object(ledger, "connect", side_effect=connected(db)):
                self.assertTrue(cache.flush_events())
            self.assertEqual(cache._counts, {})
            self.assertEqual(db.execute.call_args.args[1], ("cache_hit", 3))
        with patch.dict(os.environ, {"RAG_RESILIENCE": "local_lab"}), \
                patch.object(cache, "_counts", {}), patch.object(cache, "_started", True):
            cache.event("SYNTHETIC_PRIVATE_UNKNOWN")
            self.assertEqual(cache._counts, {})
            cache.event("cache_miss")
            self.assertEqual(cache._counts, {"cache_miss": 1})

    def test_broker_rejects_abuse_and_preserves_sql_authority(self):
        consumer = object.__new__(broker.Consumer)
        consumer.ch = Mock()
        tag = Mock(delivery_tag=7)
        with patch.object(broker.cache, "event"), patch.object(ledger, "claim") as claim:
            for body in (b"x" * 37, b"not-a-uuid", b"\xff" * 36):
                consumer.ch.basic_get.return_value = (tag, None, body)
                self.assertEqual(consumer.poll(), (None, None))
                consumer.ch.basic_reject.assert_called_with(7, requeue=False)
            claim.assert_not_called()
            rid = uuid4(); consumer.ch.basic_get.return_value = (tag, None, str(rid).encode())
            claim.return_value = None
            for state, operation in (("SUCCEEDED", "basic_ack"), ("RUNNING", "basic_nack"),
                                     ("RETRY_WAIT", "basic_nack"), (None, "basic_reject")):
                db = Mock(); db.execute.return_value = Result(row={"state": state} if state else None)
                consumer.ch.reset_mock()
                with patch.object(ledger, "connect", side_effect=connected(db)):
                    self.assertEqual(consumer.poll(), (None, None))
                getattr(consumer.ch, operation).assert_called_once()
                if operation != "basic_ack":
                    self.assertFalse(getattr(consumer.ch, operation).call_args.kwargs["requeue"])
            claim.return_value = {"id": rid, "fence": 9}
            self.assertEqual(consumer.poll(), ({"id": rid, "fence": 9}, 7))

    def test_publisher_confirms_before_sql_hint_and_no_sql_hint_on_failure(self):
        import types
        fake_pika = types.ModuleType("pika"); fake_pika.BasicProperties = Mock()
        publisher = object.__new__(broker.Publisher)
        publisher.conn, publisher.ch = Mock(), Mock()
        rid = uuid4(); calls = []
        def execute(sql, params=None):
            calls.append(sql)
            return Result(row={"id": rid, "fence": 3} if sql.startswith("SELECT") else None)
        db = Mock(); db.execute.side_effect = execute
        with patch.dict(sys.modules, {"pika": fake_pika}), patch.object(ledger, "connect", side_effect=connected(db)), \
                patch.object(cache, "event"):
            publisher.ch.basic_publish.side_effect = lambda **kwargs: calls.append("CONFIRMED")
            self.assertTrue(publisher.publish_one())
            self.assertLess(calls.index("CONFIRMED"), next(i for i, sql in enumerate(calls) if sql.startswith("UPDATE jobs")))
            self.assertEqual(publisher.ch.basic_publish.call_args.kwargs["body"], str(rid).encode())
            self.assertTrue(publisher.ch.basic_publish.call_args.kwargs["mandatory"])
            calls.clear(); publisher.ch.basic_publish.side_effect = RuntimeError("SYNTHETIC_NACK")
            with self.assertRaises(RuntimeError):
                publisher.publish_one()
            self.assertFalse(any(sql.startswith("UPDATE jobs") for sql in calls))

    def test_applied_migration_skips_schema_and_secret_reads(self):
        db = Mock()
        def execute(sql, params=None):
            return Result(row={"exists": 1} if sql.startswith("SELECT 1 FROM schema_migrations") else None)
        db.execute.side_effect = execute
        with patch.object(ledger, "connect", side_effect=connected(db)), patch.object(ledger, "secret", side_effect=AssertionError("SECRET_READ")):
            ledger.migrate()
        queries = [c.args[0] for c in db.execute.call_args_list]
        self.assertTrue(queries[0].startswith("SELECT pg_advisory_xact_lock"))
        self.assertFalse(any("ALTER TABLE" in q or "UPDATE admission" in q for q in queries))

    def test_idempotency_mapping_precedes_age_quota_and_admission_writes(self):
        who, rid = Identity("synthetic-tenant", "actor"), uuid4()
        import hashlib
        digest = hashlib.sha256(json.dumps({"question": "café"}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        def execute(sql, params=None):
            if sql.startswith("SELECT extract"):
                return Result(row={"now": Decimal("9000000")})
            if sql.startswith("SELECT id,payload_hash"):
                self.assertEqual(params, (who.tenant, who.actor, "synthetic-old-key"))
                return Result(row={"id": rid, "payload_hash": digest})
            raise AssertionError("DUPLICATE_MUST_NOT_RESERVE")
        db = Mock(); db.execute.side_effect = execute
        with patch.object(ledger, "connect", side_effect=connected(db)), patch.object(ledger, "lock_admission", return_value=100000), \
                patch.object(ledger, "key_timestamp", side_effect=AssertionError("AGE_CHECKED_BEFORE_REPLAY")):
            self.assertEqual(ledger.accept(who, "café", "synthetic-old-key"), str(rid))
            with self.assertRaises(RequestError) as error:
                ledger.accept(who, "different", "synthetic-old-key")
            self.assertEqual((error.exception.code, error.exception.status), ("IDEMPOTENCY_CONFLICT", 409))

    def test_terminal_cas_releases_bytes_and_outbox_once(self):
        row = {"id": uuid4(), "tenant": "synthetic", "fence": 7}
        calls, changed = [], True
        def execute(sql, params=None):
            nonlocal changed
            calls.append((sql, params))
            if sql.startswith("UPDATE requests SET state"):
                self.assertIn("state='RUNNING' AND fence=%s AND lease_until>clock_timestamp() AND deadline>clock_timestamp()", sql)
                value = {"id": row["id"], "bytes": len("café".encode())} if changed else None
                changed = False
                return Result(row=value)
            return Result()
        db = Mock(); db.execute.side_effect = execute
        with patch.dict(os.environ, {"RAG_RESILIENCE": "local_lab"}):
            self.assertTrue(ledger.terminal(db, row, "SUCCEEDED", {"kind": "ABSTAIN"}, worker=True))
            self.assertFalse(ledger.terminal(db, row, "SUCCEEDED", {"kind": "ABSTAIN"}, worker=True))
        self.assertEqual(sum(sql.startswith("INSERT INTO audit") for sql, _ in calls), 1)
        self.assertEqual(sum(sql.startswith("INSERT INTO outbox") for sql, _ in calls), 1)
        updates = [(sql, p) for sql, p in calls if sql.startswith("UPDATE admission_shards") or sql.startswith("UPDATE tenant_queue")]
        self.assertEqual(len(updates), 2)
        self.assertTrue(all(params[0] == 5 for _, params in updates))

    def test_failure_retry_limit_deadline_and_fence(self):
        now = datetime.now(timezone.utc)
        original = {"id": uuid4(), "tenant": "synthetic", "fence": 5}
        for state, fence, lease, deadline, attempts, expected in (
            ("RUNNING", 4, 30, 900, 1, "ignored"), ("SUCCEEDED", 5, 30, 900, 1, "ignored"),
            ("RUNNING", 5, -1, 900, 1, "ignored"), ("RUNNING", 5, 30, -1, 1, "EXPIRED"),
            ("RUNNING", 5, 30, 900, 1, "retry"), ("RUNNING", 5, 30, 900, 3, "FAILED_FINAL")):
            row = dict(original, state=state, fence=fence, lease_until=now + timedelta(seconds=lease),
                       deadline=now + timedelta(seconds=deadline), attempts=attempts, now=now)
            db = Mock(); db.execute.return_value = Result(row=row)
            with patch.object(ledger, "connect", side_effect=connected(db)), patch.object(ledger, "lock_admission"), \
                    patch.object(ledger, "terminal", return_value=True) as terminal, patch.object(ledger, "retry_in") as retry:
                value = ledger.fail(original, TimeoutError("SYNTHETIC_PRIVATE"))
            if expected == "ignored":
                self.assertFalse(value); terminal.assert_not_called(); retry.assert_not_called()
                self.assertEqual(db.execute.call_count, 1)
            elif expected == "retry":
                self.assertTrue(value); retry.assert_called_once(); terminal.assert_not_called()
            else:
                self.assertTrue(value); self.assertEqual(terminal.call_args.args[2], expected); retry.assert_not_called()
            self.assertNotIn("SYNTHETIC_PRIVATE", repr(db.execute.call_args_list))

    def test_outbox_commit_precedes_ack_and_crash_is_replayable(self):
        events, inbox, acknowledged = [], set(), set()
        ids = [1, 2]
        class Database:
            def execute(self, sql, params=None):
                if sql.startswith("SELECT"):
                    self_rows = [{"id": n} for n in ids if n not in acknowledged]
                    self_test.assertIn("LIMIT 128 FOR UPDATE SKIP LOCKED", sql)
                    return Result(rows=self_rows)
                if sql.startswith("INSERT INTO notification_inbox"):
                    self_test.assertIn("ON CONFLICT DO NOTHING", sql)
                    inbox.add(params[0]); events.append("inbox")
                elif sql.startswith("UPDATE outbox"):
                    acknowledged.update(params[0]); events.append("ack")
                else:
                    raise AssertionError("UNEXPECTED_DELIVERY_SQL")
                return Result()
        self_test = self
        @contextmanager
        def connect():
            yield Database()
            events.append("commit")
        def crash(committed_ids):
            self.assertEqual(events[-1], "commit")
            self.assertEqual(set(committed_ids), inbox)
            raise RuntimeError("SYNTHETIC_CRASH")
        with patch.dict(os.environ, {"RAG_DELIVERY": "local_lab"}), patch.object(ledger, "connect", side_effect=connect):
            with self.assertRaises(RuntimeError):
                delivery.dispatch(crash)
            self.assertEqual(inbox, {1, 2}); self.assertEqual(acknowledged, set())
            self.assertEqual(delivery.dispatch(), 2)
            self.assertEqual(delivery.dispatch(), 0)
        self.assertEqual(inbox, {1, 2}); self.assertEqual(acknowledged, {1, 2})

    def test_retention_and_inbox_preserve_ownership_and_receipts(self):
        db = Mock(); db.execute.return_value = Result(rows=[{"id": uuid4()}])
        with patch.dict(os.environ, {"RAG_DELIVERY": "local_lab"}), patch.object(ledger, "connect", side_effect=connected(db)):
            self.assertEqual(delivery.retain(), 1)
        selection, scrub = [call.args[0] for call in db.execute.call_args_list]
        for guard in ("retention_managed", "NOT content_expired", "'SUCCEEDED','FAILED_FINAL','EXPIRED','CANCELLED'", "interval '30 days'", "LIMIT 32", "SKIP LOCKED"):
            self.assertIn(guard, selection)
        self.assertNotIn("DELETE", scrub)
        for forbidden in ("idem=", "payload_hash=", "state=", "fence="):
            self.assertNotIn(forbidden, scrub)
        db.reset_mock(); db.execute.return_value = Result(rows=[])
        with patch.dict(os.environ, {"RAG_DELIVERY": "local_lab"}), patch.object(ledger, "connect", side_effect=connected(db)):
            self.assertEqual(delivery.read(Identity("tenant-a", "actor-a"), 19), {"events": [], "next_after": 19})
        self.assertEqual(db.execute.call_args.args[1], ("tenant-a", "actor-a", 19))
        self.assertIn("r.tenant=%s AND r.actor=%s", db.execute.call_args.args[0])

    def test_recovery_skips_busy_shards_and_rechecks_current_rows(self):
        now, calls, candidates = datetime.now(timezone.utc), [], []
        candidates[:] = [{"id": uuid4(), "tenant": "busy"}, {"id": uuid4(), "tenant": "independent"}]
        current = dict(candidates[1], state="RUNNING", deadline=now - timedelta(seconds=1), now=now)
        def execute(sql, params=None):
            calls.append(sql)
            if sql.startswith("SELECT r.id,r.tenant"):
                self.assertIn("JOIN admission_shards", sql)
                self.assertIn("LIMIT 128 FOR UPDATE OF s,r SKIP LOCKED", sql)
                return Result(rows=candidates)
            if sql.startswith("SELECT *,clock_timestamp"):
                self.assertEqual(params, (candidates[1]["id"],))
                self.assertIn("FOR UPDATE SKIP LOCKED", sql)
                return Result(row=current)
            if sql.startswith("INSERT INTO jobs"):
                self.assertIn("NOT EXISTS", sql)
                self.assertIn("LIMIT 128 FOR KEY SHARE OF r SKIP LOCKED ON CONFLICT DO NOTHING", sql)
                return Result()
            raise AssertionError("UNEXPECTED_RECOVERY_SQL")
        db = Mock(); db.execute.side_effect = execute
        with patch.dict(os.environ, {"RAG_RESILIENCE": "local_lab"}), patch.object(ledger, "connect", side_effect=connected(db)), \
                patch.object(ledger, "lock_admission", side_effect=[None, 1]) as admission, \
                patch.object(ledger, "terminal") as terminal:
            ledger.recover()
        self.assertEqual(admission.call_args_list[0].kwargs, {"skip_locked": True})
        terminal.assert_called_once_with(db, current, "EXPIRED")

    def test_original_pttl_oracle_keeps_all_negative_controls(self):
        for value, expected in ((-2, True), (0, True), (1, True), (299999, True), (300000, True),
                                (-1, False), (-3, False), (300001, False), (True, False), (1.5, False), (None, False)):
            self.assertIs(valid_pttl(value), expected)

    def test_aggregate_metrics_alerts_allowlists_and_cadence(self):
        def execute(sql, params=None):
            if sql.startswith("SELECT state,count"):
                return Result(rows=[{"state": "ACCEPTED", "n": 2}, {"state": "SUCCEEDED", "n": 5}])
            if "FROM worker_health" in sql:
                return Result(rows=[{"role": role, "age": 1} for role in ("worker", "control", "delivery", "relay")])
            if sql.startswith("SELECT count"):
                return Result(row={"n": 1})
            if "FROM admission_shards" in sql:
                return Result(row={"pending": Decimal(2), "bytes": Decimal(5)})
            if "FROM dependency_control" in sql:
                return Result(rows=[{"name": "embedding", "failures": 3, "opened": True},
                                    {"name": "SYNTHETIC_PRIVATE_DEPENDENCY", "failures": 4, "opened": False}])
            if "FROM resilience_events" in sql:
                return Result(rows=[{"name": "cache_hit", "value": 9}, {"name": "SYNTHETIC_PRIVATE_EVENT", "value": 8}])
            if "terminal_at-created_at" in sql:
                self.assertIn("LIMIT 10000", sql)
                return Result(rows=[{"duration": -3}, {"duration": 2}])
            if "max(EXTRACT" in sql:
                return Result(row={"age": 61})
            raise AssertionError("UNKNOWN_METRIC_SQL")
        db = Mock(); db.execute.side_effect = execute
        with patch.dict(os.environ, {"RAG_RESILIENCE": "local_lab", "RAG_GEMINI_RESPONSES": "disabled"}), \
                patch.object(ledger, "connect", side_effect=connected(db)):
            text, alerts = observability.snapshot()
            self.assertEqual(observability.stale_after("worker"), 30)
            self.assertFalse(resilience.heartbeat_due(10, 14.99)); self.assertTrue(resilience.heartbeat_due(10, 15))
        self.assertEqual(set(alerts), {"EXPIRED_PENDING_RECOVERY", "QUEUE_WAIT_HIGH", "DEPENDENCY_CIRCUIT_OPEN"})
        self.assertIn("rag_pending_requests 2\n", text)
        self.assertIn("rag_pending_bytes 5\n", text)
        self.assertIn("rag_recent_completion_seconds_sum 2\n", text)
        self.assertIn('rag_resilience_events_total{event="cache_hit"} 9\n', text)
        for forbidden in ("tenant=", "actor=", "request_id", "question", "SYNTHETIC_PRIVATE"):
            self.assertNotIn(forbidden, text)
        with patch.dict(os.environ, {"RAG_RESILIENCE": "local_lab", "RAG_GEMINI_RESPONSES": "free_lab"}):
            self.assertEqual(observability.stale_after("worker"), 60)
        logs = io.StringIO()
        with patch.dict(os.environ, {"RAG_OBSERVABILITY": "local_lab"}), \
                patch.object(observability, "snapshot", side_effect=[("", alerts), ("", alerts), ("", [])]), redirect_stdout(logs):
            state = observability.alert_transition(None)
            state = observability.alert_transition(state)
            state = observability.alert_transition(state)
        events = [json.loads(line) for line in logs.getvalue().splitlines()]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[-1]["codes"], [])

    def test_exporter_is_local_bounded_and_outage_is_nonfatal(self):
        from opentelemetry.sdk.trace.export import SpanExportResult
        payload = {"resourceSpans": [{"scopeSpans": [{"spans": [{"name": "rag.request"}]}]}]}
        for code in (200, 500):
            client = Mock(); client.__enter__ = Mock(return_value=client); client.__exit__ = Mock(return_value=False)
            client.post.return_value.status_code = code
            with patch.object(observability, "sanitized", return_value=payload), patch.object(observability.httpx, "Client", return_value=client) as factory:
                result = observability.SafeLocalExporter().export([])
            self.assertEqual(result, SpanExportResult.SUCCESS if code == 200 else SpanExportResult.FAILURE)
            self.assertEqual(factory.call_args.kwargs, {"timeout": 0.5, "trust_env": False, "follow_redirects": False})
            self.assertEqual(client.post.call_args.args, ("http://collector:4318/v1/traces",))
        with patch.object(observability, "sanitized", return_value=payload), \
                patch.object(observability.httpx, "Client", side_effect=TimeoutError("SYNTHETIC_PRIVATE_EXPORT_FAILURE")):
            self.assertEqual(observability.SafeLocalExporter().export([]), SpanExportResult.FAILURE)
        with patch.dict(os.environ, {"RAG_OBSERVABILITY": "local_lab"}), patch.object(observability, "TracerProvider"), \
                patch.object(observability, "BatchSpanProcessor") as processor, patch.object(observability.trace, "set_tracer_provider"):
            observability.setup()
        self.assertEqual(processor.call_args.kwargs, {"max_queue_size": 256, "max_export_batch_size": 32,
                                                    "schedule_delay_millis": 200, "export_timeout_millis": 1000})

    def test_worker_broker_outage_and_stale_hints_do_not_throttle_sql_backlog(self):
        for outage in (True, False):
            ledger_fake = Mock()
            rows = [{"id": uuid4(), "tenant": "synthetic", "fence": 1} for _ in range(128)]
            ledger_fake.claim.side_effect = iter(rows)
            ledger_fake.finish.return_value = True
            class Workflow:
                async def run(self, row):
                    return Proposal("ABSTAIN", "synthetic")
            clock = [0.0]
            def sleep(seconds):
                clock[0] += seconds
                if ledger_fake.finish.call_count == len(rows):
                    process.stopping = True
            consumer = Mock(had_delivery=True); consumer.poll.return_value = (None, None)
            factory = Mock(side_effect=RuntimeError("SYNTHETIC_BROKER_DOWN")) if outage else Mock(return_value=consumer)
            with patch.dict(os.environ, {"RAG_MODE": "lab", "RAG_OBSERVABILITY": "disabled", "RAG_RESILIENCE": "local_lab", "RAG_GEMINI_RESPONSES": "disabled"}), \
                    patch.object(process, "stopping", False), patch.object(process.sys, "argv", ["process", "worker"]), \
                    patch.object(process, "process_services", return_value=(ledger_fake, Workflow())), \
                    patch.object(process.signal, "signal"), patch.object(process.time, "sleep", side_effect=sleep), \
                    patch.object(process.time, "monotonic", side_effect=lambda: clock[0]), \
                    patch.object(resilience, "broker_reconnect_delay", return_value=10), patch.object(broker, "Consumer", factory), \
                    redirect_stdout(io.StringIO()):
                process.main()
            self.assertEqual(ledger_fake.finish.call_count, 128)
            self.assertEqual(ledger_fake.claim.call_count, 128)
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(ledger_fake.heartbeat.call_count, 2)
            consumer.outcome.assert_not_called()

    def test_retention_fixture_stops_owned_retainer_before_aging(self):
        tree = ast.parse((ROOT / "app/tests/lifecycle/delivery_fixture.py").read_text(encoding="utf8"))
        body = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run_round").body
        aging = next(node for node in body if isinstance(node, ast.Try) and
                     any(isinstance(child, ast.Constant) and isinstance(child.value, str) and "interval '31 days'" in child.value for child in ast.walk(node)))
        preceding = body[:body.index(aging)]
        calls = [tuple(ast.literal_eval(arg) for arg in node.value.args) for node in preceding
                 if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and ast.unparse(node.value.func) == "base.docker"]
        self.assertEqual(calls[-2:], [("stop", "worker"), ("stop", "delivery")])
        final_calls = [tuple(ast.literal_eval(arg) for arg in node.value.args) for node in aging.finalbody
                       if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and ast.unparse(node.value.func) == "base.docker"]
        self.assertEqual(final_calls, [("start", "delivery"), ("start", "worker")])

    def test_postgres_rto_gate_counts_from_fault_and_preserves_original_deadline(self):
        with patch.object(sys, "argv", ["offline-runtime-contracts"]):
            import reliability_gate as gate
        def wait(check, seconds):
            self.assertLessEqual(seconds, 60)
            self.assertFalse(check()); self.assertTrue(check())
        with patch.object(gate.base, "docker") as docker, \
                patch.object(gate, "resume_killed", return_value={"running": 1}), \
                patch.object(gate.base, "http", side_effect=[(503, {}), (200, {})]), \
                patch.object(gate.qa, "wait", side_effect=wait), patch.object(gate, "save"), \
                patch.object(gate.time, "monotonic", side_effect=[0, 20, 35, 35]):
            proof = gate.postgres_restart_probe()
        docker.assert_called_once_with("kill", "-s", "SIGKILL", "postgres")
        self.assertTrue(proof["passed"]); self.assertEqual(proof["http_statuses"], [503, 200])
        self.assertEqual(proof["elapsed_seconds"], 35)
        with patch.object(gate.base, "docker"), patch.object(gate, "resume_killed", return_value={"running": 1}), \
                patch.object(gate.qa, "wait"), patch.object(gate, "save"), \
                patch.object(gate.time, "monotonic", side_effect=[0, 59, 60.01, 60.01]), self.assertRaises(AssertionError):
            gate.postgres_restart_probe()

    def test_restore_health_requires_tcp_and_preserves_stop_grace(self):
        import yaml
        config = yaml.safe_load((ROOT / "app/infrastructure/compose/qa/compose.reliability-qa.yaml").read_text(encoding="utf8"))
        restore = config["services"]["restore-postgres"]
        self.assertEqual(restore["stop_grace_period"], "120s")
        self.assertEqual(restore["healthcheck"]["start_period"], "300s")
        command = restore["healthcheck"]["test"][1].split()
        self.assertIn("-h", command)
        self.assertEqual(command[command.index("-h") + 1], "127.0.0.1")

    def test_production_mode_stays_closed(self):
        from rag_app.config import enforce_lab
        for mode in ("production", "test", "", "local_lab"):
            with patch.dict(os.environ, {"RAG_MODE": mode}), self.assertRaises(RuntimeError):
                enforce_lab()
        with patch.dict(os.environ, {"RAG_MODE": "lab"}):
            enforce_lab()

    def test_sql_counter_decimal_serialization_and_windows_source_hashes(self):
        # Run the current gate's embedded code, not an equivalent reimplementation.
        with patch.object(sys, "argv", ["offline-runtime-contracts"]):
            import reliability_gate as gate
        raw = {"active": 0, "shard_pending": Decimal(0), "active_bytes": Decimal(0), "shard_bytes": Decimal(0),
               "tenant_mismatches": 0, "missing_jobs": 0, "missing_accept_audits": 0,
               "missing_terminal_audits": 0, "undelivered": 0, "all_receipts": 5}
        db = Mock(); db.execute.return_value = Result(row=raw)
        def inside(code):
            text = io.StringIO()
            with redirect_stdout(text):
                exec("import json\nfrom rag_app import ledger\n" + code, {})
            return json.loads(text.getvalue())
        with patch.object(ledger, "connect", side_effect=connected(db)), patch.object(gate.qa, "inside", side_effect=inside):
            result = gate.global_invariants()
        self.assertTrue(all(type(value) is int for value in result.values()))
        with patch.object(gate.qa, "hashes", return_value={"app\\src\\rag_app\\persistence\\ledger.py": "synthetic-sha"}):
            values = gate.sources()
        self.assertEqual(values["app/src/rag_app/persistence/ledger.py"], "synthetic-sha")
        self.assertFalse(any("\\" in name for name in values))


if __name__ == "__main__":
    unittest.main(verbosity=2)
