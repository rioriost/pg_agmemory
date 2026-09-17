import os
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row

from pg_agmemory.lexical import rebuild

Connection = psycopg.AsyncConnection[dict[str, Any]]
MIGRATIONS = (
    "001_initial.sql",
    "002_assertion_revisions.sql",
    "003_checkpoints.sql",
    "004_tool_effects.sql",
    "005_relational_graph.sql",
    "006_durable_jobs.sql",
    "007_japanese_fts.sql",
    "008_pgvector.sql",
    "009_scope_access.sql",
)
SCHEMA_VERSION = len(MIGRATIONS)
VECTOR_VERSION = "0.8.6"
VECTOR_QUERY = """SELECT e.extversion,n.nspname FROM pg_extension e
                  JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'vector'"""


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


class RuntimeValidationError(RuntimeError):
    def __init__(
        self,
        code: Literal[
            "runtime_role_invalid", "schema_unavailable",
            "schema_version_mismatch", "extension_version_mismatch",
        ],
        message: str,
    ) -> None:
        self.code = code
        super().__init__(message)


async def validate_runtime(url: str) -> None:
    async with await connect(url) as conn:
        await conn.execute("SET default_transaction_read_only = on")
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
            raise RuntimeValidationError(
                "runtime_role_invalid", "Runtime database role must not own tables or bypass RLS"
            )
        try:
            versions = await (
                await conn.execute(
                    "SELECT version FROM public.pgag_schema_migration ORDER BY version"
                )
            ).fetchall()
        except (psycopg.errors.UndefinedTable, psycopg.errors.InsufficientPrivilege) as exc:
            raise RuntimeValidationError(
                "schema_unavailable", "Database schema is unavailable; run pg-agmemory migrate"
            ) from exc
        if [row["version"] for row in versions] != list(range(1, SCHEMA_VERSION + 1)):
            raise RuntimeValidationError(
                "schema_version_mismatch",
                "Database schema version mismatch; run matching migrations and API",
            )
        extension = await (await conn.execute(VECTOR_QUERY)).fetchone()
        if not extension or extension != {"extversion": VECTOR_VERSION, "nspname": "public"}:
            raise RuntimeValidationError(
                "extension_version_mismatch", "pgvector 0.8.6 in public is required"
            )


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
            if name == "007_japanese_fts.sql":
                rebuild(conn)
            conn.execute(
                "INSERT INTO public.pgag_schema_migration(version) VALUES (%s)", (version,)
            )
        if "008_pgvector.sql" in MIGRATIONS:
            extension = conn.execute(VECTOR_QUERY).fetchone()
            if extension != (VECTOR_VERSION, "public"):
                raise RuntimeError("pgvector 0.8.6 in public is required")


def reindex_lexical(url: str) -> dict[str, str | int]:
    with psycopg.connect(url) as conn:
        conn.execute("SET LOCAL lock_timeout = '5s'")
        conn.execute("SELECT pg_advisory_xact_lock(742091830)")
        versions = conn.execute(
            "SELECT version FROM public.pgag_schema_migration ORDER BY version"
        ).fetchall()
        if versions != [(version,) for version in range(1, SCHEMA_VERSION + 1)]:
            raise RuntimeError("Database schema version mismatch; run matching migrations")
        return rebuild(conn)
