import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from importlib.resources import files

from pg_agmemory.api import create_app

profile = sys.argv[1]
assert profile in ("core", "hook", "sdk")
assert callable(create_app)
assert importlib.util.find_spec("mcp") is None
assert (importlib.util.find_spec("httpx") is not None) == (profile != "core")
assert files("pg_agmemory").joinpath("py.typed").is_file()
if profile == "core":
    try:
        importlib.import_module("pg_agmemory.sdk")
    except ImportError as exc:
        assert str(exc) == "Python SDK requires the pg-agmemory[sdk] extra"
    else:
        raise AssertionError("SDK imported without its HTTP dependency")
else:
    from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError

    async def probe():
        try:
            async with AsyncMemoryClient("http://127.0.0.1:1", "fixed.identity.signature"):
                raise AssertionError("Unreachable SDK endpoint accepted")
        except MemoryClientError as exc:
            assert exc.error.code == "native_api_unavailable"
            assert not exc.error.outcome_unknown

    asyncio.run(probe())
for command in ("mcp", "recall-hook"):
    process = subprocess.run(
        ["pg-agmemory", command],
        input='{"event":"session_start","query":""}',
        env={
            "PATH": os.environ["PATH"],
            "PGAG_HOOK_API_URL": "http://127.0.0.1:1",
            "PGAG_HOOK_API_TOKEN": "fixed.identity.signature",
            "PGAG_HOOK_SCOPE_IDS": '["00000000-0000-0000-0000-000000000001"]',
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    if profile != "core" and command == "recall-hook":
        assert process.returncode == 1
        result = json.loads(process.stdout)
        assert result["status"] == "error" and result["result"] is None
        assert result["error"]["code"] == "native_api_unavailable"
    else:
        assert process.returncode == 2 and process.stdout == ""
        extra = "mcp" if command == "mcp" else "hook"
        assert f"requires the pg-agmemory[{extra}] extra" in process.stderr
    assert "Traceback" not in process.stderr
print(f"Optional adapter installation smoke passed: {profile}")
