"""Alembic entry point: only migration-only mounted DSN files are accepted."""
import os
from pathlib import Path
from alembic import context
from sqlalchemy import create_engine


def run_migrations_online():
    dsn_file = os.environ.get("EVOLUTION_MIGRATION_DSN_FILE")
    if not dsn_file:
        raise RuntimeError("EVOLUTION_MIGRATION_DSN_FILE is required")
    engine = create_engine(Path(dsn_file).read_text().strip(), hide_parameters=True)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Migrations require an authenticated owner connection")
run_migrations_online()
