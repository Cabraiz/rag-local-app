# Resilience implementation acceptance — 2026-10-01

Requested: preserve the existing RAG and data; improve concurrency, retries,
timeouts and failure isolation; add RabbitMQ, optional Redis cache and a usable
local Grafana. No billing, external mutations or new Gemini evaluations.

## Contract before implementation

- Expected: HTTP 202 means the request, job and audit/outbox committed together.
  Interrupted confirmation resolves with the SAME scoped idempotency key.
- Forbidden: return accepted without commit, acknowledge a broker delivery before
  a persisted outcome, serve a revoked citation from cache, reset quota counters,
  publish secrets/questions in metrics, or silently turn on paid inference.
- Broker messages contain request IDs only. SQL remains authoritative; broker
  downtime never erases accepted jobs. SQL recovery remains available.
- Cache contains validated embeddings or scoped candidate IDs only, expires and
  is size bounded. Redis failure falls through to the original implementation.
- Global SQL locks are removed from job claiming. Admission is conservatively
  partitioned into eight deterministic tenant buckets, totaling at most 100000
  pending jobs / 32 MiB. One hot bucket may reject earlier than the global cap.
- Retry classifications distinguish permanent errors from transient failures.
  Transient waits use bounded exponential backoff with jitter. Provider timeouts
  remain ambiguous; Gemini reservations are never retried or reset blindly.
- Circuit breakers and expiring concurrency slots are shared through SQL, not
  independent per-worker counters. Each integration has its own budget.
- Grafana is bound to loopback, anonymous Viewer only, no signup or external
  analytics. Prometheus and backing services have no public ports.

## Minimum proofs

1. Two scoped adversarial rounds against isolated real containers; reset the clean
   count on implementation changes. These are same-author regressions, not
   independent blind audits.
2. Concurrent duplicate submissions, restart/recovery, stale worker fencing,
   broker outage and duplicate publication, Redis outage and invalid cache,
   circuit open/half-open/concurrency, retry timing, role/tenant denial and ACL
   revalidation; all accepted receipts must be recoverable and accounted for.
3. Real HTTP offered-load receipt explicitly reports accepted, rejected,
   unresolved confirmations, latency and terminal accounting. This does NOT
   certify 100000 concurrent successful workflows, cloud quota or production HA.
4. Grafana health, provisioned dashboard, real Prometheus queries and operational
   failure visibility; no sensitive metric labels.
5. Pre/post SQL document counts and corpus heads conserved in the main lab.

## Boundaries

This is still a one-host laboratory. Docker restart policies are the existing
external supervisor (persistent-local-server skill); a blocked Windows task
registration must not be bypassed. Host/disk loss requires a separately stored
backup and tested restore. Do not claim recovery from disk loss from same-disk
volumes. Advanced SDK work, OIDC, Vertex and MCP writes stay out of this change.
