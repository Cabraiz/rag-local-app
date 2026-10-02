# RAG local: reliability and Grafana

Runtime remains a laboratory on D:, not production HA. Python/ADK and private
PostgreSQL/Qdrant/embeddings are unchanged as the core. All infrastructure below
runs locally; no billing or paid model fallback was added.

## Requests and messaging

FastAPI validates identity/body, bounds in-flight admissions (16/API process) and
per-principal rate (3/s with burst 32/API process). These are local process bounds,
not a distributed rate guarantee. Requests/jobs/audit/outbox commit together before
202. On a lost confirmation, resolve or retry with the SAME idempotency key.
An overload rejection before admission is explicit. A 503 with
LEDGER_UNAVAILABLE_RETRY_SAME_KEY is instead an uncertain SQL confirmation: resolve
the same key; do not assume that the transaction never committed.

Eight deterministic tenant budget partitions replace the shared admission lock.
Their conservative limits sum to 100000 requests/32 MiB. A hot partition can reject
earlier. Claiming does not acquire the admission lock. A tenant still has its own
1024 outstanding-request upper bound. This is not evidence of 100k successful
parallel RAG executions. Physical disk exhaustion still needs operator recovery.

The relay publishes only request UUIDs to a durable RabbitMQ quorum queue using
publisher confirms and a reused channel/connection (not reconnecting for every
job). Idle polling services heartbeats; a broken connection is replaced.
Workers sequentially pull one delivery, use SQL claims and
fence tokens (basic.get does not rely on prefetch for limiting outstanding work). ACK is
after a SQL terminal state, never before the result commit. For transient errors,
the retry intent commits to SQL before dead-lettering the old transport delivery;
the relay emits the next due attempt. Invalid transport bodies cannot create work.
The transport queue and dead-letter queue are bounded. At least once implies
duplicates: durable state/deduplication, not an exactly-once marketing claim.

Broker outage permits direct SQL claiming. The relay re-emits still-due jobs after
15s, so a lost or exhausted transport delivery cannot orphan an accepted request.
Workers also scan SQL every five seconds when broker polling yields no claim, so
a healthy broker with a stopped relay does not strand accepted work.
Retries stop at three attempts or the deadline (15 minutes). Retry waits are
bounded exponential backoff with jitter. Permanent errors become FAILED_FINAL.
Gemini timeout/reservation remains fail-closed: no automatic second paid/provider
attempt, no quota reset, and no replacement by unsupported model output.

## Dependency isolation and cache

Circuit breakers and concurrency slots are in PostgreSQL, shared across workers.
Each dependency has independent slots; slot/probe leases expire, and generation
fencing rejects late completion from an old circuit generation. Three consecutive
transient failures open a circuit for 30s; one half-open probe is allowed. Existing
RPC budgets remain (embedding 10s, Qdrant 5s, Gemini 20s, MCP 40s overall). Worker
workflow is bounded to 35s with Gemini or 10s without it; timed-out background
thread work cannot commit an old request thanks to fences, but is not a hard-killed
thread. Slots conservatively stay occupied until cleanup or their 60s expiry.
Transient classification covers nested exception groups, HTTPX/HTTPX2 transport
errors, Gemini SDK 429/5xx and scoped MCP 429 errors. Authorization failures are
not reclassified as transient availability failures.

Redis is a disposable 128 MiB LRU cache, not a queue or authorization source.
Candidate keys include tenant, actor, immutable corpus release/model and query.
Embedding keys include scope, model, calibration and texts. Values are HMAC signed,
validated and expire in five minutes. Canonical SQL ACL/version/expiry checks happen
after candidate cache hits and again when committing/delivering citations. Miss,
invalid value or Redis outage uses the original uncached code. Redis has no port
published on the host and no persistence dependency.

## Monitoring and operations

Grafana: http://127.0.0.1:8850/d/rag-overview/rag-local

Anonymous access is local Viewer only. The generated admin credential is private;
signup, plugin installation, analytics and update downloads are disabled. Grafana
and Prometheus use bounded/persistent local volumes. Prometheus is private, scrapes
only aggregate app metrics and RabbitMQ metrics every 5s. The provisioned dashboard
has ten panels for pending states/bytes/age, worker health, cache, broker and circuits.
Prometheus evaluates alert rules locally; no email/pager notification channel is
configured. Existing sanitized OTEL traces remain in the collector, not in a new
Grafana log/trace datasource. Metrics never include questions, tokens or tenant IDs.

Existing Docker restart policies supervise services beyond the Codex turn. Exact
startup command is in app/tools/supervise-runtime.ps1; it uses the base, retrieval,
semantic, integrations, Gemini, documents, delivery, observability, resilience,
resilience-integrations and Grafana profiles with --no-build. QA overrides MUST NOT
be used in this runtime. Build api with Dockerfile.resilience and then the gateway
with Dockerfile.integrations.resilience. Source/dependencies and upstream images
are pinned; no compose service follows an unpinned latest tag.

Heartbeats are written at most once per five seconds per process, not once per
poll. Worker stale thresholds allow the existing workflow/lease budget (60s with
Gemini, 30s without); other roles retain 15s. Aggregate role heartbeats do NOT
identify every replica independently. Nonessential cache/broker event counts
are batched by a bounded, allowlisted daemon buffer every five seconds. Crash or
uncertain metric commits can lose/duplicate those approximate diagnostic counts;
they never decide acceptance, ownership, quota, delivery or billing. Request
states/audits/outbox and shared circuit/semaphore transitions stay synchronous
and durable. Do not disable fsync to improve the laboratory's I/O bottleneck.

## Recovery limitations and proof boundaries

Pre-change PostgreSQL custom-format backup is private at
.local/backups/rag-before-resilience-20261001.dump. Restore validation uses an
isolated database and --no-owner --no-acl; it validates data, not production account
recovery. A separately stored backup, secrets/roles recovery, RPO/RTO and failure
domains still need a production plan. Do not treat this same-D: backup or a one-node
quorum broker as protection from losing the computer or disk.

Test scripts: app/tests/resilience_fixture.py (two fresh adversarial seeds) and
app/tests/resilience_load.py (100000 offered HTTP requests with accepted/rejected
and persisted terminal conservation, two workers matching the live lab). The
first one-worker load and subsequent two-worker load failed their drain deadlines;
both failed receipts are preserved. The second run exposed synchronous telemetry
and heartbeat I/O contention; later rounds validate the batched implementation.
They do not prove two independent blind
audits, paid Vertex integration, 100000 simultaneous successes or production HA.
