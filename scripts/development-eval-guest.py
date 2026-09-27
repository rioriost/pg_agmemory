"""Immutable filesystem helper for a run-owned, networkless execution guest."""

import argparse
import base64
import hashlib
import json
import os
import re
import signal
import stat
import sys
from pathlib import Path
from typing import Any

WORKSPACE = Path("/workspace")
MAX_FILES = 32
MAX_BYTES = 262144
TASK_UID = 10001
CANDIDATE_MARKER = b"PGAG-CANDIDATE-START-v1\n"


class GuestError(Exception):
    pass


def require(condition: bool, code: str) -> None:
    if not condition:
        raise GuestError(code)


def safe_path(value: object) -> str:
    require(isinstance(value, str), "path_required")
    assert isinstance(value, str)
    require(len(value.encode("utf-8")) <= 256, "path_limit")
    require(
        all(re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]*", part)
            for part in value.split("/")),
        "unsafe_path",
    )
    return value


def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def no_constant(value: str) -> None:
    raise GuestError("nonfinite_json")


def read_request() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(2 * MAX_BYTES + 1)
    require(len(raw) <= 2 * MAX_BYTES, "request_limit")
    value = json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=no_constant)
    require(isinstance(value, dict), "request_object_required")
    return value


def fields(value: object, names: set[str]) -> dict[str, Any]:
    require(isinstance(value, dict) and set(value) == names, "invalid_fields")
    assert isinstance(value, dict)
    return value


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_record(path: str, raw: bytes) -> dict[str, Any]:
    return {
        "path": path, "size": len(raw), "sha256": digest(raw),
        "content_base64": base64.b64encode(raw).decode("ascii"),
    }


def tree_digest(records: list[dict[str, Any]]) -> str:
    manifest = [
        {"path": row["path"], "size": row["size"], "sha256": row["sha256"]}
        for row in records
    ]
    return digest(json.dumps(
        manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))


def seed(root: Path, request: dict[str, Any]) -> dict[str, Any]:
    fields(request, {"files"})
    source = request["files"]
    require(isinstance(source, list) and len(source) <= MAX_FILES, "file_limit")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    for row in source:
        fields(row, {"path", "size", "sha256", "content_base64"})
        name = safe_path(row["path"])
        require(name not in seen, "duplicate_path")
        seen.add(name)
        require(isinstance(row["content_base64"], str), "content_required")
        raw = base64.b64decode(row["content_base64"], validate=True)
        require(type(row["size"]) is int and len(raw) == row["size"], "size_mismatch")
        require(digest(raw) == row["sha256"], "hash_mismatch")
        total += len(raw)
        require(total <= MAX_BYTES, "artifact_limit")
        records.append(file_record(name, raw))
    require(root.is_dir() and not root.is_symlink() and not any(root.iterdir()),
            "empty_workspace_required")
    for row in records:
        target = root / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o777)
        for parent in target.parents:
            if parent == root:
                break
            parent.chmod(0o777)
        with target.open("xb") as stream:
            stream.write(base64.b64decode(row["content_base64"], validate=True))
        target.chmod(0o666)
    ordered = sorted(records, key=lambda row: row["path"])
    return {"status": "ok", "files": len(ordered), "bytes": total,
            "tree_sha256": tree_digest(ordered)}


def task_processes(proc: Path = Path("/proc")) -> list[int]:
    result: list[int] = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            text = (entry / "status").read_text()
        except FileNotFoundError:
            continue
        match = re.search(r"^Uid:\s+(\d+)\s+", text, re.MULTILINE)
        require(match is not None, "process_inventory_invalid")
        assert match is not None
        if int(match.group(1)) != TASK_UID:
            continue
        state = re.search(r"^State:\s+([A-Z])", text, re.MULTILINE)
        if state is not None and state.group(1) in ("Z", "X"):
            threads = re.search(r"^Threads:\s+([1-9][0-9]*)\s*$", text, re.MULTILINE)
            require(threads is not None, "process_inventory_invalid")
            assert threads is not None
            # A zombie leader can still own live worker threads.
            if int(threads.group(1)) == 1:
                continue
        result.append(int(entry.name))
    return sorted(result)


