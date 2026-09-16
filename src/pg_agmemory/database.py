import os
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

import psycopg
from psycopg.rows import dict_row

Connection = psycopg.AsyncConnection[dict[str, Any]]


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
        await conn.execute("SELECT 1 FROM memory.tenant LIMIT 0")


def migrate(url: str) -> None:
    with psycopg.connect(url) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(742091830)")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS public.pgag_schema_migration
               (version integer PRIMARY KEY, applied_at timestamptz DEFAULT clock_timestamp())"""
        )
        row = conn.execute(
            "SELECT version FROM public.pgag_schema_migration WHERE version = 1"
        ).fetchone()
        if row is None:
            sql = files("pg_agmemory").joinpath("storage/001_initial.sql").read_text()
            conn.execute(sql)
            conn.execute("INSERT INTO public.pgag_schema_migration(version) VALUES (1)")
