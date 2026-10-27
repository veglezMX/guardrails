"""Constrain private quota state and index bounded active-lease queries."""

revision = "0003_quota_state"
down_revision = "0002_config_identity"
branch_labels = None
depends_on = None

SCHEMA_SQL = r"""
DO $$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='evolution.records'::regclass AND conname='records_quota_window') THEN
  ALTER TABLE evolution.records ADD CONSTRAINT records_quota_window CHECK (
   kind<>'quota_windows' OR (
    data->>'tenant_id'=tenant_id AND jsonb_typeof(data->'tenant_id')='string'
    AND (data->>'scope'='tenant' OR data->>'scope' ~ '^workload:[a-z0-9][a-z0-9-]{0,63}$')
    AND jsonb_typeof(data->'seconds')='number' AND data->>'seconds' IN ('1','60')
    AND jsonb_typeof(data->'window_start')='number' AND data->>'window_start' ~ '^[0-9]+$'
    AND jsonb_typeof(data->'count')='number' AND data->>'count' ~ '^[1-9][0-9]*$'
    AND key=(data->>'scope') || ':' || (data->>'seconds') || ':' || (data->>'window_start')
    AND NOT immutable
    AND data - ARRAY['tenant_id','scope','seconds','window_start','count','created_at','revision']='{}'::jsonb
   ) IS TRUE
  );
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='evolution.records'::regclass AND conname='records_quota_lease') THEN
  ALTER TABLE evolution.records ADD CONSTRAINT records_quota_lease CHECK (
   kind<>'quota_leases' OR (
    data->>'tenant_id'=tenant_id AND jsonb_typeof(data->'tenant_id')='string'
    AND data->>'principal_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    AND data->>'request_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    AND data->>'lease_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    AND data->>'workload_id' ~ '^[a-z0-9][a-z0-9-]{0,63}$'
    AND data->>'owner_hash' ~ '^[a-f0-9]{64}$' AND data->>'policy_digest' ~ '^[a-f0-9]{64}$'
    AND data->>'stage' IN ('input','output','retrieval','tool_arguments','tool_result')
    AND data->>'state' IN ('active','released','expired')
    AND jsonb_typeof(data->'expires_epoch')='number' AND (data->>'expires_epoch')::numeric>0
    AND key=(data->>'principal_id') || ':' || (data->>'workload_id') || ':' || (data->>'request_id')
    AND NOT immutable
    AND data - ARRAY['tenant_id','principal_id','workload_id','request_id','policy_digest','stage','lease_id','owner_hash','state','expires_epoch','created_at','revision']='{}'::jsonb
   ) IS TRUE
  );
 END IF;
END $$;
CREATE INDEX IF NOT EXISTS quota_active_expiry_idx
 ON evolution.records(tenant_id,((data->>'expires_epoch')::numeric),key)
 WHERE kind='quota_leases' AND data->>'state'='active';
"""


def upgrade():
    from alembic import op

    op.get_bind().connection.driver_connection.execute(SCHEMA_SQL)


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; restore an approved backup")
