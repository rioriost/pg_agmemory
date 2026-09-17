import argparse
import asyncio
import json
import logging
import os
import secrets
from uuid import uuid4

import psycopg
import uvicorn

from pg_agmemory.database import migrate, reindex_lexical
from pg_agmemory.worker import run


def main() -> None:
    parser = argparse.ArgumentParser(prog="pg-agmemory")
    parser.add_argument(
        "command", choices=["serve", "migrate", "provision", "worker", "reindex-lexical", "mcp"]
    )
    parser.add_argument(
        "--subject", help="Trusted issuer subject for provisioning or fixed-principal worker"
    )
    parser.add_argument(
        "--once", action="store_true", help="Worker: process at most one due job and exit"
    )
    args = parser.parse_args()
    if args.once and args.command != "worker":
        parser.error("--once is only supported by worker")
    if args.command == "reindex-lexical" and args.subject is not None:
        parser.error("reindex-lexical rebuilds all tenants; --subject is not supported")
    if args.command == "mcp":
        if args.subject is not None:
            parser.error("mcp uses its fixed startup token; --subject is not supported")
        try:
            from pg_agmemory.mcp_adapter import main as mcp_main
        except ModuleNotFoundError as exc:
            if exc.name not in ("mcp", "httpx"):
                raise
            parser.error("mcp requires the pg-agmemory[mcp] extra")
        mcp_main()
    elif args.command == "migrate":
        migrate(os.environ["PGAG_ADMIN_DATABASE_URL"])
    elif args.command == "reindex-lexical":
        print(json.dumps(reindex_lexical(os.environ["PGAG_ADMIN_DATABASE_URL"])))
    elif args.command == "worker":
        if not args.subject or not 1 <= len(args.subject) <= 256:
            parser.error("worker requires --subject with 1 to 256 characters")
        logging.basicConfig(level=logging.INFO)
        asyncio.run(run(os.environ["PGAG_DATABASE_URL"], args.subject, once=args.once))
    elif args.command == "provision":
        if not args.subject or not 1 <= len(args.subject) <= 256:
            parser.error("provision requires --subject with 1 to 256 characters")
        tenant, principal, scope = uuid4(), uuid4(), uuid4()
        with psycopg.connect(os.environ["PGAG_ADMIN_DATABASE_URL"]) as conn:
            conn.execute(
                "INSERT INTO memory.tenant(id, dedup_secret) VALUES (%s, %s)",
                (tenant, secrets.token_bytes(32)),
            )
            conn.execute(
                "INSERT INTO memory.principal(tenant_id, id, external_subject) VALUES (%s, %s, %s)",
                (tenant, principal, args.subject),
            )
            conn.execute("INSERT INTO memory.scope(tenant_id, id) VALUES (%s, %s)", (tenant, scope))
            conn.execute(
                """INSERT INTO memory.scope_member
                   (tenant_id, scope_id, principal_id, permissions)
                   VALUES (%s, %s, %s, ARRAY['read','write','delete'])""",
                (tenant, scope, principal),
            )
        print(
            json.dumps(
                {"tenant_id": str(tenant), "principal_id": str(principal), "scope_id": str(scope)}
            )
        )
    else:
        uvicorn.run("pg_agmemory.api:create_app", factory=True, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
