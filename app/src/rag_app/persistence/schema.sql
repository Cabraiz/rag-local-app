CREATE TABLE IF NOT EXISTS schema_version(version integer PRIMARY KEY);
INSERT INTO schema_version VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS admission(id integer PRIMARY KEY CHECK(id=1), pending integer NOT NULL CHECK(pending>=0));
INSERT INTO admission VALUES (1,0) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS tenant_queue(tenant text PRIMARY KEY, pending integer NOT NULL DEFAULT 0 CHECK(pending>=0), last_claim timestamptz NOT NULL DEFAULT 'epoch');
CREATE TABLE IF NOT EXISTS requests(
 id uuid PRIMARY KEY, tenant text NOT NULL, actor text NOT NULL, idem text NOT NULL,
 payload_hash text NOT NULL, question text NOT NULL, state text NOT NULL
 CHECK(state IN ('ACCEPTED','RUNNING','RETRY_WAIT','SUCCEEDED','FAILED_FINAL','EXPIRED','CANCELLED')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), deadline timestamptz NOT NULL,
 lease_until timestamptz, next_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 fence bigint NOT NULL DEFAULT 0, attempts integer NOT NULL DEFAULT 0, result jsonb,
 UNIQUE(tenant,actor,idem));
CREATE INDEX IF NOT EXISTS requests_due ON requests(tenant,state,next_at,created_at);
CREATE INDEX IF NOT EXISTS requests_expiry ON requests(state,deadline);
CREATE INDEX IF NOT EXISTS requests_lease ON requests(state,lease_until);
CREATE TABLE IF NOT EXISTS jobs(id uuid PRIMARY KEY REFERENCES requests(id));
CREATE TABLE IF NOT EXISTS audit(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, request_id uuid NOT NULL REFERENCES requests(id), kind text NOT NULL, at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(request_id,kind));
CREATE TABLE IF NOT EXISTS outbox(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, request_id uuid NOT NULL REFERENCES requests(id), kind text NOT NULL, at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(request_id,kind));
CREATE TABLE IF NOT EXISTS worker_health(role text PRIMARY KEY, at timestamptz NOT NULL);
-- Additive laboratory corpus extension. Never drops the lifecycle tables/data.
CREATE TABLE IF NOT EXISTS corpus_heads(
 tenant text NOT NULL, actor text NOT NULL, generation bigint NOT NULL DEFAULT 0,
 release_id uuid, PRIMARY KEY(tenant,actor));
CREATE TABLE IF NOT EXISTS corpus_releases(
 id uuid PRIMARY KEY, tenant text NOT NULL, actor text NOT NULL, manifest_hash text NOT NULL,
 parent_generation bigint NOT NULL, state text NOT NULL CHECK(state IN ('BUILDING','READY','SUPERSEDED')),
 embedding_version text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE INDEX IF NOT EXISTS corpus_candidate ON corpus_releases(tenant,actor,manifest_hash,parent_generation,state);
-- Preserve historical per-release indexes; new releases choose shared layout explicitly.
ALTER TABLE corpus_releases ADD COLUMN IF NOT EXISTS index_layout text NOT NULL DEFAULT 'legacy_release_v1';
CREATE TABLE IF NOT EXISTS corpus_documents(
 id uuid PRIMARY KEY, release_id uuid NOT NULL REFERENCES corpus_releases(id),
 tenant text NOT NULL, actor text NOT NULL, source_key text NOT NULL, title text NOT NULL,
 content_hash text NOT NULL, revoked boolean NOT NULL DEFAULT false,
 acl_epoch bigint NOT NULL DEFAULT 0, valid_until timestamptz,
 UNIQUE(release_id,source_key));
CREATE TABLE IF NOT EXISTS corpus_chunks(
 id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES corpus_documents(id),
 ordinal integer NOT NULL, quote text NOT NULL, UNIQUE(document_id,ordinal));
ALTER TABLE requests ADD COLUMN IF NOT EXISTS source_snapshot uuid;
ALTER TABLE corpus_documents ADD COLUMN IF NOT EXISTS original_hash text;
ALTER TABLE corpus_documents ADD COLUMN IF NOT EXISTS original_media_type text;
ALTER TABLE corpus_documents ADD COLUMN IF NOT EXISTS original_bytes integer;
CREATE TABLE IF NOT EXISTS corpus_revocations(
 tenant text NOT NULL, actor text NOT NULL, source_key text NOT NULL,
 revoked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant,actor,source_key));
