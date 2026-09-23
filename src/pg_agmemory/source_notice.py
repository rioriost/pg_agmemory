"""Authenticated local source notices, not an upstream entitlement verifier."""

import argparse
import base64
import json
import math
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal, Never
from uuid import UUID

import jwt
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pg_agmemory.admin import MAX_EPOCH, AdminError, Epoch
from pg_agmemory.human_review import _invalid_constant, _unique_object, load_json
from pg_agmemory.source_access import (
    SourceAccessRequest,
    SourceAccessResult,
    SourceIdentity,
    SourceNotice,
    source_access,
)

MAX_SIGNED_NOTICE_BYTES = 16384
MAX_SOURCE_PROFILE_BYTES = 32768
MAX_SOURCE_DELIVERY_BYTES = 32768
MAX_SIGNED_NOTICE_SECONDS = 300

SignerText = Annotated[str, Field(strict=True, min_length=1, max_length=256)]
NoticeTimestamp = Annotated[int, Field(strict=True, ge=0, le=253402300799)]


def _public_key(value: str) -> rsa.RSAPublicKey:
    try:
        if not re.fullmatch(
            r"-----BEGIN (PUBLIC KEY|RSA PUBLIC KEY)-----\r?\n"
            r"[A-Za-z0-9+/=\r\n]+-----END \1-----",
            value.strip(),
        ):
            raise ValueError("Invalid public key")
        key = load_pem_public_key(value.encode("utf-8"))
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
            raise ValueError("Invalid public key")
    except (UnsupportedAlgorithm, ValueError, TypeError, OverflowError, RecursionError):
        raise ValueError("Invalid source notice public key") from None
    return key


class SourceNoticeProfile(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always", hide_input_in_errors=True
    )
    format: Literal["pgag-source-notice-profile-v1"] = "pgag-source-notice-profile-v1"
    issuer: SignerText
    audience: SignerText
    subject: SignerText
    key_id: SignerText
    public_key: Annotated[str, Field(strict=True, min_length=1, max_length=8192, repr=False)]
    tenant_id: UUID
    scope_id: UUID
    principal_id: UUID
    source: SourceIdentity

    @field_validator("issuer", "audience", "subject", "key_id")
    @classmethod
    def exact_signer_text(cls, value: str) -> str:
        try:
            value.encode("utf-8")
            if value != value.strip() or "\x00" in value:
                raise ValueError("Invalid signer identity")
        except (ValueError, TypeError, OverflowError):
            raise ValueError("Invalid source notice signer identity") from None
        return value

    @field_validator("public_key")
    @classmethod
    def rsa_public_key(cls, value: str) -> str:
        _public_key(value)
        return value


class SignedSourceNoticeDelivery(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always", hide_input_in_errors=True
    )
    format: Literal["pgag-signed-source-notice-v1"] = "pgag-signed-source-notice-v1"
    token: Annotated[
        str, Field(strict=True, min_length=1, max_length=MAX_SIGNED_NOTICE_BYTES, repr=False)
    ]

    @field_validator("token")
    @classmethod
    def ascii_token(cls, value: str) -> str:
        try:
            value.encode("ascii")
        except (ValueError, TypeError, OverflowError):
            raise ValueError("Invalid signed source notice token") from None
        return value


