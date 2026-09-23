import argparse
import asyncio
import json
import logging
import os
import secrets
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import uvicorn

from pg_agmemory.database import migrate, reindex_lexical
from pg_agmemory.worker import run


def main() -> None:
    if sys.argv[1:2] == ["infer"]:
        try:
            from pg_agmemory.inference import main as inference_main
        except ImportError as exc:
            if str(exc) != "Inference providers require the pg-agmemory[providers] extra":
                raise
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from None
        inference_main(sys.argv[2:])
        return
    if sys.argv[1:2] == ["scope-access"]:
        from pg_agmemory.scope_access import main as access_main

        access_main(sys.argv[2:])
        return
    if sys.argv[1:2] == ["source-access"]:
        from pg_agmemory.source_access import main as source_access_main

        source_access_main(sys.argv[2:])
        return
    if sys.argv[1:2] == ["source-dataset"]:
        from pg_agmemory.source_dataset import main as source_dataset_main

        source_dataset_main(sys.argv[2:])
        return
    if sys.argv[1:2] == ["scope-capture"]:
        from pg_agmemory.capture_policy import main as capture_main

        capture_main(sys.argv[2:])
        return
    if sys.argv[1:2] == ["scope-synthesis"]:
        from pg_agmemory.synthesis_policy import main as synthesis_main

        synthesis_main(sys.argv[2:])
        return
    if sys.argv[1:2] == ["deletion-history"]:
        from pg_agmemory.deletion_history import main as history_main

        history_main(sys.argv[2:])
        return
    if sys.argv[1:2] == ["processing-recovery"]:
        from pg_agmemory.processing_recovery import main as recovery_main

        recovery_main(sys.argv[2:])
        return
    if sys.argv[1:2] == ["recovery-apply"]:
        from pg_agmemory.recovery_apply import main as apply_main

        apply_main(sys.argv[2:])
        return
    if sys.argv[1:2] == ["graph-generation"]:
        from pg_agmemory.graph_generation import main as graph_generation_main

        graph_generation_main(sys.argv[2:])
        return
    if sys.argv[1:2] == ["graph-artifact"]:
        from pg_agmemory.graph_artifact import main as graph_artifact_main

        graph_artifact_main(sys.argv[2:])
        return
    if sys.argv[1:2] == ["age-projection"]:
        from pg_agmemory.age_projection import main as age_projection_main

        age_projection_main(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(prog="pg-agmemory")
    parser.add_argument(
        "command",
        choices=[
            "serve",
            "migrate",
            "provision",
            "worker",
            "reindex-lexical",
            "mcp",
            "recall-hook",
            "scope-access",
            "source-access",
            "source-dataset",
            "scope-capture",
            "scope-synthesis",
            "deletion-history",
            "processing-recovery",
            "recovery-apply",
            "graph-generation",
            "graph-artifact",
            "age-projection",
            "infer",
        ],
    )
    parser.add_argument(
        "--subject", help="Trusted issuer subject for provisioning or fixed-principal worker"
    )
    parser.add_argument(
        "--once", action="store_true", help="Worker: process at most one due job and exit"
    )
    parser.add_argument("--provider-config", type=Path, help="Worker: trusted local profile JSON")
    parser.add_argument(
        "--print-profile-digest", action="store_true",
        help="Worker: validate local profile and print policy digest without making model calls",
    )
    args = parser.parse_args()
    if args.once and args.command != "worker":
        parser.error("--once is only supported by worker")
    if (args.provider_config or args.print_profile_digest) and args.command != "worker":
        parser.error("provider configuration is only supported by worker")
    if args.command == "reindex-lexical" and args.subject is not None:
        parser.error("reindex-lexical rebuilds all tenants; --subject is not supported")
    if args.command == "scope-access":
        parser.error("scope-access must precede its arguments; use scope-access --help")
    elif args.command == "source-access":
        parser.error("source-access must precede its arguments; use source-access --help")
    elif args.command == "source-dataset":
        parser.error("source-dataset must precede its arguments; use source-dataset --help")
    elif args.command == "scope-capture":
        parser.error("scope-capture must precede its arguments; use scope-capture --help")
    elif args.command == "scope-synthesis":
        parser.error("scope-synthesis must precede its arguments; use scope-synthesis --help")
    elif args.command == "deletion-history":
        parser.error("deletion-history must precede its arguments; use deletion-history --help")
    elif args.command == "processing-recovery":
        parser.error(
            "processing-recovery must precede its arguments; use processing-recovery --help"
        )
    elif args.command == "recovery-apply":
        parser.error("recovery-apply must precede its arguments; use recovery-apply --help")
    elif args.command == "graph-generation":
        parser.error("graph-generation must precede its arguments; use graph-generation --help")
    elif args.command == "graph-artifact":
        parser.error("graph-artifact must precede its arguments; use graph-artifact --help")
    elif args.command == "age-projection":
        parser.error("age-projection must precede its arguments; use age-projection --help")
    elif args.command == "infer":
        parser.error("infer must precede its arguments; use infer --help")
    elif args.command == "recall-hook":
        if args.subject is not None:
            parser.error("recall-hook uses its fixed startup token; --subject is not supported")
        try:
            from pg_agmemory.recall_hook import main as hook_main
        except ModuleNotFoundError as exc:
            if exc.name != "httpx":
                raise
            parser.error("recall-hook requires the pg-agmemory[hook] extra")
        hook_main()
    elif args.command == "mcp":
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
        profile = None
        if args.provider_config is not None:
            try:
                from pg_agmemory.providers import ProviderFailure
                from pg_agmemory.worker_profile import WorkerProfile
            except ImportError as exc:
                if str(exc) != "Inference providers require the pg-agmemory[providers] extra":
                    raise
                parser.error("model worker requires pg-agmemory[providers]")
            try:
                with args.provider_config.open("rb") as stream:
                    profile = WorkerProfile.parse(stream.read(32769))
            except (OSError, ValueError, ProviderFailure):
                parser.error("invalid_worker_profile")
        if args.print_profile_digest:
            if profile is None:
                parser.error("--print-profile-digest requires --provider-config")
            print(json.dumps({"profile_digest": profile.digest}), flush=True)
            return
        if not args.subject or not 1 <= len(args.subject) <= 256:
            parser.error("worker requires --subject with 1 to 256 characters")
        logging.basicConfig(level=logging.INFO)
        asyncio.run(run(
            os.environ["PGAG_DATABASE_URL"], args.subject, once=args.once, profile=profile
        ))
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
