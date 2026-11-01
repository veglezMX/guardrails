"""Verified TLS connection churn through the real owned PostgreSQL forward."""

import time
from pathlib import Path

import psycopg
from cluster_support import Cluster
from evolution.seed import private_read
from guardrails.evolution import models
from psycopg.conninfo import conninfo_to_dict


def test_twenty_verified_application_database_connections_preserve_owned_forward():
    cluster = Cluster("deterministic_mock")
    try:
        directory = cluster.state / "assembly"
        assembly = cluster.deployment.support("assembly")
        identity_path = directory / "forward-postgres.json"
        identity_bytes = private_read(identity_path)
        identity = models.strict_json_loads(identity_bytes)
        ports = models.strict_json_loads(private_read(directory / "ports.json"))
        port = ports["postgres"]
        assert identity["command"] == assembly._forward_digest(
            cluster.deployment, "postgres", port
        )
        assert assembly._process_identity(identity["pid"]) == identity
        assert assembly._forward_listening(identity, port)

        # Read the protected application URL; never render it in an assertion or
        # log. psycopg owns TLS end to end through the opaque pod-side relay.
        url = (
            private_read(cluster.access["database_url_file"], maximum=4096)
            .decode()
            .strip()
        )
        if not url.startswith("postgresql+psycopg://"):
            raise AssertionError("application database URL protocol invalid")
        try:
            parameters = conninfo_to_dict(
                url.replace("postgresql+psycopg://", "postgresql://", 1)
            )
        except (psycopg.Error, ValueError):
            raise AssertionError("application database URL invalid") from None
        assert parameters["host"] == "localhost"
        assert int(parameters["port"]) == port
        assert parameters["user"] == "evolution_app"
        assert parameters["sslmode"] == "verify-full"
        assert Path(parameters["sslrootcert"]) == Path(cluster.access["ca_file"])
        backend_pids = set()
        for _ in range(20):
            try:
                with psycopg.connect(
                    **parameters,
                    autocommit=True,
                    connect_timeout=5,
                    options="-c default_transaction_read_only=on -c statement_timeout=5000",
                ) as connection:
                    row = connection.execute(
                        "SELECT 1, current_user, pg_backend_pid(), tls.ssl, tls.version, "
                        "tls.cipher, current_setting('transaction_read_only'), role.rolsuper, role.rolbypassrls "
                        "FROM pg_stat_ssl AS tls JOIN pg_roles AS role ON role.rolname=current_user "
                        "WHERE tls.pid=pg_backend_pid()"
                    ).fetchone()
                    assert row is not None
                    assert row[0] == 1 and row[1] == "evolution_app"
                    assert row[2] not in backend_pids
                    backend_pids.add(row[2])
                    assert row[3] is True and row[4] in {"TLSv1.2", "TLSv1.3"}
                    assert isinstance(row[5], str) and bool(row[5])
                    assert row[6:] == ("on", False, False)
            except psycopg.Error:
                raise AssertionError(
                    "cluster application database TLS query failed"
                ) from None
            # Do not call access()/platform(): those can recreate a dead
            # forward and would conceal the connection-close regression.
            assert connection.closed
            assert private_read(identity_path) == identity_bytes
            assert assembly._process_identity(identity["pid"]) == identity
            assert assembly._forward_listening(identity, port)
        assert len(backend_pids) == 20
        # Observe delayed close propagation without sending a bare TCP probe.
        deadline = time.monotonic() + 0.25
        while time.monotonic() < deadline:
            assert assembly._process_identity(identity["pid"]) == identity
            assert assembly._forward_listening(identity, port)
            time.sleep(0.01)
        assert private_read(identity_path) == identity_bytes
    finally:
        cluster.close()
