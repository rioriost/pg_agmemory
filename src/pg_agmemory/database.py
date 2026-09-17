import os
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

import psycopg
from psycopg.rows import dict_row

Connection = psycopg.AsyncConnection[dict[str, Any]]
MIGRATIONS = (
    "001_initial.sql",
    "002_assertion_revisions.sql",
    "003_checkpoints.sql",
    "004_tool_effects.sql",
    "005_relational_graph.sql",
    "006_durable_jobs.sql",
)
SCHEMA_VERSION = len(MIGRATIONS)


@dataclass(frozen=True)
class Settings:
    database_url: str
    jwt_public_key: str
    jwt_issuer: str
    jwt_audience: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ["PGAG_DATABASE_URL"],
            jwt_public_key=os.environ["PGAG_JWT_PUBLIC_KEY"],
            jwt_issuer=os.environ["PGAG_JWT_ISSUER"],
            jwt_audience=os.environ["PGAG_JWT_AUDIENCE"],
        )


async def connect(url: str) -> Connection:
    return await psycopg.AsyncConnection.connect(
        url,
        autocommit=True,
        row_factory=dict_row,
        connect_timeout=5,
        options="-c statement_timeout=5000 -c lock_timeout=5000",
    )


async def validate_runtime(url: str) -> None:
    async with await connect(url) as conn:
        cursor = await conn.execute(
            """SELECT rolsuper, rolbypassrls,
                EXISTS (
                    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname IN ('memory','memory_ops')
                      AND pg_has_role(current_user, c.relowner, 'MEMBER')
                ) AS owns_tables
                FROM pg_roles WHERE rolname = current_user"""
        )
        role = await cursor.fetchone()
        if not role or role["rolsuper"] or role["rolbypassrls"] or role["owns_tables"]:
            raise RuntimeError("Runtime database role must not own tables or bypass RLS")
        try:
            versions = await (
                await conn.execute(
                    "SELECT version FROM public.pgag_schema_migration ORDER BY version"
                )
            ).fetchall()
        except (psycopg.errors.UndefinedTable, psycopg.errors.InsufficientPrivilege) as exc:
            raise RuntimeError("Database schema is unavailable; run pg-agmemory migrate") from exc
        if [row["version"] for row in versions] != list(range(1, SCHEMA_VERSION + 1)):
            raise RuntimeError("Database schema version mismatch; run matching migrations and API")


def migrate(url: str) -> None:
    with psycopg.connect(url) as conn:
        conn.execute("SET LOCAL lock_timeout = '5s'")
        conn.execute("SELECT pg_advisory_xact_lock(742091830)")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS public.pgag_schema_migration
               (version integer PRIMARY KEY, applied_at timestamptz DEFAULT clock_timestamp())"""
        )
        installed = [
            row[0]
            for row in conn.execute(
                "SELECT version FROM public.pgag_schema_migration ORDER BY version"
            ).fetchall()
        ]
        if installed != list(range(1, len(installed) + 1)) or len(installed) > SCHEMA_VERSION:
            raise RuntimeError("Unsupported database migration history")
        for version, name in enumerate(MIGRATIONS, start=1):
            if version <= len(installed):
                continue
            sql = files("pg_agmemory").joinpath("storage", name).read_text()
            conn.execute(sql)
            conn.execute(
                "INSERT INTO public.pgag_schema_migration(version) VALUES (%s)", (version,)
            )
