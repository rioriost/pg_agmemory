import asyncio
import ipaddress
import json
import re
from collections.abc import Set
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from pg_agmemory import __version__
from pg_agmemory.database import SCHEMA_VERSION
from pg_agmemory.models import ErrorBody

MAX_REQUEST_BYTES = 262144
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
SAFE_NATIVE_CODES = {
    "unauthenticated",
    "not_found",
    "idempotency_conflict",
    "invalid_evidence",
    "invalid_request",
    "malformed_json",
    "body_too_large",
    "budget_too_small",
    "budget_exhausted",
    "deletion_limit_exceeded",
    "relation_invalidated",
    "dependency_unavailable",
    "database_error",
}


@dataclass(frozen=True)
class NativeSettings:
    api_url: str
    api_token: str = field(repr=False)

    def __post_init__(self) -> None:
        try:
            if any(character.isspace() or ord(character) < 32 for character in self.api_url):
                raise ValueError
            url = urlsplit(self.api_url)
            port = url.port
            if (
                url.scheme not in ("http", "https")
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.path not in ("", "/")
                or url.query
                or url.fragment
                or (port is not None and not 1 <= port <= 65535)
            ):
                raise ValueError
            if url.scheme == "http" and url.hostname != "localhost":
                if not ipaddress.ip_address(url.hostname).is_loopback:
                    raise ValueError
            httpx.URL(self.api_url)
        except (ValueError, httpx.InvalidURL):
            raise ValueError(
                "Native API URL requires HTTPS or loopback HTTP, with no path or credentials"
            ) from None
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", self.api_token):
            raise ValueError("Native API requires a startup bearer token")
        if len(self.api_token) > 16377:
            raise ValueError("Startup bearer token exceeds the Native API limit")

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.api_url,
            headers={"Authorization": f"Bearer {self.api_token}"},
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(10, connect=5),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
        )


class AdapterError(BaseModel):
    code: str
    retryable: bool
    outcome_unknown: bool = False
    native_status: int | None = None
    request_id: UUID | None = None


class AdapterFailure(Exception):
    def __init__(self, error: AdapterError) -> None:
        self.error = error
        super().__init__(error.code)


def failure(
    code: str,
    *,
    retryable: bool = False,
    unknown: bool = False,
    status: int | None = None,
    request_id: UUID | None = None,
) -> AdapterFailure:
    return AdapterFailure(
        AdapterError(
            code=code,
            retryable=retryable,
            outcome_unknown=unknown,
            native_status=status,
            request_id=request_id,
        )
    )


class NativeHTTPClient:
    def __init__(
        self, client: httpx.AsyncClient, *, safe_codes: Set[str] = SAFE_NATIVE_CODES
    ) -> None:
        self.client = client
        self.safe_codes = safe_codes

    async def exchange(
        self,
        path: str,
        *,
        body: bytes | None = None,
        key: str | None = None,
        mutation: bool = False,
    ) -> tuple[int, Any]:
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if key is not None:
            headers["Idempotency-Key"] = key
        try:
            async with asyncio.timeout(20):
                async with self.client.stream(
                    "GET" if body is None else "POST",
                    path,
                    content=body,
                    headers=headers,
                ) as response:
                    if response.headers.get("content-type", "").split(";")[0] != "application/json":
                        raise failure("invalid_native_response", unknown=mutation)
                    if response.headers.get("content-encoding", "identity") != "identity":
                        raise failure("invalid_native_response", unknown=mutation)
                    content = bytearray()
                    async for part in response.aiter_raw():
                        content.extend(part)
                        if len(content) > MAX_RESPONSE_BYTES:
                            raise failure("invalid_native_response", unknown=mutation)
                    try:
                        payload = json.loads(content)
                    except (ValueError, UnicodeError, RecursionError):
                        raise failure("invalid_native_response", unknown=mutation) from None
                    if not 200 <= response.status_code < 300:
                        try:
                            native = ErrorBody.model_validate(payload)
                            request_id = UUID(native.request_id)
                        except (ValidationError, ValueError):
                            raise failure(
                                "invalid_native_response",
                                unknown=mutation,
                                status=response.status_code,
                            ) from None
                        raise failure(
                            native.code if native.code in self.safe_codes else "native_api_error",
                            retryable=response.status_code in (429, 503),
                            unknown=mutation and response.status_code >= 500,
                            status=response.status_code,
                            request_id=request_id,
                        )
                    return response.status_code, payload
        except (httpx.TransportError, TimeoutError):
            raise failure("native_api_unavailable", retryable=True, unknown=mutation) from None

    async def validate(self) -> None:
        status, data = await self.exchange("/v1/capabilities")
        if (
            status != 200
            or not isinstance(data, dict)
            or any(
                data.get(key) != value
                for key, value in (
                    ("api_version", "v1"),
                    ("service_version", __version__),
                    ("schema_version", SCHEMA_VERSION),
                )
            )
        ):
            raise failure("native_version_mismatch")

    async def request[T: BaseModel](
        self,
        path: str,
        request: BaseModel | None,
        response: TypeAdapter[T],
        *,
        expected_status: int = 200,
        key: str | None = None,
        mutation: bool = False,
        max_request_bytes: int = MAX_REQUEST_BYTES,
    ) -> T:
        body = None
        try:
            if request is not None:
                body = json.dumps(
                    request.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
                ).encode()
        except UnicodeEncodeError:
            raise failure("invalid_request") from None
        if body is not None and len(body) > max_request_bytes:
            raise failure("body_too_large")
        status, payload = await self.exchange(path, body=body, key=key, mutation=mutation)
        if status != expected_status:
            raise failure("invalid_native_response", unknown=mutation)
        try:
            return response.validate_python(payload)
        except ValidationError:
            raise failure("invalid_native_response", unknown=mutation) from None
