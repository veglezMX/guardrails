"""Initial v2 tenant-scoped storage; run as migration owner only."""
revision = "0001_shared_state"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA_SQL = r"""
CREATE SCHEMA IF NOT EXISTS evolution;
CREATE OR REPLACE FUNCTION evolution.tenant_scope() RETURNS text
LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
DECLARE scope text;
BEGIN
 scope := current_setting('guardrails.tenant_id',true);
 IF scope IS NULL OR scope='' THEN RAISE EXCEPTION 'Tenant scope required' USING ERRCODE='42501'; END IF;
 RETURN scope;
END $$;
CREATE TABLE IF NOT EXISTS evolution.tenants (
 tenant_id text PRIMARY KEY CHECK (tenant_id ~ '^([a-z0-9][a-z0-9-]{0,63}|_platform)$')
);
CREATE TABLE IF NOT EXISTS evolution.records (
 tenant_id text NOT NULL REFERENCES evolution.tenants(tenant_id),
 kind text NOT NULL CHECK (length(kind) BETWEEN 1 AND 80),
 key text NOT NULL CHECK (length(key) BETWEEN 1 AND 256),
 data jsonb NOT NULL CHECK (jsonb_typeof(data)='object'),
 canonical_bytes bytea NOT NULL,
 digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
 revision integer NOT NULL DEFAULT 1 CHECK (revision>=1),
 immutable boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 audit_sequence bigint,
 principal_kind text GENERATED ALWAYS AS (CASE WHEN kind IN ('credentials','sessions') THEN 'principals' END) STORED,
 principal_key text GENERATED ALWAYS AS (CASE WHEN kind IN ('credentials','sessions') THEN data->>'principal_id' END) STORED,
 workload_kind text GENERATED ALWAYS AS (CASE WHEN data->>'workload_id' IS NOT NULL AND kind IN ('credentials','bindings','binding_pointers','contexts','receipts') THEN 'workloads' END) STORED,
 workload_key text GENERATED ALWAYS AS (CASE WHEN kind IN ('credentials','bindings','binding_pointers','contexts','receipts') THEN data->>'workload_id' END) STORED,
 policy_kind text GENERATED ALWAYS AS (CASE WHEN kind IN ('approvals','bindings','releases','policy_status') THEN 'policies' END) STORED,
 policy_key text GENERATED ALWAYS AS (CASE WHEN kind IN ('approvals','bindings','releases','policy_status') THEN (data->'policy'->>'policy_id') || ':' || (data->'policy'->>'version') END) STORED,
 PRIMARY KEY(tenant_id,kind,key),
 UNIQUE (tenant_id,audit_sequence),
 CHECK ((kind='audit')=(audit_sequence IS NOT NULL)),
 FOREIGN KEY(tenant_id,principal_kind,principal_key) REFERENCES evolution.records(tenant_id,kind,key),
 FOREIGN KEY(tenant_id,workload_kind,workload_key) REFERENCES evolution.records(tenant_id,kind,key),
 FOREIGN KEY(tenant_id,policy_kind,policy_key) REFERENCES evolution.records(tenant_id,kind,key),
 CHECK (data->>'tenant_id' IS NULL OR data->>'tenant_id'=tenant_id),
 CHECK (kind NOT IN ('credentials','sessions') OR principal_key IS NOT NULL),
 CHECK (kind NOT IN ('bindings','binding_pointers') OR workload_key IS NOT NULL),
 CHECK (kind NOT IN ('approvals','bindings','releases') OR policy_key IS NOT NULL),
 CHECK (data->'policy'->>'tenant_id' IS NULL OR data->'policy'->>'tenant_id'=tenant_id),
 CHECK ((kind NOT IN ('policies','approvals','bindings','audit','outbox','snapshots','evidence','checkpoints') AND kind NOT LIKE 'config:%') OR immutable)
);
CREATE INDEX IF NOT EXISTS records_page_idx ON evolution.records(tenant_id,kind,created_at,key);
CREATE TABLE IF NOT EXISTS evolution.record_links (
 tenant_id text NOT NULL,
 source_kind text NOT NULL,
 source_key text NOT NULL,
 target_kind text NOT NULL,
 target_key text NOT NULL,
 PRIMARY KEY(tenant_id,source_kind,source_key,target_kind,target_key),
 FOREIGN KEY(tenant_id,source_kind,source_key) REFERENCES evolution.records(tenant_id,kind,key),
 FOREIGN KEY(tenant_id,target_kind,target_key) REFERENCES evolution.records(tenant_id,kind,key)
);
CREATE TABLE IF NOT EXISTS evolution.audit_heads (
 tenant_id text PRIMARY KEY REFERENCES evolution.tenants(tenant_id),
 sequence bigint NOT NULL CHECK(sequence>=0),
 event_hash text,
 checkpoint_sequence bigint NOT NULL DEFAULT 0,
 checkpoint_hash text
);
CREATE OR REPLACE FUNCTION evolution.reject_immutable_change() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF OLD.immutable THEN
  IF TG_OP='DELETE' AND OLD.kind='audit' AND current_user=pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid='evolution.records'::regclass)) AND current_setting('guardrails.retention',true)='checkpointed' THEN RETURN OLD; END IF;
  RAISE EXCEPTION 'Immutable record' USING ERRCODE='42501';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 IF NEW.tenant_id<>OLD.tenant_id OR NEW.kind<>OLD.kind OR NEW.key<>OLD.key OR NEW.immutable<>OLD.immutable THEN
  RAISE EXCEPTION 'Immutable record identity' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS immutable_records ON evolution.records;
CREATE TRIGGER immutable_records BEFORE UPDATE OR DELETE ON evolution.records FOR EACH ROW EXECUTE FUNCTION evolution.reject_immutable_change();
ALTER TABLE evolution.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE evolution.tenants FORCE ROW LEVEL SECURITY;
ALTER TABLE evolution.records ENABLE ROW LEVEL SECURITY;
ALTER TABLE evolution.records FORCE ROW LEVEL SECURITY;
ALTER TABLE evolution.record_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE evolution.record_links FORCE ROW LEVEL SECURITY;
ALTER TABLE evolution.audit_heads ENABLE ROW LEVEL SECURITY;
ALTER TABLE evolution.audit_heads FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON evolution.tenants;
CREATE POLICY tenant_isolation ON evolution.tenants USING (tenant_id=evolution.tenant_scope()) WITH CHECK (tenant_id=evolution.tenant_scope());
DROP POLICY IF EXISTS tenant_isolation ON evolution.records;
CREATE POLICY tenant_isolation ON evolution.records USING (tenant_id=evolution.tenant_scope()) WITH CHECK (tenant_id=evolution.tenant_scope());
DROP POLICY IF EXISTS tenant_isolation ON evolution.record_links;
CREATE POLICY tenant_isolation ON evolution.record_links USING (tenant_id=evolution.tenant_scope()) WITH CHECK (tenant_id=evolution.tenant_scope());
DROP POLICY IF EXISTS tenant_isolation ON evolution.audit_heads;
CREATE POLICY tenant_isolation ON evolution.audit_heads USING (tenant_id=evolution.tenant_scope()) WITH CHECK (tenant_id=evolution.tenant_scope());
CREATE OR REPLACE FUNCTION evolution.purge_audit(checkpoint_id text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE scope text; cp jsonb; cut bigint; expected_hash text; deleted bigint;
BEGIN
 scope:=evolution.tenant_scope();
 PERFORM pg_advisory_xact_lock(hashtextextended(scope || ':audit',0));
 SELECT data INTO cp FROM evolution.records WHERE tenant_id=scope AND kind='checkpoints' AND key=checkpoint_id;
 IF cp IS NULL OR cp->>'signature' IS NULL OR cp->>'key_id' IS NULL THEN RAISE EXCEPTION 'Signed checkpoint required' USING ERRCODE='42501'; END IF;
 cut:=(cp->>'last_sequence')::bigint;
 SELECT data->>'event_hash' INTO expected_hash FROM evolution.records WHERE tenant_id=scope AND kind='audit' AND audit_sequence=cut;
 IF expected_hash IS NULL OR expected_hash<>cp->>'last_hash' THEN RAISE EXCEPTION 'Checkpoint mismatch' USING ERRCODE='42501'; END IF;
 IF cut<=(SELECT checkpoint_sequence FROM evolution.audit_heads WHERE tenant_id=scope) THEN RAISE EXCEPTION 'Checkpoint stale' USING ERRCODE='42501'; END IF;
 PERFORM set_config('guardrails.retention','checkpointed',true);
 DELETE FROM evolution.record_links WHERE tenant_id=scope AND target_kind='audit' AND target_key IN (SELECT key FROM evolution.records WHERE tenant_id=scope AND kind='audit' AND audit_sequence<=cut);
 DELETE FROM evolution.records WHERE tenant_id=scope AND kind='audit' AND audit_sequence<=cut;
 GET DIAGNOSTICS deleted=ROW_COUNT;
 UPDATE evolution.audit_heads SET checkpoint_sequence=cut,checkpoint_hash=expected_hash WHERE tenant_id=scope;
 PERFORM set_config('guardrails.retention','',true);
 RETURN deleted;
END $$;
REVOKE ALL ON SCHEMA evolution FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA evolution FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA evolution FROM PUBLIC;
"""


def upgrade():
    from alembic import op
    op.get_bind().connection.driver_connection.execute(SCHEMA_SQL)


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; restore an approved backup")
