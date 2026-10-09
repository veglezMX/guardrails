CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY CHECK (version=1));
INSERT INTO schema_migrations VALUES (1);
CREATE TABLE decision_audit (
  event_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL,
  tenant_id TEXT NOT NULL, workload_id TEXT NOT NULL,
  correlation_id TEXT NOT NULL, category TEXT,
  document TEXT NOT NULL
);
CREATE TABLE client_events (
  event_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL,
  tenant_id TEXT NOT NULL, workload_id TEXT NOT NULL,
  correlation_id TEXT NOT NULL, category TEXT,
  document TEXT NOT NULL
);
CREATE INDEX decision_scope ON decision_audit(tenant_id, occurred_at, event_id);
CREATE INDEX client_scope ON client_events(tenant_id, occurred_at, event_id);
