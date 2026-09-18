import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from pg_agmemory.providers import (
    MAX_INFERENCE_BYTES,
    ProviderFailure,
    ProviderSettings,
    make_provider,
    parse_input,
    parse_settings,
)


async def run(settings: ProviderSettings, operation: str, raw: bytes) -> dict[str, Any]:
    provider = make_provider(settings)
    if operation == "inspect":
        if raw.strip():
            raise ProviderFailure("invalid_inference_input")
        return await provider.inspect()
    data = parse_input(raw)
    if operation == "summarize":
        return (await provider.summarize(data)).model_dump(mode="json")
    if operation == "embed":
        return (await provider.embed(data)).model_dump(mode="json")
    if operation == "extract":
        return (await provider.extract(data)).model_dump(mode="json")
    raise ProviderFailure("invalid_inference_operation")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pg-agmemory infer")
    parser.add_argument("operation", choices=["inspect", "summarize", "embed", "extract"])
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s", force=True)
    try:
        try:
            with args.config.open("rb") as stream:
                settings = parse_settings(stream.read(32769))
        except OSError:
            raise ProviderFailure("invalid_provider_configuration") from None
        raw = b"" if args.operation == "inspect" else sys.stdin.buffer.read(MAX_INFERENCE_BYTES + 1)
        result = asyncio.run(run(settings, args.operation, raw))
    except ProviderFailure as exc:
        print(json.dumps({"status": "error", "result": None, "error": exc.error.model_dump()}))
        raise SystemExit(2 if exc.error.code.startswith("invalid_") else 1) from None
    print(json.dumps({"status": "ok", "result": result, "error": None}, ensure_ascii=False))
