import argparse
import json
import os
import secrets
from uuid import uuid4

import psycopg
import uvicorn

from pg_agmemory.database import migrate


def main() -> None:
    parser = argparse.ArgumentParser(prog="pg-agmemory")
    parser.add_argument("command", choices=["serve", "migrate", "provision"])
    parser.add_argument("--subject", help="Verified issuer subject to bind to a new private tenant")
    args = parser.parse_args()
    if args.command == "migrate":
        migrate(os.environ["PGAG_ADMIN_DATABASE_URL"])
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
