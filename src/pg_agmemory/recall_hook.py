import asyncio
import json
import logging
import os
import sys
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

from pg_agmemory.models import Contract, Recall, RecallResult, SearchProfile, ShortText
from pg_agmemory.native_client import (
    AdapterError,
    AdapterFailure,
    NativeHTTPClient,
    NativeSettings,
    failure,
)

MAX_INPUT_BYTES = 32768
HookEvent = Literal["session_start", "task_switch", "after_compaction"]
logger = logging.getLogger("pg_agmemory.recall_hook")


class HookInput(Contract):
    event: HookEvent
    query: Annotated[str, Field(max_length=4096)]


class HookSettings(Contract):
    model_config = ConfigDict(frozen=True)

    api_url: Annotated[str, StringConstraints(strip_whitespace=False)]
    api_token: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(repr=False)
    scope_ids: Annotated[list[UUID], Field(min_length=1, max_length=32)]
    purpose: ShortText = "implicit_context"
    token_budget: Annotated[int, Field(ge=64, le=2000)] = 2000
    max_items: Annotated[int, Field(ge=1, le=20)] = 20
    search_profile: SearchProfile = "simple-v1"
    timeout_seconds: Annotated[float, Field(ge=0.1, le=20, allow_inf_nan=False)] = 2.0

    @model_validator(mode="after")
    def validate_startup(self) -> "HookSettings":
        NativeSettings(self.api_url, self.api_token)
        if len(set(self.scope_ids)) != len(self.scope_ids):
            raise ValueError("Hook scopes must be unique")
        return self

    @classmethod
    def from_env(cls) -> "HookSettings":
        values = {
            name: os.environ[f"PGAG_HOOK_{name.upper()}"]
            for name in cls.model_fields
            if f"PGAG_HOOK_{name.upper()}" in os.environ
        }
        try:
            scopes = json.loads(values.pop("scope_ids", "null"))
            return cls.model_validate({**values, "scope_ids": scopes})
        except (ValueError, RecursionError):
            raise failure("invalid_hook_configuration") from None

    def request(self, data: HookInput) -> Recall:
        return Recall(
            query=data.query,
            scope_ids=self.scope_ids,
            purpose=self.purpose,
            mode="implicit",
            token_budget=self.token_budget,
            max_items=self.max_items,
            search_profile=self.search_profile,
        )


class HookOutput(BaseModel):
    status: Literal["ok", "error"]
    event: HookEvent | None
    result: RecallResult | None
    error: AdapterError | None


def parse_input(raw: bytes) -> HookInput:
    if len(raw) > MAX_INPUT_BYTES:
        raise failure("hook_input_too_large")
    try:
        data = HookInput.model_validate(json.loads(raw.decode("utf-8")))
        data.query.encode("utf-8")
        return data
    except (ValueError, RecursionError):
        raise failure("invalid_hook_input") from None


async def recall(settings: HookSettings, data: HookInput, native: NativeHTTPClient) -> RecallResult:
    try:
        async with asyncio.timeout(settings.timeout_seconds):
            await native.validate()
            result = await native.request(
                "/v1/recall", settings.request(data), TypeAdapter(RecallResult)
            )
    except TimeoutError:
        raise failure("hook_deadline_exceeded", retryable=True) from None
    try:
        byte_count = len(
            json.dumps(
                result.context_pack.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except UnicodeEncodeError:
        raise failure("invalid_native_response") from None
    if (
        byte_count != result.context_pack.byte_count
        or byte_count > settings.token_budget
        or len(result.items) > settings.max_items
        or result.search_profile != settings.search_profile
        or result.retrieval_mode != "lexical"
        or result.embedding_model is not None
        or result.coverage.vector_incomplete
        or any(item.retrieval is not None for item in result.items)
    ):
        raise failure("invalid_native_response")
    return result


async def run(settings: HookSettings, data: HookInput) -> RecallResult:
    async with NativeSettings(settings.api_url, settings.api_token).client() as client:
        return await recall(settings, data, NativeHTTPClient(client))


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s", force=True)
    event: HookEvent | None = None
    exit_code = 2
    try:
        settings = HookSettings.from_env()
        try:
            raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        except OSError:
            raise failure("hook_input_unavailable") from None
        data = parse_input(raw)
        event = data.event
        exit_code = 1
        result = asyncio.run(run(settings, data))
        output = HookOutput(status="ok", event=event, result=result, error=None)
        exit_code = 0
    except AdapterFailure as exc:
        logger.warning("hook_error code=%s", exc.error.code)
        output = HookOutput(status="error", event=event, result=None, error=exc.error)
    try:
        print(output.model_dump_json())
        sys.stdout.flush()
    except BrokenPipeError:
        logger.warning("hook_error code=hook_output_unavailable")
        raise SystemExit(1) from None
    raise SystemExit(exit_code)