def stop_task_processes() -> None:
    require(os.getuid() == TASK_UID, "task_cleanup_uid_required")
    for pid in task_processes():
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue


def collect(root: Path, allowed: list[str]) -> dict[str, Any]:
    require(len(allowed) <= MAX_FILES and len(set(allowed)) == len(allowed), "file_limit")
    permitted = set(map(safe_path, allowed))
    require(root.is_dir() and not root.is_symlink(), "workspace_required")
    records: list[dict[str, Any]] = []
    total = 0
    for directory, directories, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in directories:
            child = base / name
            require(not child.is_symlink(), "artifact_symlink")
            relative = child.relative_to(root).as_posix()
            require(any(path.startswith(relative + "/") for path in permitted),
                    "unexpected_directory")
        for name in filenames:
            target = base / name
            relative = target.relative_to(root).as_posix()
            require(relative in permitted, "unexpected_file")
            descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(descriptor)
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                        "artifact_regular_single_link_required")
                require(info.st_size <= MAX_BYTES - total, "artifact_limit")
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    raw = stream.read(MAX_BYTES - total + 1)
                require(len(raw) == info.st_size and len(raw) <= MAX_BYTES - total,
                        "artifact_changed_or_oversized")
            finally:
                os.close(descriptor)
            total += len(raw)
            records.append(file_record(relative, raw))
    records.sort(key=lambda row: row["path"])
    return {"status": "ok", "files": records, "bytes": total,
            "tree_sha256": tree_digest(records)}


def probe() -> dict[str, Any]:
    return {
        "status": "ok", "uid": os.getuid(),
        "network_interfaces": sorted(path.name for path in Path("/sys/class/net").iterdir()),
        "routes": Path("/proc/net/route").read_text().splitlines()[1:],
        "workspace_mount": [
            line for line in Path("/proc/mounts").read_text().splitlines()
            if line.split()[1] == "/workspace"
        ],
        "root_mount": [
            line for line in Path("/proc/mounts").read_text().splitlines()
            if line.split()[1] == "/"
        ],
        "effective_capabilities": next(
            line.split()[1] for line in Path("/proc/self/status").read_text().splitlines()
            if line.startswith("CapEff:")
        ),
        "task_processes": task_processes(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("seed", "export", "stop-tasks", "probe", "candidate"))
    parser.add_argument("--entry-point")
    args = parser.parse_args()
    require(sys.platform == "linux" and Path("/opt/pgag-eval/owned-guest").is_file(),
            "owned_linux_guest_required")
    if args.operation == "stop-tasks":
        stop_task_processes()
        return
    if args.operation == "candidate":
        require(os.getuid() == TASK_UID, "candidate_uid_required")
        entry = safe_path(args.entry_point)
        sys.stdout.buffer.write(CANDIDATE_MARKER)
        sys.stdout.buffer.flush()
        os.execv(sys.executable, [sys.executable, "-E", "-s", "-B", str(WORKSPACE / entry)])
    if args.operation == "seed":
        require(os.getuid() == TASK_UID, "task_seed_uid_required")
        require(task_processes() == [os.getpid()], "task_processes_still_present")
        result = seed(WORKSPACE, read_request())
    else:
        require(os.getuid() == 0, "trusted_helper_uid_required")
        require(not task_processes(), "task_processes_still_present")
        if args.operation == "export":
            request = fields(read_request(), {"allowed_paths"})
            require(isinstance(request["allowed_paths"], list), "paths_required")
            result = collect(WORKSPACE, request["allowed_paths"])
        else:
            result = probe()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    try:
        main()
    except (GuestError, ValueError, OSError, UnicodeError, RecursionError) as error:
        code = str(error) if isinstance(error, GuestError) else type(error).__name__
        print(json.dumps({"status": "error", "code": code}), flush=True)
        raise SystemExit(1) from None
