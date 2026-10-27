"""Bind immutable configuration keys to their canonical embedded identity.

The existing (tenant_id, kind, key) primary key then enforces uniqueness of
tenant, resource family, resource ID and version even for direct SQL writers.
Existing malformed rows cause an explicit migration failure; never rewrite
approved historical bytes to make a constraint pass.
"""
revision = "0002_config_identity"
down_revision = "0001_shared_state"
branch_labels = None
depends_on = None

SCHEMA_SQL = r"""
DO $$
BEGIN
 IF NOT EXISTS (
  SELECT 1 FROM pg_constraint
  WHERE conrelid='evolution.records'::regclass AND conname='records_config_identity'
 ) THEN
  ALTER TABLE evolution.records ADD CONSTRAINT records_config_identity CHECK (
   kind NOT LIKE 'config:%' OR (
    kind IN ('config:lists','config:residency','config:destinations','config:providers','config:tools','config:quotas')
    AND jsonb_typeof(data->'tenant_id')='string'
    AND data->>'tenant_id'=tenant_id
    AND jsonb_typeof(data->'version')='number'
    AND data->>'version' ~ '^[1-9][0-9]*$'
    AND (CASE kind
     WHEN 'config:lists' THEN data->>'list_id'
     WHEN 'config:residency' THEN data->>'residency_id'
     WHEN 'config:destinations' THEN data->>'destination_id'
     WHEN 'config:providers' THEN data->>'provider_id'
     WHEN 'config:tools' THEN data->>'tool_id'
     WHEN 'config:quotas' THEN data->>'quota_id'
    END) ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    AND key = (CASE kind
     WHEN 'config:lists' THEN data->>'list_id'
     WHEN 'config:residency' THEN data->>'residency_id'
     WHEN 'config:destinations' THEN data->>'destination_id'
     WHEN 'config:providers' THEN data->>'provider_id'
     WHEN 'config:tools' THEN data->>'tool_id'
     WHEN 'config:quotas' THEN data->>'quota_id'
    END) || ':' || (data->>'version')
   ) IS TRUE
  );
 END IF;
END $$;
"""


def upgrade():
    from alembic import op
    op.get_bind().connection.driver_connection.execute(SCHEMA_SQL)


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; restore an approved backup")
