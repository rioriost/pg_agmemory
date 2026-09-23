"""Startup failures must remain diagnosable without printing runtime credentials."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("engine", ["container", "docker"])
@pytest.mark.parametrize("failure", ["age", "test", "runtime", "extension"])
@pytest.mark.parametrize("reuse_runtime", [False, True])
def test_enabled_runner_surfaces_setup_errors_and_waits_for_tcp(
    tmp_path, engine, failure, reuse_runtime,
):
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    helper = scripts / "test-age-enabled-containers.sh"
    helper.write_bytes((ROOT / "scripts/test-age-enabled-containers.sh").read_bytes())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.jsonl"
    stub = """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

name, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
if name == "jq":
    sys.stdin.read()
    print("192.0.2.7")
    raise SystemExit(0)
if name == "git":
    print("a" * 40)
    raise SystemExit(0)
safe = ["REDACTED" if "PASSWORD=" in arg else arg for arg in args]
with pathlib.Path(os.environ["TEST_CALL_LOG"]).open("a") as stream:
    stream.write(json.dumps(safe) + "\\n")
failure = os.environ["TEST_FAILURE"]
if args[:1] == ["build"]:
    target = args[args.index("--target") + 1] if "--target" in args else "age"
    if target == failure:
        print("synthetic build diagnostic " + target, file=sys.stderr)
        raise SystemExit(37)
if args[:1] == ["exec"] and "psql" in args and failure == "extension":
    print("synthetic extension connection failure", file=sys.stderr)
    raise SystemExit(2)
if "inspect" in args:
    print("[]")
"""
    for name in (engine, "jq", "git"):
        path = bin_dir / name
        path.write_text(stub)
        path.chmod(0o700)
    environment = {
        key: value for key, value in os.environ.items()
        if key not in ("PGAG_AGE_PATCHED_IMAGE", "PGAG_AGE_TEST_IMAGE", "PGAG_AGE_RUNTIME_IMAGE")
    } | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "TEST_CALL_LOG": str(log), "TEST_FAILURE": failure,
    }
    if reuse_runtime:
        environment["PGAG_AGE_RUNTIME_IMAGE"] = "caller-owned-runtime"
    result = subprocess.run(
        ["bash", str(helper), engine], env=environment, capture_output=True,
        text=True, check=False, timeout=30,
    )
    skipped_failure = failure == "runtime" and reuse_runtime
    expected_status = 0 if skipped_failure else (2 if failure == "extension" else 37)
    assert result.returncode == expected_status, result.stderr
    expected = ("synthetic extension connection failure" if failure == "extension"
                else "synthetic build diagnostic " + failure)
    assert (expected in result.stderr) is not skipped_failure
    assert "POSTGRES_PASSWORD=" not in result.stderr + result.stdout
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    if reuse_runtime:
        assert not any(call[:1] == ["build"] and "runtime" in call for call in calls)
        assert not any(call[:2] == ["image", "rm"] and "caller-owned-runtime" in call
                       for call in calls)
    if failure == "extension" or skipped_failure:
        ready = [call for call in calls if "pg_isready" in call]
        assert ready and all(call[call.index("-h") + 1] == "127.0.0.1" for call in ready)
        if reuse_runtime:
            assert ["image", "inspect", "caller-owned-runtime"] in calls
    else:
        assert not any(call[:1] == ["run"] for call in calls)
