from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Annotated, Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from pydantic import Field

from pg_agmemory.database import SCHEMA_VERSION, VECTOR_QUERY, VECTOR_VERSION, Connection, connect
from pg_agmemory.transactions import CommitDeadlineSetupError, CommitOutcomeUnknown

MAX_EPOCH = 9223372036854775807
Epoch = Annotated[int, Field(ge=1, le=MAX_EPOCH, strict=True)]


class AdminError(Exception):
    def __init__(self, code: str, *, outcome_unknown: bool = False) -> None:
        self.code = code
        self.outcome_unknown = outcome_unknown
        super().__init__(code)


def _validate_admin_connection(conn: psycopg.Connection[dict[str, Any]]) -> None:
    role = conn.execute(
        "SELECT rolsuper OR rolbypassrls AS allowed FROM pg_roles WHERE rolname=current_user"
    ).fetchone()
    if not role or not role["allowed"]:
        raise AdminError("admin_role_required")
    versions = conn.execute(
        "SELECT version FROM public.pgag_schema_migration ORDER BY version"
    ).fetchall()
    if [row["version"] for row in versions] != list(range(1, SCHEMA_VERSION + 1)):
        raise AdminError("schema_version_mismatch")
    if conn.execute(VECTOR_QUERY).fetchone() != {
        "extversion": VECTOR_VERSION,
        "nspname": "public",
    }:
        raise AdminError("extension_version_mismatch")


@contextmanager
def admin_connection(url: str, tenant_id: UUID) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    with psycopg.connect(
        url,
        autocommit=True,
        row_factory=dict_row,
        connect_timeout=5,
        options="-c statement_timeout=5000 -c lock_timeout=5000",
    ) as conn:
        _validate_admin_connection(conn)
        # Keep the API/worker barrier through commit and administrative output delivery.
        conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (str(tenant_id),))
        yield conn


@contextmanager
def read_admin_snapshot(url: str) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    """Yield an ADMIN metadata snapshot without taking the tenant admission barrier."""
    with psycopg.connect(
        url,
        autocommit=True,
        row_factory=dict_row,
        connect_timeout=5,
        options=(
            "-c statement_timeout=5000 -c lock_timeout=5000 "
            "-c default_transaction_read_only=on"
        ),
    ) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            conn.execute("SET LOCAL timezone='UTC'")
            _validate_admin_connection(conn)
            yield conn


@asynccontextmanager
async def async_admin_connection(url: str, tenant_id: UUID) -> AsyncIterator[Connection]:
    async with await connect(url) as conn:
        role = await (
            await conn.execute(
                "SELECT rolsuper OR rolbypassrls AS allowed "
                "FROM pg_roles WHERE rolname=current_user"
            )
        ).fetchone()
        if not role or not role["allowed"]:
            raise AdminError("admin_role_required")
        versions = await (
            await conn.execute(
                "SELECT version FROM public.pgag_schema_migration ORDER BY version"
            )
        ).fetchall()
        if [row["version"] for row in versions] != list(range(1, SCHEMA_VERSION + 1)):
            raise AdminError("schema_version_mismatch")
        if await (await conn.execute(VECTOR_QUERY)).fetchone() != {
            "extversion": VECTOR_VERSION,
            "nspname": "public",
        }:
            raise AdminError("extension_version_mismatch")
        # A session lock survives commit and administrative output delivery.
        await conn.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s, 0))", (str(tenant_id),)
        )
        yield conn


def admin_failure(
    exc: psycopg.Error | CommitOutcomeUnknown, commit_attempted: bool,
) -> AdminError:
    if isinstance(exc, CommitOutcomeUnknown):
        return AdminError("commit_outcome_unknown", outcome_unknown=True)
    if isinstance(exc, CommitDeadlineSetupError):
        return AdminError("admin_database_unavailable", outcome_unknown=False)
    if isinstance(exc, (psycopg.errors.UndefinedTable, psycopg.errors.InvalidSchemaName)):
        code = "schema_unavailable"
    elif isinstance(exc, psycopg.errors.InsufficientPrivilege):
        code = "admin_privilege_required"
    elif isinstance(exc, (psycopg.OperationalError, psycopg.errors.QueryCanceled)):
        code = "admin_database_unavailable"
    else:
        code = "admin_database_error"
    return AdminError(code, outcome_unknown=commit_attempted)
