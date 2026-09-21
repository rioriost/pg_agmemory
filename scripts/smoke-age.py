#!/usr/bin/env python3
"""Bounded, synthetic AGE qualification, never an application migration or adapter.

Only scripts/test-age-containers.sh should provision this dedicated fresh cluster.
A successful execution is not production approval: the report and exit status
distinguish working traversal from failed runtime-role isolation.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ARTIFACT = {
    "tag": "PG18/v1.8.0-rc0",
    "commit": "e43dc1a12b78fba4acef9835b2b10379b8d243b4",
    "archive_sha256": "555736a31974255223778959ca8bcd9cb710b93a8fab1d846eaf3e84704b9417",
    "archive_url": (
        "https://github.com/apache/age/releases/download/"
        "PG18/v1.8.0-rc0/apache-age-1.8.0-src.tar.gz"
    ),
    "base_image": (
        "docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@"
        "sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a"
    ),
}
GRAPH = "age_qualification"
DATABASE = "pgag_age_qualification"
VERTICES = (
    ("root", "alpha", True),
    ("good", "alpha", True),
    ("leaf", "alpha", True),
    ("hidden", "alpha", False),
    ("via_hidden", "alpha", True),
    ("edge_target", "alpha", True),
    ("foreign", "beta", True),
    ("via_foreign", "alpha", True),
    ("foreign_edge_target", "alpha", True),
)
EDGES = (
    ("root", "good", "good_edge", "alpha", True),
    ("good", "leaf", "leaf_edge", "alpha", True),
    ("root", "hidden", "into_hidden", "alpha", True),
    ("hidden", "via_hidden", "out_of_hidden", "alpha", True),
    ("root", "edge_target", "hidden_edge", "alpha", False),
    ("root", "foreign", "into_foreign", "alpha", True),
    ("foreign", "via_foreign", "out_of_foreign", "alpha", True),
    ("root", "foreign_edge_target", "foreign_edge", "beta", True),
)
LABELS = ("_ag_label_vertex", "_ag_label_edge", "Node", "LINK")
INPUTS = (
    "Dockerfile.age", "scripts/smoke-age.py", "scripts/test-age-containers.sh",
    "tests/test_age_profile.py",
)


def cypher_statement(query: str, *, parameters: bool = False) -> sql.Composed:
    """Only trusted static Cypher text; runtime values belong in the agtype map."""
    if "$pgag_age$" in query:
        raise ValueError("Cypher text cannot contain the SQL delimiter")
    suffix = sql.SQL(", %s::ag_catalog.agtype") if parameters else sql.SQL("")
    # AGE's parser requires a dollar-quoted constant, not a regular SQL literal.
    literal = sql.SQL("$pgag_age$" + query + "$pgag_age$")
    return sql.SQL(
        "SELECT value::jsonb::text FROM ag_catalog.cypher({}, {}{}) AS (value ag_catalog.agtype)"
    ).format(sql.Literal(GRAPH), literal, suffix)


def traversal(*, parameters: bool = False, hops: int = 2) -> str:
    if type(hops) is not int or hops not in (1, 2):
        raise ValueError("qualification permits only one or two hops")
    root = "$root" if parameters else "'root'"
    return (
        f"MATCH (s:Node {{key: {root}}})-[:LINK*1..{hops}]->(n:Node) "
        "RETURN DISTINCT n.key ORDER BY n.key LIMIT 32"
    )


def fixed_one_hop(*, parameters: bool = True, direction: str = "out") -> str:
    patterns = {
        "out": "-[e:LINK]->",
        "in": "<-[e:LINK]-",
        "both": "-[e:LINK]-",
    }
    if direction not in patterns:
        raise ValueError("qualification permits only out, in, or both")
    root = "$root" if parameters else "'root'"
    return (
        f"MATCH (s:Node {{key: {root}}}){patterns[direction]}(n:Node) "
        "RETURN [s.key, e.key, n.key] ORDER BY e.key, n.key LIMIT 32"
    )


def visible_policy(admin: psycopg.Connection[Any], label: str) -> None:
    admin.execute(sql.SQL(
        "CREATE POLICY visible_rows ON {} FOR SELECT TO pgag_runtime USING "
        "((properties::jsonb ->> 'tenant') = "
        "current_setting('pgag.age_tenant', true) "
        "AND properties::jsonb @> '{{\"visible\": true}}'::jsonb)"
    ).format(sql.Identifier(GRAPH, label)))


def record(
    connection: psycopg.Connection[Any],
    checks: list[dict[str, Any]],
    name: str,
    query: sql.Composable | str,
    expected: list[Any],
    *,
    parameters: list[str] | None = None,
    agtype: bool = False,
) -> None:
    try:
        rows = connection.execute(query, parameters, prepare=parameters is not None).fetchall()
        actual = sorted(json.loads(row[0]) if agtype else row[0] for row in rows)
        checks.append({
            "name": name, "expected": sorted(expected), "actual": actual,
            "passed": actual == sorted(expected),
        })
    except psycopg.Error as error:
        checks.append({
            "name": name, "expected": sorted(expected), "passed": False,
            "sqlstate": error.sqlstate,
            "error": error.diag.message_primary,
        })


def fixed_host_bfs(connection: psycopg.Connection[Any]) -> list[str]:
    """Synthetic host expansion only; not the production BFS/authorization adapter."""
    statement = cypher_statement(fixed_one_hop(), parameters=True)
    frontier = ["root"]
    visited = {"root"}
    found = []
    for _ in range(2):
        next_frontier = []
        for root in frontier:
            rows = connection.execute(
                statement, [json.dumps({"root": root})], prepare=True,
            ).fetchall()
            for row in rows:
                source, _edge, target = json.loads(row[0])
                if source != root:
                    raise RuntimeError("fixed query returned a different source")
                if target not in visited:
                    visited.add(target)
                    found.append(target)
                    next_frontier.append(target)
                    if len(found) == 32:
                        return found
        frontier = next_frontier
    return found


def fixed_template_probe(
    admin: psycopg.Connection[Any], runtime: psycopg.Connection[Any],
) -> dict[str, Any]:
    """Run after the original VLE deny-all test, preserving that failed evidence."""
    checks: list[dict[str, Any]] = []
    for label in ("LINK", "_ag_label_edge"):
        visible_policy(admin, label)
    for label in LABELS:
        admin.execute(sql.SQL(
            "CREATE POLICY actor_gate ON {} AS RESTRICTIVE FOR SELECT TO pgag_runtime "
            "USING (current_setting('pgag.age_actor', true) = 'reader')"
        ).format(sql.Identifier(GRAPH, label)))
    runtime.execute("SET pgag.age_actor = 'reader'")
    runtime.execute("SET pgag.age_tenant = 'alpha'")
    literal = cypher_statement(fixed_one_hop(parameters=False))
    prepared = cypher_statement(fixed_one_hop(), parameters=True)
    root_map = [json.dumps({"root": "root"})]
    allowed = [["root", "good_edge", "good"]]
    record(runtime, checks, "literal_outgoing", literal, allowed, agtype=True)
    for name, root, expected in (
        ("prepared_outgoing", "root", allowed),
        ("prepared_second_frontier", "good", [["good", "leaf_edge", "leaf"]]),
        ("prepared_hidden_seed", "hidden", []),
        ("prepared_foreign_seed", "foreign", []),
        ("prepared_injection_value", "root'}) MATCH (n) RETURN n //", []),
        ("prepared_repeated_root", "root", allowed),
    ):
        record(runtime, checks, name, prepared, expected,
               parameters=[json.dumps({"root": root})], agtype=True)
    for direction, root, expected in (
        ("in", "good", [["good", "good_edge", "root"]]),
        ("in", "via_hidden", []),
        ("in", "via_foreign", []),
        ("in", "edge_target", []),
        ("in", "foreign_edge_target", []),
        ("both", "good", [["good", "good_edge", "root"], ["good", "leaf_edge", "leaf"]]),
        ("both", "root", allowed),
        ("both", "via_hidden", []),
        ("both", "edge_target", []),
    ):
        record(
            runtime, checks, f"prepared_{direction}_{root}",
            cypher_statement(fixed_one_hop(direction=direction), parameters=True), expected,
            parameters=[json.dumps({"root": root})], agtype=True,
        )
    try:
        reached = fixed_host_bfs(runtime)
        checks.append({
            "name": "host_bfs_two_hops", "expected": ["good", "leaf"], "actual": reached,
            "passed": reached == ["good", "leaf"],
        })
    except psycopg.Error as error:
        checks.append({
            "name": "host_bfs_two_hops", "passed": False,
            "sqlstate": error.sqlstate, "error": error.diag.message_primary,
        })
    for name, setting, value, expected in (
        ("tenant_beta", "pgag.age_tenant", "beta", []),
        ("tenant_alpha_restored", "pgag.age_tenant", "alpha", allowed),
        ("actor_denied", "pgag.age_actor", "denied", []),
        ("actor_reader_restored", "pgag.age_actor", "reader", allowed),
    ):
        runtime.execute("SELECT set_config(%s, %s, false)", (setting, value))
        record(runtime, checks, f"reused_connection_literal_{name}",
               literal, expected, agtype=True)
        record(runtime, checks, f"reused_connection_prepared_{name}",
               prepared, expected, parameters=root_map, agtype=True)
    for name, labels in (
        ("deny_node_label", ("Node",)),
        ("deny_edge_label", ("LINK",)),
        ("deny_all_labels", LABELS),
    ):
        for label in labels:
            admin.execute(sql.SQL("DROP POLICY visible_rows ON {}").format(
                sql.Identifier(GRAPH, label),
            ))
            record(runtime, checks, f"{name}_direct_{label}", sql.SQL(
                "SELECT properties::jsonb ->> 'key' FROM {} ORDER BY 1 LIMIT 32"
            ).format(sql.Identifier(GRAPH, label)), [])
        record(runtime, checks, f"{name}_literal", literal, [], agtype=True)
        record(runtime, checks, f"{name}_prepared",
               prepared, [], parameters=root_map, agtype=True)
        for label in labels:
            visible_policy(admin, label)
        record(runtime, checks, f"{name}_prepared_restored",
               prepared, allowed, parameters=root_map, agtype=True)
    return {
        "candidate_only": True,
        "adapter_qualified": False,
        "checks_passed": bool(checks) and all(check["passed"] for check in checks),
        "checks": checks,
        "failed_checks": [check["name"] for check in checks if not check["passed"]],
        "templates": {
            "literal_out": fixed_one_hop(parameters=False),
            "prepared_out": fixed_one_hop(),
            "prepared_in": fixed_one_hop(direction="in"),
            "prepared_both": fixed_one_hop(direction="both"),
        },
        "constraints": [
            "Same non-owner runtime connection and server-prepared statements reused.",
            "Tenant and actor are synthetic session policy contexts, not PostgreSQL role changes.",
            "Actor policy is a restrictive current_setting gate, not canonical membership checks.",
            "Direct labels Node and LINK; never substitute native variable-length syntax.",
            "Returned fixture keys are not yet canonical object/edge IDs.",
            "Generation, time validity, canonical ACL joins, and revocation races remain untested.",
            "Fixed templates plus host BFS are a candidate for implementation, not activation.",
        ],
    }


def setup(admin: psycopg.Connection[Any], password: str) -> dict[str, Any]:
    identity = admin.execute(
        "SELECT current_database(), current_setting('server_version_num')::int, "
        "current_setting('shared_preload_libraries'), version()"
    ).fetchone()
    assert identity is not None
    if identity[0] != DATABASE or identity[1] != 180006 or "age" not in identity[2].split(","):
        raise RuntimeError("requires dedicated PG18.6 qualification database and AGE preload")
    if admin.execute(
        "SELECT EXISTS (SELECT FROM pg_roles WHERE rolname IN "
        "('pgag_runtime', 'pgag_age_reader')) OR EXISTS "
        "(SELECT FROM pg_extension WHERE extname != 'plpgsql') OR EXISTS "
        "(SELECT FROM pg_namespace WHERE nspname NOT IN "
        "('public', 'pg_catalog', 'information_schema', 'pg_toast') "
        "AND nspname NOT LIKE 'pg_temp_%' AND nspname NOT LIKE 'pg_toast_temp_%')"
    ).fetchone() != (False,):
        raise RuntimeError("refusing non-fresh cluster/database")
    admin.execute("CREATE EXTENSION vector VERSION '0.8.6'")
    admin.execute("CREATE EXTENSION age VERSION '1.8.0'")
    admin.execute("SET search_path = ag_catalog, pg_catalog")
    admin.execute("SELECT ag_catalog.create_graph(%s)", (GRAPH,))
    admin.execute(
        "CREATE ROLE pgag_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS "
        "NOCREATEDB NOCREATEROLE NOREPLICATION"
    )
    admin.execute(sql.SQL(
        "CREATE ROLE pgag_age_reader LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS "
        "NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}"
    ).format(sql.Literal(password)))
    admin.execute("GRANT pgag_runtime TO pgag_age_reader")
    for key, tenant, visible in VERTICES:
        properties = (
            f"key: {json.dumps(key)}, tenant: {json.dumps(tenant)}, "
            f"visible: {json.dumps(visible)}"
        )
        admin.execute(cypher_statement(f"CREATE (:Node {{{properties}}})"))
    for source, target, key, tenant, visible in EDGES:
        admin.execute(cypher_statement(
            f"MATCH (a:Node {{key: {json.dumps(source)}}}), "
            f"(b:Node {{key: {json.dumps(target)}}}) "
            f"CREATE (a)-[:LINK {{key: {json.dumps(key)}, tenant: {json.dumps(tenant)}, "
            f"visible: {json.dumps(visible)}}}]->(b)"
        ))
    admin.execute(sql.SQL(
        "GRANT USAGE ON SCHEMA ag_catalog, {} TO pgag_runtime"
    ).format(sql.Identifier(GRAPH)))
    admin.execute(
        "GRANT SELECT ON ag_catalog.ag_graph, ag_catalog.ag_label TO pgag_runtime"
    )
    for label in LABELS:
        relation = sql.Identifier(GRAPH, label)
        admin.execute(sql.SQL("GRANT SELECT ON {} TO pgag_runtime").format(relation))
        admin.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(relation))
        admin.execute(sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(relation))
        visible_policy(admin, label)
    return {
        "postgres_version_num": identity[1],
        "postgres_version": identity[3],
        "shared_preload_libraries": identity[2],
        "extensions": dict(admin.execute(
            "SELECT extname, extversion FROM pg_extension WHERE extname IN ('age', 'vector')"
        ).fetchall()),
    }


def probe(dsn: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "format": "pgag-age-qualification-v2", "artifact": ARTIFACT,
        "production_enabled": False, "qualified": False, "checks": checks,
        "bounds": {"max_hops": 2, "max_results": 32, "statement_timeout_ms": 5000},
        "constraints": [
            "Dedicated disposable cluster/database; not the application schema.",
            "Pinned upstream tag literally includes rc0; no stable-release claim.",
            "AGE preloaded by cluster startup; runtime does not LOAD or own extension/tables.",
            "Synthetic RLS property policy is a probe, not pg_agmemory authorization.",
            "Failure forbids native AGE traversal promotion; SQL remains the default.",
        ],
        "files_sha256": {
            name: hashlib.sha256(
                (Path(__file__).resolve().parents[1] / name).read_bytes(),
            ).hexdigest()
            for name in INPUTS
        },
    }
    password = secrets.token_hex(24)
    with psycopg.connect(dsn, autocommit=True) as admin:
        report["runtime"] = setup(admin, password)
        runtime_dsn = conninfo_to_dict(dsn)
        runtime_dsn.update(user="pgag_age_reader", password=password)
        with psycopg.connect(make_conninfo(**runtime_dsn), autocommit=True) as runtime:
            runtime.execute("SET ROLE pgag_runtime")
            runtime.execute("SET search_path = ag_catalog, pg_catalog")
            runtime.execute("SET statement_timeout = '5s'")
            runtime.execute("SET lock_timeout = '2s'")
            runtime.execute("SET pgag.age_tenant = 'alpha'")
            role = runtime.execute(
                "SELECT session_user, current_user, rolsuper, rolbypassrls, "
                "rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = current_user"
            ).fetchone()
            report["runtime"]["role"] = {
                "session_user": role[0], "current_user": role[1],
                "superuser": role[2], "bypassrls": role[3],
                "createrole": role[4], "createdb": role[5],
            }
            if role != ("pgag_age_reader", "pgag_runtime", False, False, False, False):
                raise RuntimeError("runtime identity is not restricted")
            policies = runtime.execute(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                "pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n "
                "ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind = 'r' "
                "ORDER BY c.relname", (GRAPH,),
            ).fetchall()
            report["runtime"]["relations"] = [
                {"name": name, "rls": rls, "forced_rls": forced, "owner": owner}
                for name, rls, forced, owner in policies
            ]
            if len(policies) != 4 or any(
                not rls or not forced or owner in ("pgag_runtime", "pgag_age_reader")
                for _, rls, forced, owner in policies
            ):
                raise RuntimeError("base and child labels must force RLS with external ownership")
            visible_nodes = [
                key for key, tenant, visible in VERTICES if tenant == "alpha" and visible
            ]
            visible_edges = [
                key for _, _, key, tenant, visible in EDGES if tenant == "alpha" and visible
            ]
            for label, expected in (
                ("Node", visible_nodes), ("_ag_label_vertex", visible_nodes),
                ("LINK", visible_edges), ("_ag_label_edge", visible_edges),
            ):
                record(runtime, checks, f"direct_{label}", sql.SQL(
                    "SELECT properties::jsonb ->> 'key' FROM {} ORDER BY 1 LIMIT 32"
                ).format(sql.Identifier(GRAPH, label)), expected)
            for name, query, expected in (
                ("labeled_vertices", "MATCH (n:Node) RETURN n.key ORDER BY n.key LIMIT 32",
                 visible_nodes),
                ("unlabeled_vertices", "MATCH (n) RETURN n.key ORDER BY n.key LIMIT 32",
                 visible_nodes),
                ("fixed_one_hop", "MATCH (s:Node {key: 'root'})-[:LINK]->(n:Node) "
                 "RETURN n.key ORDER BY n.key LIMIT 32", ["good"]),
                ("fixed_two_hops", "MATCH (s:Node {key: 'root'})-[:LINK]->(m:Node)"
                 "-[:LINK]->(n:Node) RETURN n.key ORDER BY n.key LIMIT 32", ["leaf"]),
                ("bounded_literal_one_hop", traversal(hops=1), ["good"]),
                ("bounded_literal_two_hops", traversal(), ["good", "leaf"]),
            ):
                record(runtime, checks, name, cypher_statement(query), expected, agtype=True)
            statement = cypher_statement(traversal(parameters=True), parameters=True)
            for name, root, expected in (
                ("bounded_parameterized", "root", ["good", "leaf"]),
                ("prepared_second_root", "good", ["leaf"]),
                ("parameter_not_cypher", "root'}) MATCH (n) RETURN n //", []),
                ("prepared_repeated_root", "root", ["good", "leaf"]),
            ):
                record(runtime, checks, name, statement, expected,
                       parameters=[json.dumps({"root": root})], agtype=True)
            runtime.execute("SET pgag.age_tenant = 'beta'")
            record(runtime, checks, "changed_tenant_direct", sql.SQL(
                "SELECT properties::jsonb ->> 'key' FROM {}"
            ).format(sql.Identifier(GRAPH, "Node")), ["foreign"])
            record(runtime, checks, "changed_tenant_prepared", statement, [],
                   parameters=[json.dumps({"root": "root"})], agtype=True)
            runtime.execute("SET pgag.age_tenant = 'alpha'")
            for label in ("LINK", "_ag_label_edge"):
                admin.execute(sql.SQL("DROP POLICY visible_rows ON {}").format(
                    sql.Identifier(GRAPH, label),
                ))
            record(runtime, checks, "deny_all_direct_edges", sql.SQL(
                "SELECT properties::jsonb ->> 'key' FROM {}"
            ).format(sql.Identifier(GRAPH, "LINK")), [])
            record(runtime, checks, "deny_all_literal_traversal",
                   cypher_statement(traversal()), [], agtype=True)
            record(runtime, checks, "deny_all_prepared_traversal", statement, [],
                   parameters=[json.dumps({"root": "root"})], agtype=True)
            report["fixed_template_candidate"] = fixed_template_probe(admin, runtime)
    report["native_vle_qualified"] = bool(checks) and all(check["passed"] for check in checks)
    report["qualified"] = (
        report["native_vle_qualified"] and report["fixed_template_candidate"]["checks_passed"]
    )
    report["failed_checks"] = [check["name"] for check in checks if not check["passed"]]
    return report


def main() -> int:
    dsn = os.environ.get("PGAG_AGE_ADMIN_DATABASE_URL")
    if not dsn or os.environ.get("PGAG_AGE_DISPOSABLE") != "1":
        print(json.dumps({"qualified": False, "error": "explicit disposable AGE profile required"}))
        return 2
    try:
        report = probe(dsn)
    except (OSError, ValueError, RuntimeError, psycopg.Error) as error:
        # Connection diagnostics can contain credentials/DSNs; never print str(error).
        print(json.dumps({
            "qualified": False, "phase": "setup_or_probe",
            "error_type": type(error).__name__,
            "sqlstate": getattr(error, "sqlstate", None),
        }))
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
