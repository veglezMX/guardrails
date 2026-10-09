CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY CHECK (version = 1));
INSERT INTO schema_migrations VALUES (1);
CREATE TABLE tenants (tenant_id TEXT PRIMARY KEY, document TEXT NOT NULL);
CREATE TABLE workloads (
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id), workload_id TEXT NOT NULL,
    document TEXT NOT NULL, PRIMARY KEY (tenant_id, workload_id)
);
CREATE TABLE credentials (
    credential_id TEXT PRIMARY KEY, token_sha256 TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL, tenant_id TEXT REFERENCES tenants(tenant_id), workload_id TEXT,
    revoked_at TEXT, document TEXT NOT NULL,
    FOREIGN KEY (tenant_id, workload_id) REFERENCES workloads(tenant_id, workload_id)
);
CREATE TABLE policies (
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id), policy_id TEXT NOT NULL,
    version TEXT NOT NULL, ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    digest TEXT NOT NULL, document TEXT NOT NULL, operation_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, policy_id, version), UNIQUE (tenant_id, policy_id, ordinal)
);
CREATE TABLE binding_history (
    tenant_id TEXT NOT NULL, workload_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0), document TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workload_id, revision),
    FOREIGN KEY (tenant_id, workload_id) REFERENCES workloads(tenant_id, workload_id)
);
CREATE TABLE bindings_current (
    tenant_id TEXT NOT NULL, workload_id TEXT NOT NULL, revision INTEGER NOT NULL,
    policy_id TEXT NOT NULL, policy_version TEXT NOT NULL, document TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workload_id),
    FOREIGN KEY (tenant_id, workload_id, revision) REFERENCES binding_history(tenant_id, workload_id, revision),
    FOREIGN KEY (tenant_id, policy_id, policy_version) REFERENCES policies(tenant_id, policy_id, version)
);
CREATE TABLE governance_floors (
    scope TEXT NOT NULL CHECK (scope IN ('organization','tenant')), scope_id TEXT NOT NULL,
    document TEXT NOT NULL, PRIMARY KEY (scope, scope_id)
);
CREATE TABLE operations (
    operation_id TEXT PRIMARY KEY, desired_generation INTEGER NOT NULL UNIQUE CHECK (desired_generation > 0),
    actor_id TEXT NOT NULL, tenant_id TEXT REFERENCES tenants(tenant_id), committed_at TEXT NOT NULL,
    applied_at TEXT, snapshot_digest TEXT NOT NULL, snapshot TEXT NOT NULL
);
CREATE TABLE runtime_ack (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1), generation INTEGER NOT NULL,
    digest TEXT NOT NULL, acknowledged_at TEXT NOT NULL, service_id TEXT NOT NULL
);
CREATE TABLE management_audit (
    event_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, tenant_id TEXT, workload_id TEXT,
    correlation_id TEXT, document TEXT NOT NULL
);
CREATE INDEX audit_order ON management_audit(occurred_at, event_id);
CREATE TRIGGER policies_no_update BEFORE UPDATE ON policies BEGIN SELECT RAISE(ABORT, 'immutable_policy'); END;
CREATE TRIGGER policies_no_delete BEFORE DELETE ON policies BEGIN SELECT RAISE(ABORT, 'immutable_policy'); END;
CREATE TRIGGER history_no_update BEFORE UPDATE ON binding_history BEGIN SELECT RAISE(ABORT, 'immutable_history'); END;
CREATE TRIGGER history_no_delete BEFORE DELETE ON binding_history BEGIN SELECT RAISE(ABORT, 'immutable_history'); END;
CREATE TRIGGER audit_no_update BEFORE UPDATE ON management_audit BEGIN SELECT RAISE(ABORT, 'immutable_audit'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON management_audit BEGIN SELECT RAISE(ABORT, 'immutable_audit'); END;
