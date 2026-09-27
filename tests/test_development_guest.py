import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "development_guest", Path(__file__).parents[1] / "scripts" / "development-eval-guest.py",
)
assert SPEC is not None and SPEC.loader is not None
guest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guest)


def test_seed_export_exact_bytes_and_stable_tree(tmp_path: Path) -> None:
    records = [
        guest.file_record("src/main.py", b"print('hello')\n"),
        guest.file_record("tests.py", b"assert True\n"),
    ]
    seeded = guest.seed(tmp_path, {"files": records})
    exported = guest.collect(tmp_path, ["src/main.py", "tests.py"])
    assert seeded["tree_sha256"] == exported["tree_sha256"]
    assert exported["files"] == records
    assert exported["bytes"] == sum(row["size"] for row in records)


@pytest.mark.parametrize("path", ["../x", "/tmp/x", "a/../x", ".git/x", "a/.x", "a//b", ""])
def test_paths_fail_closed(path: str) -> None:
    with pytest.raises(guest.GuestError):
        guest.safe_path(path)


def test_seed_validates_entire_input_before_writing(tmp_path: Path) -> None:
    first = guest.file_record("first.py", b"pass\n")
    invalid = guest.file_record("second.py", b"pass\n") | {"sha256": "0" * 64}
    with pytest.raises(guest.GuestError, match="hash_mismatch"):
        guest.seed(tmp_path, {"files": [first, invalid]})
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "unexpected", "directory"])
def test_export_rejects_noncanonical_files(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "main.py"
    if kind == "symlink":
        target.symlink_to("/etc/hostname")
    elif kind == "hardlink":
        (tmp_path / "other.py").write_text("pass\n")
        target.hardlink_to(tmp_path / "other.py")
    elif kind == "fifo":
        guest.os.mkfifo(target)
    elif kind == "unexpected":
        (tmp_path / "notes.txt").write_text("memory\n")
    else:
        (tmp_path / "private").mkdir()
    with pytest.raises((guest.GuestError, OSError)):
        guest.collect(tmp_path, ["main.py", "other.py"])


def test_export_and_seed_limits_are_actual_bytes(tmp_path: Path) -> None:
    raw = b"x" * guest.MAX_BYTES
    guest.seed(tmp_path, {"files": [guest.file_record("main.py", raw)]})
    assert guest.collect(tmp_path, ["main.py"])["bytes"] == guest.MAX_BYTES
    (tmp_path / "main.py").write_bytes(raw + b"x")
    with pytest.raises(guest.GuestError, match="artifact_limit"):
        guest.collect(tmp_path, ["main.py"])


def test_duplicate_json_fields_rejected() -> None:
    with pytest.raises(guest.GuestError, match="duplicate_json_key"):
        json.loads('{"files":[],"files":[]}', object_pairs_hook=guest.unique_pairs)


def test_process_inventory_uses_uid_not_name(tmp_path: Path) -> None:
    for pid, uid in [(10, 0), (20, guest.TASK_UID), (30, 10002)]:
        directory = tmp_path / str(pid)
        directory.mkdir()
        (directory / "status").write_text(f"Name:\tpython\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
    assert guest.task_processes(tmp_path) == [20]


@pytest.mark.parametrize("state", ["Z", "X"])
def test_zombie_thread_group_is_not_treated_as_quiescent(tmp_path: Path, state: str) -> None:
    for pid, threads in [(10, 1), (20, 2)]:
        directory = tmp_path / str(pid)
        directory.mkdir()
        (directory / "status").write_text(
            f"State:\t{state}\nUid:\t{guest.TASK_UID}\nThreads:\t{threads}\n",
        )
    assert guest.task_processes(tmp_path) == [20]


@pytest.mark.parametrize("threads", [
    "", "Threads:\t0\n", "Threads:\t-1\n", "Threads:\t1.5\n",
    "Threads:\t1 trailing\n", "Threads:\tinvalid\n",
])
def test_unknown_zombie_thread_count_fails_closed(tmp_path: Path, threads: str) -> None:
    directory = tmp_path / "20"
    directory.mkdir()
    (directory / "status").write_text(f"State:\tZ\nUid:\t{guest.TASK_UID}\n{threads}")
    with pytest.raises(guest.GuestError, match="process_inventory_invalid"):
        guest.task_processes(tmp_path)
