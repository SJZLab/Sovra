"""Alembic environment (FTF-0102).

Resolves the real migration connection from `SOVRA_DATABASE_URL` only — no
hardcoded/development-fallback credential, matching FTF-0005's
`configuration_system.py` rule for every other Factory entry point. This is
deliberately the *migration* role's URL (schema owner, e.g. the
`postgres:16-alpine` image's bootstrap superuser locally), never the
restricted `sovra_app` runtime role `organization_workspace_schema.py`
defines — that role has no DDL privileges by design.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth.organization_workspace_schema import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("SOVRA_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "SOVRA_DATABASE_URL is required to run migrations — no fallback "
            "connection string exists (same rule as apps/api/app/config/"
            "configuration_system.py)."
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
