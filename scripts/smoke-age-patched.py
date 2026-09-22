#!/usr/bin/env python3
"""Run the unchanged original AGE checks against the pinned patched source."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = "/usr/share/postgresql/18/extension/pgag_age_build.json"
ARTIFACT = json.loads((ROOT / "patches/age/source.json").read_text())
INPUTS = (
    "Dockerfile.age-patched", "patches/age/source.json", ARTIFACT["patch_file"],
    ARTIFACT["distribution_stamp_file"], ARTIFACT["preload_diagnostic_file"],
    "scripts/smoke-age.py", "scripts/smoke-age-patched.py",
    "scripts/test-age-patched-containers.sh", "tests/test_age_patched_profile.py",
)
spec = importlib.util.spec_from_file_location("original_age_probe", ROOT / "scripts/smoke-age.py")
assert spec is not None and spec.loader is not None
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)
original.ARTIFACT = ARTIFACT
original.INPUTS = INPUTS
ORIGINAL_SETUP = original.setup
SQL_IDENTITY = {
    field: ARTIFACT[field] for field in ("commit", "upstream_base_commit", "patch_sha256")
}
PRELOAD_METADATA = {
    "security_definer": True,
    "volatility": "s",
    "config": ["search_path=pg_catalog"],
    "arguments": 0,
    "returns_boolean": True,
    "language": "sql",
    "superuser_owner": True,
    "extension_owned": True,
    "runtime_owner_member": False,
    "login_owner_member": False,
    "safe_acl": True,
    "runtime_settings_reader": False,
    "login_settings_reader": False,
    "runtime_schema_create": False,
}


def sql_build_stamp(dsn: str, password: str) -> dict[str, Any]:
    runtime_dsn = conninfo_to_dict(dsn)
    runtime_dsn.update(user="pgag_age_reader", password=password)
    with psycopg.connect(make_conninfo(**runtime_dsn), autocommit=True) as runtime:
        runtime.execute("SET ROLE pgag_runtime")
        row = runtime.execute(
            "SELECT ag_catalog.pgag_age_build(), p.prosecdef, p.provolatile, l.lanname "
            "FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_language l ON l.oid = p.prolang "
            "WHERE p.oid = 'ag_catalog.pgag_age_build()'::pg_catalog.regprocedure"
        ).fetchone()
    if row != (SQL_IDENTITY, False, "i", "sql"):
        raise RuntimeError("runtime-readable SQL build stamp does not match pinned identity")
    return row[0]


def setup(admin: psycopg.Connection[Any], password: str) -> dict[str, Any]:
    runtime = ORIGINAL_SETUP(admin, password)
    runtime["sql_build_stamp"] = sql_build_stamp(admin.info.dsn, password)
    runtime["preload_diagnostic"] = sql_preload_diagnostic(admin.info.dsn, password)
    return runtime


def sql_preload_diagnostic(dsn: str, password: str) -> dict[str, Any]:
    runtime_dsn = conninfo_to_dict(dsn)
    runtime_dsn.update(user="pgag_age_reader", password=password)
    with psycopg.connect(make_conninfo(**runtime_dsn), autocommit=True) as runtime:
        runtime.execute("SET ROLE pgag_runtime")
        row = runtime.execute(
            "SELECT p.prosecdef, p.provolatile, p.proconfig, p.pronargs, "
            "p.prorettype = 'pg_catalog.bool'::pg_catalog.regtype, l.lanname, o.rolsuper, "
            "EXISTS (SELECT FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_extension e "
            "ON e.oid = d.refobjid WHERE d.classid = 'pg_catalog.pg_proc'::pg_catalog.regclass "
            "AND d.refclassid = 'pg_catalog.pg_extension'::pg_catalog.regclass "
            "AND d.objid = p.oid AND d.deptype = 'e' AND e.extname = 'age' "
            "AND e.extowner = p.proowner), "
            "pg_catalog.pg_has_role(current_user, p.proowner, 'MEMBER'), "
            "pg_catalog.pg_has_role(session_user, p.proowner, 'MEMBER'), "
            "NOT EXISTS (SELECT FROM pg_catalog.aclexplode("
            "COALESCE(p.proacl, pg_catalog.acldefault('f', p.proowner))) a "
            "WHERE a.grantee NOT IN (0, p.proowner) OR a.is_grantable "
            "OR a.privilege_type <> 'EXECUTE'), "
            "pg_catalog.pg_has_role(current_user, 'pg_read_all_settings', 'MEMBER'), "
            "pg_catalog.pg_has_role(session_user, 'pg_read_all_settings', 'MEMBER'), "
            "pg_catalog.has_schema_privilege(current_user, 'ag_catalog', 'CREATE') "
            "FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_language l ON l.oid = p.prolang "
            "JOIN pg_catalog.pg_roles o ON o.oid = p.proowner "
            "WHERE p.oid = 'ag_catalog.pgag_age_preloaded()'::pg_catalog.regprocedure"
        ).fetchone()
        if row != tuple(PRELOAD_METADATA.values()):
            raise RuntimeError("preload diagnostic has unexpected definition or owner permissions")
        if runtime.execute("SELECT ag_catalog.pgag_age_preloaded()").fetchone() != (True,):
            raise RuntimeError("preload diagnostic did not confirm AGE preload")
    return {**PRELOAD_METADATA, "preloaded": True}


original.setup = setup


def installed_identity(dsn: str) -> dict[str, Any]:
    with psycopg.connect(dsn, autocommit=True) as admin:
        row = admin.execute("SELECT pg_read_file(%s)::jsonb", (MANIFEST_PATH,)).fetchone()
    if row is None or row[0] != ARTIFACT:
        raise RuntimeError("installed patched AGE build identity does not match pinned source")
    return row[0]


def probe(dsn: str) -> dict[str, Any]:
    identity = installed_identity(dsn)
    report = original.probe(dsn)
    report["format"] = "pgag-age-patched-qualification-v1"
    report["installed_build_identity"] = identity
    historical_constraint = "Pinned upstream tag literally includes rc0; no stable-release claim."
    report["constraints"] = [
        constraint for constraint in report["constraints"] if constraint != historical_constraint
    ] + [
        "Public upstream base plus exact local committed patch; not the historical rc0 artifact.",
        "Original 19 native/direct and 40 fixed-template checks are unchanged.",
        "Preload helper is a narrow diagnostic definer, not a data or RLS authorization bypass.",
        "Passing this bounded synthetic probe is not whole-M3 production approval.",
    ]
    counts = {
        "native_and_direct": len(report["checks"]),
        "fixed_template": len(report["fixed_template_candidate"]["checks"]),
    }
    report["original_check_counts"] = counts
    report["original_checks_complete"] = counts == {
        "native_and_direct": 19, "fixed_template": 40,
    }
    report["qualified"] = report["qualified"] and report["original_checks_complete"]
    return report


def main() -> int:
    dsn = os.environ.get("PGAG_AGE_ADMIN_DATABASE_URL")
    if not dsn or os.environ.get("PGAG_AGE_DISPOSABLE") != "1":
        print(json.dumps({"qualified": False, "error": "explicit disposable AGE profile required"}))
        return 2
    try:
        report = probe(dsn)
    except (OSError, ValueError, RuntimeError, psycopg.Error) as error:
        print(json.dumps({
            "qualified": False, "phase": "identity_setup_or_probe",
            "error_type": type(error).__name__,
            "sqlstate": getattr(error, "sqlstate", None),
        }))
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
