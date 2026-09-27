"""Provision read/write-only identities in the coordinator's fresh owned database."""

import argparse
import ctypes
import hashlib
import json
import os
import re
import secrets
import stat
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql

from pg_agmemory.database import migrate
from pg_agmemory.transactions import CommitOutcomeUnknown, transaction


def publish_reply(staging: str, destination: str) -> dict[str, object]:
    source, target = Path(staging), Path(destination)
    if (
        source.parent.name != "staging" or target.parent.name != "replies"
        or target.parent.parent.name != "ipc"
        or source.parent.parent != target.parent.parent.parent
        or re.fullmatch(r"[0-9]{6}\.json", target.name) is None
        or source.name != target.name
    ):
        raise ValueError("invalid_reply_paths")
    for parent in (*source.parents, *target.parents):
        if parent.is_symlink():
            raise ValueError("reply_path_symlink")
    info = source.lstat()
    if (
        not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
        or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 70000
    ):
        raise ValueError("invalid_staged_reply")
    library = ctypes.CDLL(None, use_errno=True)
    rename = library.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))
    descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return {"status": "published"}


def provision(run_id: str, projects: list[str]) -> dict[str, object]:
    if (
        re.fullmatch(r"pgag-dev-[0-9a-f]{24}", run_id) is None
        or os.environ.get("PGAG_DEVELOPMENT_OWNED_RUN") != run_id
        or len(projects) != 2 or len(set(projects)) != 2
        or any(re.fullmatch(r"[a-z][a-z0-9-]{0,63}", project) is None for project in projects)
    ):
        raise ValueError("invalid_owned_provisioning")
    agent = distribution("mini-swe-agent")
    agent_hash = hashlib.sha256(
        agent.locate_file("minisweagent/agents/default.py").read_bytes(),
    ).hexdigest()
    if (
        agent.version != "2.4.6"
        or agent_hash != "e8ef8aa365942d739c2ec5cb0879f60f377d2dc2de8ec670aaedf3bafb45a4c2"
    ):
        raise ValueError("default_agent_identity_mismatch")
    password = os.environ["PGAG_DEVELOPMENT_RUNTIME_PASSWORD"]
    if re.fullmatch(r"[0-9a-f]{64}", password) is None:
        raise ValueError("invalid_runtime_credential")
    database = os.environ["PGAG_ADMIN_DATABASE_URL"]
    migrate(database)
    bindings: list[dict[str, str]] = []
    with psycopg.connect(database, autocommit=True) as connection, transaction(connection):
        connection.execute(
            sql.SQL(
                "CREATE ROLE pgag_development_runtime LOGIN NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD {} IN ROLE pgag_runtime"
            ).format(sql.Literal(password)),
        )
        for project in projects:
            for arm in ("no_memory", "handoff", "pg_agmemory"):
                tenant, principal, scope = uuid4(), uuid4(), uuid4()
                subject = f"{run_id}:{project}:{arm}"
                connection.execute(
                    "INSERT INTO memory.tenant(id, dedup_secret) VALUES (%s, %s)",
                    (tenant, secrets.token_bytes(32)),
                )
                connection.execute(
                    "INSERT INTO memory.principal(tenant_id,id,external_subject) VALUES (%s,%s,%s)",
                    (tenant, principal, subject),
                )
                connection.execute(
                    "INSERT INTO memory.scope(tenant_id,id) VALUES (%s,%s)", (tenant, scope),
                )
                connection.execute(
                    "INSERT INTO memory.scope_member(tenant_id,scope_id,principal_id,permissions) "
                    "VALUES (%s,%s,%s,ARRAY['read','write'])", (tenant, scope, principal),
                )
                bindings.append({
                    "run_id": run_id, "project_id": project, "arm": arm,
                    "tenant_id": str(tenant), "principal_id": str(principal),
                    "scope_id": str(scope), "subject": subject,
                })
    return {
        "format": "pgag-development-native-v1", "run_id": run_id,
        "permissions": ["read", "write"], "bindings": bindings,
        "mini_swe_agent": {"version": agent.version, "default_agent_sha256": agent_hash},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id")
    parser.add_argument("--project", action="append")
    parser.add_argument("--publish-reply", nargs=2)
    args = parser.parse_args()
    if args.publish_reply is None and (args.run_id is None or args.project is None):
        parser.error("--run-id and --project are required for provisioning")
    try:
        if args.publish_reply is not None:
            if args.run_id is not None or args.project is not None:
                raise ValueError("mixed_native_helper_operations")
            result = publish_reply(*args.publish_reply)
        else:
            result = provision(args.run_id, args.project)
    except CommitOutcomeUnknown:
        print(json.dumps({"status": "error", "code": "commit_outcome_unknown",
                          "outcome_unknown": True}), flush=True)
        raise SystemExit(1) from None
    except (ValueError, KeyError, OSError, PackageNotFoundError, psycopg.Error) as error:
        print(json.dumps({
            "status": "error", "code": type(error).__name__,
            "sqlstate": error.sqlstate if isinstance(error, psycopg.Error) else None,
        }), flush=True)
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