class _NoticeHeader(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    alg: Literal["RS256"]
    typ: Literal["pgag-source-notice+jwt"]
    kid: SignerText


class _NoticeClaims(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    iss: SignerText
    aud: SignerText
    sub: SignerText
    iat: NoticeTimestamp
    exp: NoticeTimestamp
    notice: SourceNotice

    @model_validator(mode="after")
    def bounded_lifetime(self) -> "_NoticeClaims":
        if not self.iat < self.exp <= self.iat + MAX_SIGNED_NOTICE_SECONDS:
            raise ValueError("Invalid signed source notice lifetime")
        return self


class SourceNoticeResult(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always", hide_input_in_errors=True
    )
    notification_signature_verified: Literal[True] = True
    source_authorization_verified: Literal[False] = False
    access: SourceAccessResult


def _validated_profile(profile: SourceNoticeProfile) -> SourceNoticeProfile:
    try:
        return SourceNoticeProfile.model_validate(profile)
    except (UnsupportedAlgorithm, ValueError, TypeError, OverflowError, RecursionError):
        raise AdminError("invalid_source_notice_profile") from None


def _decode_segment(value: str) -> bytes:
    if re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise ValueError("Invalid signed source notice encoding")
    decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
        raise ValueError("Invalid signed source notice encoding")
    return decoded


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def _strict_json(value: bytes) -> object:
    return json.loads(
        value.decode("utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_invalid_constant,
        parse_float=_finite_float,
    )


def verify_source_notice(
    profile: SourceNoticeProfile, delivery: SignedSourceNoticeDelivery
) -> SourceNotice:
    profile = _validated_profile(profile)
    try:
        delivery = SignedSourceNoticeDelivery.model_validate(delivery)
        segments = delivery.token.split(".")
        if len(segments) != 3:
            raise ValueError("Invalid signed source notice encoding")
        header_bytes, claims_bytes, _ = (_decode_segment(segment) for segment in segments)
        header = _NoticeHeader.model_validate(_strict_json(header_bytes))
        claims = _NoticeClaims.model_validate(_strict_json(claims_bytes))
        if (
            header.kid != profile.key_id
            or claims.iss != profile.issuer
            or claims.aud != profile.audience
            or claims.sub != profile.subject
            or claims.notice.source != profile.source
        ):
            raise ValueError("Invalid signed source notice identity")
        jwt.decode(
            delivery.token,
            _public_key(profile.public_key),
            algorithms=["RS256"],
            issuer=profile.issuer,
            audience=profile.audience,
            subject=profile.subject,
            leeway=0,
            options={
                "require": ["iss", "aud", "sub", "iat", "exp", "notice"],
                "strict_aud": True,
                "verify_signature": True,
                "verify_iat": True,
                "verify_exp": True,
            },
        )
        return claims.notice
    except (
        jwt.InvalidTokenError,
        UnsupportedAlgorithm,
        ValueError,
        TypeError,
        OverflowError,
        RecursionError,
    ):
        raise AdminError("invalid_signed_source_notice") from None


@contextmanager
def receive_source_notice(
    url: str,
    profile: SourceNoticeProfile,
    delivery: SignedSourceNoticeDelivery,
    *,
    expected_access_epoch: Epoch,
) -> Iterator[SourceNoticeResult]:
    # Keep a private revalidated copy for both verification and local routing.
    profile = _validated_profile(profile)
    notice = verify_source_notice(profile, delivery)
    try:
        request = SourceAccessRequest(
            operation="apply",
            tenant_id=profile.tenant_id,
            scope_id=profile.scope_id,
            principal_id=profile.principal_id,
            expected_access_epoch=expected_access_epoch,
            notice=notice,
        )
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise AdminError("invalid_source_access_request") from None
    with source_access(url, request) as access:
        yield SourceNoticeResult(access=access)


class SourceNoticeParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        super().error("invalid_source_notice_arguments")


def main(argv: list[str]) -> None:
    parser = SourceNoticeParser(
        prog="pg-agmemory source-notice",
        description="Apply a signed local source notice using a trusted operator profile.",
        epilog=(
            "Verifies notification signatures, not upstream authorization. "
            "Requires an existing source binding; never auto-binds or retries. "
            "Applied sequence_gap and invalid_lease denials exit 1 with a JSON result; "
            "explicit denials exit 0."
        ),
        allow_abbrev=False,
    )
    parser.add_argument("operation", choices=("apply",))
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--delivery-file", required=True, type=Path)
    parser.add_argument("--expected-access-epoch", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.expected_access_epoch <= MAX_EPOCH:
            parser.error("invalid_source_notice_arguments")
        profile = load_json(args.profile, SourceNoticeProfile, max_bytes=MAX_SOURCE_PROFILE_BYTES)
        delivery = load_json(
            args.delivery_file, SignedSourceNoticeDelivery, max_bytes=MAX_SOURCE_DELIVERY_BYTES
        )
    except (OSError, ValueError, TypeError, OverflowError, RecursionError, UnsupportedAlgorithm):
        parser.error("invalid_source_notice_arguments")
    url = os.environ.get("PGAG_ADMIN_DATABASE_URL", "")
    if not url.strip():
        parser.exit(2, "pg-agmemory source-notice: PGAG_ADMIN_DATABASE_URL is required\n")
    try:
        with receive_source_notice(
            url, profile, delivery, expected_access_epoch=args.expected_access_epoch
        ) as result:
            print(result.model_dump_json(), flush=True)
            if result.access.reason in ("invalid_lease", "sequence_gap"):
                raise SystemExit(1)
    except AdminError as exc:
        print(
            json.dumps({"error": {"code": exc.code, "outcome_unknown": exc.outcome_unknown}}),
            flush=True,
        )
        raise SystemExit(1) from None