ALTER TABLE admission ADD COLUMN IF NOT EXISTS pending_bytes bigint NOT NULL DEFAULT 0 CHECK(pending_bytes>=0);
ALTER TABLE tenant_queue ADD COLUMN IF NOT EXISTS pending_bytes bigint NOT NULL DEFAULT 0 CHECK(pending_bytes>=0);
ALTER TABLE requests ADD COLUMN IF NOT EXISTS retention_managed boolean NOT NULL DEFAULT false;
ALTER TABLE requests ADD COLUMN IF NOT EXISTS content_expired boolean NOT NULL DEFAULT false;
ALTER TABLE requests ADD COLUMN IF NOT EXISTS terminal_at timestamptz;
CREATE INDEX IF NOT EXISTS requests_terminal_recent ON requests(terminal_at DESC) WHERE terminal_at IS NOT NULL;
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS delivered_at timestamptz;
CREATE TABLE IF NOT EXISTS notification_inbox(
 outbox_id bigint PRIMARY KEY REFERENCES outbox(id),
 delivered_at timestamptz NOT NULL DEFAULT clock_timestamp());
-- Migrate quiescent project before API/worker startup; preserve all rows.
UPDATE admission SET pending_bytes=(SELECT COALESCE(sum(octet_length(question)),0) FROM requests WHERE state IN ('ACCEPTED','RUNNING','RETRY_WAIT')) WHERE id=1;
UPDATE tenant_queue q SET pending_bytes=(SELECT COALESCE(sum(octet_length(question)),0) FROM requests r WHERE r.tenant=q.tenant AND r.state IN ('ACCEPTED','RUNNING','RETRY_WAIT'));
-- Additive resilience tables. Budget partitions initialize once, never reset live.
CREATE TABLE IF NOT EXISTS admission_shards(
 id integer PRIMARY KEY CHECK(id>=0 AND id<8),
 pending integer NOT NULL CHECK(pending>=0), pending_bytes bigint NOT NULL CHECK(pending_bytes>=0));
INSERT INTO admission_shards
 SELECT bucket, count(r.id)::integer, COALESCE(sum(octet_length(r.question)),0)
 FROM generate_series(0,7) bucket LEFT JOIN requests r
 ON r.state IN ('ACCEPTED','RUNNING','RETRY_WAIT')
 AND (('x'||substr(md5(r.tenant),1,8))::bit(32)::bigint % 8)=bucket
 GROUP BY bucket ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS dependency_control(
 name text PRIMARY KEY, failures integer NOT NULL DEFAULT 0,
 open_until timestamptz, probe_until timestamptz, generation bigint NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS dependency_slots(
 id uuid PRIMARY KEY, name text NOT NULL REFERENCES dependency_control(name), expires timestamptz NOT NULL,
 generation bigint NOT NULL DEFAULT 0);
INSERT INTO dependency_control(name) VALUES ('embedding'),('qdrant'),('gemini'),('github'),('jira') ON CONFLICT DO NOTHING;
CREATE INDEX IF NOT EXISTS dependency_slot_expiry ON dependency_slots(name,expires);
ALTER TABLE requests ADD COLUMN IF NOT EXISTS failure_code text;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS broker_fence bigint NOT NULL DEFAULT -1;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS broker_at timestamptz;
CREATE TABLE IF NOT EXISTS resilience_events(
 name text PRIMARY KEY, value bigint NOT NULL DEFAULT 0);
