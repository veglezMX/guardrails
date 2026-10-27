"""Actual relay password authentication, mandatory TLS and tenant RLS proof."""

import secrets
from pathlib import Path

import psycopg
from cluster_support import Cluster
from evolution.seed import private_read
from guardrails.evolution import models
from psycopg.conninfo import conninfo_to_dict


def parameters(path, role, cluster, port):
    # Neither the original URL nor driver diagnostics enter assertion output.
    raw = private_read(path, maximum=4096).decode().strip()
    if not raw.startswith("postgresql+psycopg://"):
        raise AssertionError("database credential URL protocol invalid")
    try:
        value = conninfo_to_dict(
            raw.replace("postgresql+psycopg://", "postgresql://", 1)
        )
    except psycopg.Error, ValueError:
        raise AssertionError("database credential URL invalid") from None
    safe = (
        value.get("user") == role
        and value.get("host") == "localhost"
        and value.get("port") == str(port)
        and value.get("dbname") == "guardrails_evolution"
        and value.get("sslmode") == "verify-full"
        and Path(value.get("sslrootcert", "")) == Path(cluster.access["ca_file"])
        and isinstance(value.get("password"), str)
        and bool(value["password"])
    )
    if not safe:
        raise AssertionError("database credential URL scope invalid")
    return {
        **value,
        "connect_timeout": 5,
        "options": "-c default_transaction_read_only=on -c statement_timeout=5000",
    }


def denied(value, role, *, plaintext=False):
    try:
        connection = psycopg.connect(**value, autocommit=True)
    except psycopg.Error as error:
        # libpq connection failures may have no structured SQLSTATE. In that
        # case require the precise server authentication or HBA rejection,
        # rather than accepting a timeout, refused socket, or TLS failure.
        if plaintext:
            expected = error.sqlstate == "28000" or (
                "pg_hba.conf rejects connection for host " in str(error)
                and f'user "{role}"' in str(error)
                and "no encryption" in str(error)
            )
        else:
            expected = error.sqlstate == "28P01" or (
                f'password authentication failed for user "{role}"' in str(error)
            )
        if not expected:
            raise AssertionError(
                "database denial was not the expected authentication rejection"
            ) from None
        return True
    else:
        connection.close()
        return False


def test_actual_postgres_relay_requires_password_tls_and_scoped_application_role():
    cluster = Cluster("deterministic_mock")
    try:
        directory = cluster.state / "assembly"
        assembly = cluster.deployment.support("assembly")
        identity_path = directory / "forward-postgres.json"
        identity_bytes = private_read(identity_path)
        identity = models.strict_json_loads(identity_bytes)
        port = models.strict_json_loads(private_read(directory / "ports.json"))[
            "postgres"
        ]
        assert assembly._process_identity(identity["pid"]) == identity
        assert assembly._forward_listening(identity, port)
        identities = (
            ("evolution_owner", directory / "private/database-owner-url"),
            ("evolution_app", Path(cluster.access["database_url_file"])),
        )
        for role, credential_file in identities:
            value = parameters(credential_file, role, cluster, port)
            try:
                with psycopg.connect(**value, autocommit=True) as connection:
                    row = connection.execute(
                        "SELECT current_user, tls.ssl, tls.version, role.rolsuper, role.rolbypassrls, "
                        "current_setting('transaction_read_only') FROM pg_stat_ssl tls "
                        "JOIN pg_roles role ON role.rolname=current_user WHERE tls.pid=pg_backend_pid()"
                    ).fetchone()
                    assert row is not None and row[0] == role
                    assert row[1] is True and row[2] in {"TLSv1.2", "TLSv1.3"}
                    assert row[5] == "on"
                    if role == "evolution_app":
                        assert row[3:5] == (False, False)
                        forced = connection.execute(
                            "SELECT count(*), bool_and(c.relrowsecurity AND c.relforcerowsecurity) "
                            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                            "WHERE n.nspname='evolution' AND c.relkind IN ('r','p')"
                        ).fetchone()
                        assert forced[0] > 0 and forced[1] is True
                        unscoped_denied = False
                        try:
                            connection.execute(
                                "SELECT count(*) FROM evolution.records"
                            ).fetchone()
                        except psycopg.Error as error:
                            unscoped_denied = (
                                error.sqlstate == "42501"
                                and error.diag.message_primary
                                == "Tenant scope required"
                            )
                        assert unscoped_denied, "missing tenant scope was not denied"
                        for tenant in cluster.tenants:
                            connection.execute(
                                "SELECT set_config('guardrails.tenant_id',%s,false)",
                                (tenant,),
                            )
                            visible, foreign = connection.execute(
                                "SELECT count(*),count(*) FILTER (WHERE tenant_id<>%s) FROM evolution.records",
                                (tenant,),
                            ).fetchone()
                            assert visible > 0 and foreign == 0
                    else:
                        assert row[3] is True
            except psycopg.Error:
                raise AssertionError(
                    "verified database role or RLS query failed"
                ) from None

            wrong = {**value, "password": "invalid-guardrails-" + secrets.token_hex(32)}
            authenticated_rejection = denied(wrong, role)
            assert authenticated_rejection, "wrong password unexpectedly authenticated"
            plaintext_rejection = denied(
                {**value, "sslmode": "disable"}, role, plaintext=True
            )
            assert plaintext_rejection, (
                "unencrypted credentials unexpectedly authenticated"
            )
            assert private_read(identity_path) == identity_bytes
            assert assembly._process_identity(identity["pid"]) == identity
            assert assembly._forward_listening(identity, port)
    finally:
        cluster.close()
