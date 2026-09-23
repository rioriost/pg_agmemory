import base64
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from pydantic import ValidationError
from test_source_access import (
    IDENTITY,
    SOURCE,
    allow_notice,
    bind,
    database_now,
    deny_notice,
    event_rows,
    execute,
    fingerprint,
    regular_access,
)

from pg_agmemory import source_notice as ingress
from pg_agmemory.admin import MAX_EPOCH, AdminError
from pg_agmemory.source_access import SourceAccessResult, SourceIdentity, SourceNotice
from pg_agmemory.source_notice import (
    MAX_SIGNED_NOTICE_BYTES,
    MAX_SIGNED_NOTICE_SECONDS,
    MAX_SOURCE_DELIVERY_BYTES,
    MAX_SOURCE_PROFILE_BYTES,
    SignedSourceNoticeDelivery,
    SourceNoticeProfile,
    receive_source_notice,
    verify_source_notice,
)

SENSITIVE = "DO_NOT_ECHO_SYNTHETIC_NOTICE"
UNAVAILABLE_URL = "postgresql://synthetic:DO_NOT_ECHO@127.0.0.1:1/DO_NOT_ECHO"


def public_pem(key):
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def unrelated_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def profile(signing_key):
    return SourceNoticeProfile(
        issuer="synthetic-local-notifier",
        audience="synthetic-memory-ingress",
        subject="synthetic-source-administrator",
        key_id="synthetic-key-1",
        public_key=public_pem(signing_key),
        tenant_id=uuid4(),
        scope_id=uuid4(),
        principal_id=uuid4(),
        source=IDENTITY,
    )


def bound_profile(profile, env, **changes):
    return SourceNoticeProfile.model_validate(
        profile.model_dump()
        | {
            "tenant_id": env.tenants[0],
            "scope_id": env.scopes[0],
            "principal_id": env.principals[2],
        }
        | changes
    )


def claims_for(profile, notice=None, **changes):
    now = int(time.time())
    return {
        "iss": profile.issuer,
        "aud": profile.audience,
        "sub": profile.subject,
        "iat": now - 2,
        "exp": now + 120,
        "notice": (notice or deny_notice(sequence=1)).model_dump(mode="json"),
        **changes,
    }


def header_for(profile, **changes):
    return {
        "alg": "RS256",
        "typ": "pgag-source-notice+jwt",
        "kid": profile.key_id,
        **changes,
    }


def json_bytes(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def signed_raw(signing_key, header, payload):
    message = f"{b64(header)}.{b64(payload)}"
    signature = signing_key.sign(message.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{message}.{b64(signature)}"


def signed(signing_key, profile, notice=None, *, claims=None, header=None):
    return SignedSourceNoticeDelivery(
        token=signed_raw(
            signing_key,
            json_bytes(header_for(profile) if header is None else header),
            json_bytes(claims_for(profile, notice) if claims is None else claims),
        )
    )


def receive(env, profile, delivery, epoch=None):
    if epoch is None:
        epoch = execute(env).access_epoch
    with receive_source_notice(
        env.admin_url, profile, delivery, expected_access_epoch=epoch
    ) as result:
        return result


@pytest.fixture
def forbid_database(monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail("Rejected or verification-only input attempted database access")

    monkeypatch.setattr(psycopg, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    return calls


def assert_invalid_delivery(profile, delivery):
    with pytest.raises(AdminError, match="^invalid_signed_source_notice$") as failure:
        verify_source_notice(profile, delivery)
    assert not failure.value.outcome_unknown
    assert SENSITIVE not in str(failure.value)
    with pytest.raises(AdminError, match="^invalid_signed_source_notice$"):
        with receive_source_notice(
            UNAVAILABLE_URL, profile, delivery, expected_access_epoch=1
        ):
            pytest.fail("Invalid signed notice was accepted")


@pytest.fixture
def input_files():
    prefix = f".source-notice-test-{uuid4().hex}"
    paths = {
        "profile": Path(prefix + "-profile.json"),
        "delivery": Path(prefix + "-delivery.json"),
        "target": Path(prefix + "-target.json"),
    }
    try:
        yield paths
    finally:
        for path in paths.values():
            if path.is_dir() and not path.is_symlink():
                path.rmdir()
            else:
                path.unlink(missing_ok=True)


def write_inputs(input_files, profile, delivery):
    input_files["profile"].write_text(profile.model_dump_json(), encoding="utf-8")
    input_files["delivery"].write_text(delivery.model_dump_json(), encoding="utf-8")


def cli(*arguments, url=None):
    variables = {"PATH": os.environ["PATH"]}
    if url is not None:
        variables["PGAG_ADMIN_DATABASE_URL"] = url
    return subprocess.run(
        [sys.executable, "-m", "pg_agmemory.cli", "source-notice", *arguments],
        env=variables,
        capture_output=True,
        text=True,
        timeout=15,
    )


def cli_arguments(input_files, epoch=1):
    return [
        "apply",
        "--profile",
        str(input_files["profile"]),
        "--delivery-file",
        str(input_files["delivery"]),
        "--expected-access-epoch",
        str(epoch),
    ]


def assert_safe_cli_failure(result, code, exit_code=1):
    assert result.returncode == exit_code
    output = result.stdout + result.stderr
    assert code in output
    assert SENSITIVE not in output and "DO_NOT_ECHO" not in output
    assert "Traceback" not in output and "BEGIN PUBLIC KEY" not in output
    if exit_code == 1:
        assert result.stderr == ""
        assert json.loads(result.stdout)["error"]["code"] == code
    else:
        assert result.stdout == ""


def test_frozen_ingress_limits():
    assert MAX_SIGNED_NOTICE_BYTES == 16384
    assert MAX_SOURCE_PROFILE_BYTES == MAX_SOURCE_DELIVERY_BYTES == 32768
    assert MAX_SIGNED_NOTICE_SECONDS == 300


def test_real_rs256_signature_authenticates_only_configured_signer(
    profile, signing_key, forbid_database
):
    notice = deny_notice(sequence=1)
    delivery = SignedSourceNoticeDelivery(
        token=jwt.encode(
            claims_for(profile, notice),
            signing_key,
            algorithm="RS256",
            headers={"typ": "pgag-source-notice+jwt", "kid": profile.key_id},
        )
    )
    assert verify_source_notice(profile, delivery) == notice
    assert not forbid_database
    assert profile.format == "pgag-source-notice-profile-v1"
    assert delivery.format == "pgag-signed-source-notice-v1"
    for value, field in ((profile, "issuer"), (delivery, "token")):
        with pytest.raises(ValidationError):
            setattr(value, field, "replacement")


@pytest.mark.parametrize("field", ["issuer", "audience", "subject", "key_id"])
@pytest.mark.parametrize(
    "value",
    ["", " ", " leading", "trailing ", "\tvalue", "value\n", "x" * 257,
     "\x00", "\ud800", None, 1, True, b"bytes"],
)
def test_profile_requires_bounded_exact_signer_text(profile, field, value):
    with pytest.raises(ValidationError) as failure:
        SourceNoticeProfile.model_validate(profile.model_dump() | {field: value})
    assert "input_value=" not in str(failure.value)


@pytest.mark.parametrize("field", ["issuer", "audience", "subject", "key_id"])
@pytest.mark.parametrize("value", ["x" * 256, "合成署名者"])
def test_profile_exact_text_boundary_and_unicode_are_not_normalized(
    profile, signing_key, field, value, forbid_database
):
    changed = SourceNoticeProfile.model_validate(profile.model_dump() | {field: value})
    assert getattr(changed, field) == value
    assert verify_source_notice(changed, signed(signing_key, changed)).decision == "deny"


def test_only_source_identity_is_normalized(profile, signing_key, forbid_database):
    wrapped = {key: f" \t{value}\n " for key, value in SOURCE.items()}
    changed = SourceNoticeProfile.model_validate(profile.model_dump() | {"source": wrapped})
    assert changed.source == IDENTITY
    claims = claims_for(changed)
    claims["notice"]["source"] = wrapped
    notice = verify_source_notice(changed, signed(signing_key, changed, claims=claims))
    assert notice.source == IDENTITY


@pytest.mark.parametrize(
    "changes",
    [
        {"format": "wrong"},
        {"tenant_id": "DO_NOT_ECHO"},
        {"scope_id": None},
        {"principal_id": 123},
        {"source": SOURCE | {"principal_id": "DO_NOT_ECHO"}},
        {"jwks_url": "https://not-contacted.invalid/DO_NOT_ECHO"},
        {"key_id": "DO_NOT_ECHO", "password": "DO_NOT_ECHO"},
    ],
)
def test_profile_rejects_invalid_mapping_and_extra_fields(profile, changes):
    with pytest.raises(ValidationError) as failure:
        SourceNoticeProfile.model_validate(profile.model_dump() | changes)
    assert "input_value=" not in str(failure.value)


@pytest.mark.parametrize("kind", ["private", "ec", "small-rsa", "junk", "oversized", "bytes"])
def test_profile_requires_bounded_rsa_public_pem(profile, signing_key, kind):
    if kind == "private":
        pem = signing_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")
    elif kind == "ec":
        pem = public_pem(ec.generate_private_key(ec.SECP256R1()))
    elif kind == "small-rsa":
        pem = public_pem(rsa.generate_private_key(public_exponent=65537, key_size=1024))
    elif kind == "junk":
        pem = SENSITIVE
    elif kind == "bytes":
        pem = public_pem(signing_key).encode("ascii")
    else:
        pem = public_pem(signing_key) + " " * 8193
    with pytest.raises(ValidationError) as failure:
        SourceNoticeProfile.model_validate(profile.model_dump() | {"public_key": pem})
    assert "input_value=" not in str(failure.value)


def test_public_pem_exact_byte_boundary(profile, signing_key, forbid_database):
    pem = public_pem(signing_key)
    padded = pem + " " * (8192 - len(pem))
    changed = SourceNoticeProfile.model_validate(profile.model_dump() | {"public_key": padded})
    assert len(changed.public_key.encode("ascii")) == 8192
    assert verify_source_notice(changed, signed(signing_key, changed)).decision == "deny"
    with pytest.raises(ValidationError):
        SourceNoticeProfile.model_validate(profile.model_dump() | {"public_key": padded + " "})


@pytest.mark.parametrize(
    "mutation", ["issuer", "key", "mapping", "nested-source", "constructed", "delivery"]
)
def test_constructed_and_copied_models_are_deeply_revalidated_before_database(
    profile, signing_key, forbid_database, mutation
):
    delivery = signed(signing_key, profile)
    code = "invalid_source_notice_profile"
    if mutation == "issuer":
        profile = profile.model_copy(update={"issuer": f" {SENSITIVE}"})
    elif mutation == "key":
        profile = profile.model_copy(update={"public_key": SENSITIVE})
    elif mutation == "mapping":
        profile = profile.model_copy(update={"principal_id": SENSITIVE})
    elif mutation == "nested-source":
        source = SourceIdentity.model_construct(**(SOURCE | {"source_subject": "\x00"}))
        profile = profile.model_copy(update={"source": source})
    elif mutation == "constructed":
        profile = SourceNoticeProfile.model_construct(
            **(profile.model_dump() | {"tenant_id": SENSITIVE})
        )
    else:
        delivery = SignedSourceNoticeDelivery.model_construct(token=SENSITIVE + "\ud800")
        code = "invalid_signed_source_notice"
    with pytest.raises(AdminError, match=f"^{code}$") as failure:
        verify_source_notice(profile, delivery)
    assert not failure.value.outcome_unknown and SENSITIVE not in str(failure.value)
    with pytest.raises(AdminError, match=f"^{code}$"):
        with receive_source_notice(
            UNAVAILABLE_URL, profile, delivery, expected_access_epoch=1
        ):
            pytest.fail("Invalid constructed model reached the database")


@pytest.mark.parametrize("epoch", [None, True, False, "1", 1.0, 0, -1, MAX_EPOCH + 1])
def test_invalid_epoch_is_rejected_before_database(
    profile, signing_key, forbid_database, epoch
):
    with pytest.raises(AdminError, match="^invalid_source_access_request$"):
        with receive_source_notice(
            UNAVAILABLE_URL, profile, signed(signing_key, profile), expected_access_epoch=epoch
        ):
            pytest.fail("Invalid epoch was accepted")


@pytest.mark.parametrize("mutation", ["payload", "signature", "wrong-key", "hs256", "none"])
def test_tampering_and_algorithm_confusion_are_rejected_before_database(
    profile, signing_key, unrelated_key, forbid_database, mutation
):
    delivery = signed(signing_key, profile)
    header, payload, signature = delivery.token.split(".")
    if mutation == "payload":
        payload = b64(json_bytes(claims_for(profile, deny_notice(sequence=2))))
        token = f"{header}.{payload}.{signature}"
    elif mutation == "signature":
        signature = ("A" if signature[0] != "A" else "B") + signature[1:]
        token = f"{header}.{payload}.{signature}"
    elif mutation == "wrong-key":
        token = signed(unrelated_key, profile).token
    elif mutation == "hs256":
        token = jwt.encode(
            claims_for(profile), "synthetic-hmac-key-" * 4, algorithm="HS256",
            headers={"typ": "pgag-source-notice+jwt", "kid": profile.key_id},
        )
    else:
        token = jwt.encode(
            claims_for(profile), "", algorithm="none",
            headers={"typ": "pgag-source-notice+jwt", "kid": profile.key_id},
        )
    assert_invalid_delivery(profile, SignedSourceNoticeDelivery.model_construct(token=token))


@pytest.mark.parametrize(
    "changes",
    [
        {"alg": "HS256"}, {"alg": "none"}, {"alg": "PS256"}, {"alg": "rs256"},
        {"typ": "JWT"}, {"typ": "pgag-source-notice+jwt "},
        {"kid": "unconfigured-key"}, {"kid": ["synthetic-key-1"]},
        {"jku": "https://not-contacted.invalid/DO_NOT_ECHO"},
        {"jwk": {"kty": "RSA", "n": "DO_NOT_ECHO"}},
        {"crit": []}, {"b64": True}, {"x5u": "https://not-contacted.invalid"},
        {"extra": SENSITIVE},
    ],
)
def test_header_is_exact_and_cannot_select_remote_or_embedded_keys(
    profile, signing_key, forbid_database, changes
):
    assert_invalid_delivery(
        profile, signed(signing_key, profile, header=header_for(profile, **changes))
    )


@pytest.mark.parametrize("field", ["alg", "typ", "kid"])
def test_all_header_fields_are_required(profile, signing_key, forbid_database, field):
    header = header_for(profile)
    del header[field]
    assert_invalid_delivery(profile, signed(signing_key, profile, header=header))


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "other-signer"}, {"iss": "synthetic-local-notifier "},
        {"aud": ["synthetic-memory-ingress"]}, {"aud": []},
        {"aud": "other-ingress"}, {"aud": 1}, {"sub": "other-subject"},
        {"sub": True}, {"jti": SENSITIVE}, {"nbf": 0},
        {"tenant_id": str(uuid4())}, {"scope_id": str(uuid4())},
        {"principal_id": str(uuid4())}, {"source": SOURCE},
        {"permissions": ["admin"]}, {"expected_access_epoch": 1},
    ],
)
def test_claims_are_exact_without_audience_arrays_or_message_controlled_routing(
    profile, signing_key, forbid_database, changes
):
    assert_invalid_delivery(
        profile, signed(signing_key, profile, claims=claims_for(profile, **changes))
    )


@pytest.mark.parametrize("field", ["iss", "aud", "sub", "iat", "exp", "notice"])
def test_all_claim_fields_are_required(profile, signing_key, forbid_database, field):
    claims = claims_for(profile)
    del claims[field]
    assert_invalid_delivery(profile, signed(signing_key, profile, claims=claims))


@pytest.mark.parametrize(
    "mutation", ["source_system", "dataset_id", "source_subject", "mapping", "sequence", "lease"]
)
def test_signed_notice_source_and_nested_model_are_checked_before_database(
    profile, signing_key, forbid_database, mutation
):
    claims = claims_for(profile)
    if mutation in SOURCE:
        claims["notice"]["source"][mutation] = "different-source"
    elif mutation == "mapping":
        claims["notice"]["principal_id"] = str(uuid4())
    elif mutation == "sequence":
        claims["notice"]["sequence"] = True
    else:
        claims["notice"]["valid_until"] = "2026-09-23T01:00:00Z"
    assert_invalid_delivery(profile, signed(signing_key, profile, claims=claims))


@pytest.mark.parametrize(
    ("iat_offset", "exp_offset", "valid"),
    [(-10, 290, True), (-10, 291, False), (60, 120, False),
     (-120, -60, False), (-60, -60, False), (-60, -61, False)],
)
def test_signed_lifetime_is_bounded_with_no_future_or_expired_acceptance(
    profile, signing_key, forbid_database, iat_offset, exp_offset, valid
):
    now = int(time.time())
    delivery = signed(
        signing_key, profile,
        claims=claims_for(profile, iat=now + iat_offset, exp=now + exp_offset),
    )
    if valid:
        assert verify_source_notice(profile, delivery).decision == "deny"
    else:
        assert_invalid_delivery(profile, delivery)


@pytest.mark.parametrize("field", ["iat", "exp"])
@pytest.mark.parametrize("value", [True, False, 1.5, "1", None, -1, 253402300800])
def test_signed_timestamps_are_strict_bounded_integers(
    profile, signing_key, forbid_database, field, value
):
    assert_invalid_delivery(
        profile, signed(signing_key, profile, claims=claims_for(profile, **{field: value}))
    )


@pytest.mark.parametrize("field", ["iat", "exp"])
@pytest.mark.parametrize("conversion", [float, str])
def test_current_timestamp_is_not_coerced_from_float_or_string(
    profile, signing_key, forbid_database, field, conversion
):
    claims = claims_for(profile)
    claims[field] = conversion(claims[field])
    assert_invalid_delivery(profile, signed(signing_key, profile, claims=claims))


@pytest.mark.parametrize(
    ("iat", "exp", "valid"),
    [
        (0, 300, True), (100, 101, True), (99, 100, False), (0, 0, False),
        (101, 102, False), (True, 101, False), (False, 101, False),
    ],
)
def test_pyjwt_time_boundary_has_zero_leeway(
    profile, signing_key, forbid_database, monkeypatch, iat, exp, valid
):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(100, tz=tz)

    monkeypatch.setattr(jwt.api_jwt, "datetime", FixedDatetime)
    delivery = signed(signing_key, profile, claims=claims_for(profile, iat=iat, exp=exp))
    if valid:
        assert verify_source_notice(profile, delivery).decision == "deny"
    else:
        assert_invalid_delivery(profile, delivery)


@pytest.mark.parametrize("location", ["header", "claims", "notice", "source"])
def test_even_identical_duplicate_json_keys_are_rejected_at_every_depth(
    profile, signing_key, forbid_database, location
):
    header = json_bytes(header_for(profile))
    payload = json_bytes(claims_for(profile))
    if location == "header":
        header = header.replace(b'"alg":"RS256"', b'"alg":"RS256","alg":"RS256"', 1)
    elif location == "claims":
        marker = json_bytes({"sub": profile.subject})[1:-1]
        payload = payload.replace(marker, marker + b"," + marker, 1)
    elif location == "notice":
        payload = payload.replace(b'"sequence":1', b'"sequence":1,"sequence":1', 1)
    else:
        marker = json_bytes({"dataset_id": SOURCE["dataset_id"]})[1:-1]
        payload = payload.replace(marker, marker + b"," + marker, 1)
    delivery = SignedSourceNoticeDelivery(token=signed_raw(signing_key, header, payload))
    assert_invalid_delivery(profile, delivery)


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity", b"1e999"])
@pytest.mark.parametrize("location", ["timestamp", "nested"])
def test_nonfinite_json_is_rejected_including_nested_notice_values(
    profile, signing_key, forbid_database, constant, location
):
    claims = claims_for(profile)
    if location == "timestamp":
        claims["iat"] = "NONFINITE_MARKER"
    else:
        claims["notice"]["acl_version"] = "NONFINITE_MARKER"
    payload = json_bytes(claims).replace(b'"NONFINITE_MARKER"', constant)
    delivery = SignedSourceNoticeDelivery(
        token=signed_raw(signing_key, json_bytes(header_for(profile)), payload)
    )
    assert_invalid_delivery(profile, delivery)


@pytest.mark.parametrize(
    "location", ["header-utf8", "payload-utf8", "header-array", "payload-array", "source-surrogate"]
)
def test_jwt_json_requires_utf8_objects_and_valid_nested_text(
    profile, signing_key, forbid_database, location
):
    header = json_bytes(header_for(profile))
    payload = json_bytes(claims_for(profile))
    if location == "header-utf8":
        header = b'{"alg":"RS256","typ":"\xff","kid":"synthetic-key-1"}'
    elif location == "payload-utf8":
        payload = b'{"iss":"\xff"}'
    elif location == "header-array":
        header = b"[]"
    elif location == "payload-array":
        payload = b"[]"
    else:
        claims = claims_for(profile)
        claims["notice"]["source"]["source_subject"] = "\ud800"
        payload = json_bytes(claims)
    delivery = SignedSourceNoticeDelivery(token=signed_raw(signing_key, header, payload))
    assert_invalid_delivery(profile, delivery)


@pytest.mark.parametrize(
    "mutation", ["leading", "trailing", "newline", "unicode", "padding", "alphabet",
                 "noncanonical-signature", "empty", "short", "long", "oversized", "non-string"]
)
def test_compact_jwt_text_and_base64_are_strict_without_trimming(
    profile, signing_key, forbid_database, mutation
):
    token = signed(signing_key, profile).token
    header, payload, signature = token.split(".")
    if mutation == "leading":
        token = " " + token
    elif mutation == "trailing":
        token += " "
    elif mutation == "newline":
        token += "\n"
    elif mutation == "unicode":
        token = "署" + token
    elif mutation == "padding":
        token = f"{header}=.{payload}.{signature}"
    elif mutation == "alphabet":
        token = f"{header}.{payload}.{signature[:-1]}+"
    elif mutation == "noncanonical-signature":
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        replacement = alphabet[alphabet.index(signature[-1]) + 1]
        token = f"{header}.{payload}.{signature[:-1]}{replacement}"
    elif mutation == "empty":
        token = f"{header}..{signature}"
    elif mutation == "short":
        token = "a.b.c"
    elif mutation == "long":
        token += ".extra"
    elif mutation == "oversized":
        token = "a" * (MAX_SIGNED_NOTICE_BYTES + 1)
    else:
        token = token.encode("ascii")
    assert_invalid_delivery(profile, SignedSourceNoticeDelivery.model_construct(token=token))


def token_at_limit(signing_key, profile):
    header = json_bytes(header_for(profile))
    payload = json_bytes(claims_for(profile))
    for header_padding in range(3):
        head = header + b" " * header_padding
        overhead = len(b64(head)) + 2 + 342
        payload_size = (MAX_SIGNED_NOTICE_BYTES - overhead) * 3 // 4
        for adjustment in range(-2, 3):
            padded = payload + b" " * (payload_size + adjustment - len(payload))
            if overhead + len(b64(padded)) == MAX_SIGNED_NOTICE_BYTES:
                return signed_raw(signing_key, head, padded)
    raise AssertionError("Unable to construct an exactly bounded synthetic JWT")


def test_exact_token_byte_limit_and_original_signed_bytes_are_accepted(
    profile, signing_key, forbid_database
):
    token = token_at_limit(signing_key, profile)
    assert len(token.encode("ascii")) == MAX_SIGNED_NOTICE_BYTES
    delivery = SignedSourceNoticeDelivery(token=token)
    assert verify_source_notice(profile, delivery).sequence == 1
    with pytest.raises(ValidationError):
        SignedSourceNoticeDelivery(token=token + "a")
    header, payload, signature = token.split(".")
    # Same decoded claims, different bytes: signature verification must not reserialize JSON.
    raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    changed = f"{header}.{b64(raw.rstrip())}.{signature}"
    assert_invalid_delivery(profile, SignedSourceNoticeDelivery(token=changed))


@pytest.mark.parametrize(
    "changes",
    [
        {"format": "other"},
        {"token": None},
        {"token": 17},
        {"token": True},
        {"profile": SENSITIVE},
        {"tenant_id": str(uuid4())},
    ],
)
def test_delivery_model_forbids_extra_fields_and_hides_inputs(profile, signing_key, changes):
    data = signed(signing_key, profile).model_dump() | changes
    with pytest.raises(ValidationError) as failure:
        SignedSourceNoticeDelivery.model_validate(data)
    assert "input_value=" not in str(failure.value)


def test_receiver_routes_once_holds_context_and_checks_envelope_at_receipt_only(
    profile, signing_key, monkeypatch, forbid_database
):
    calls = []
    lifecycle = []
    notice = deny_notice(sequence=1)
    access = SourceAccessResult(
        operation="apply",
        tenant_id=profile.tenant_id,
        scope_id=profile.scope_id,
        principal_id=profile.principal_id,
        source=IDENTITY,
        sequence=notice.sequence,
        decision="deny",
        reason="revoked",
        changed=True,
        replayed=False,
        access_epoch=2,
        permissions=[],
        effective_permissions=[],
        expires_at=None,
        evaluated_at=datetime.now(UTC),
    )

    @contextmanager
    def controlled(url, request):
        calls.append((url, request))
        lifecycle.append("enter")
        after_receipt = datetime.now(UTC) + timedelta(hours=1)

        class LaterDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return after_receipt.astimezone(tz)

        monkeypatch.setattr(jwt.api_jwt, "datetime", LaterDatetime)
        try:
            yield access
        finally:
            lifecycle.append("exit")

    monkeypatch.setattr(ingress, "source_access", controlled)
    with receive_source_notice(
        UNAVAILABLE_URL, profile, signed(signing_key, profile, notice),
        expected_access_epoch=MAX_EPOCH,
    ) as result:
        assert lifecycle == ["enter"]
        assert result.notification_signature_verified is True
        assert result.source_authorization_verified is False
        assert result.access == access
        assert len(calls) == 1
        url, request = calls[0]
        assert url == UNAVAILABLE_URL
        assert request.operation == "apply"
        assert request.tenant_id == profile.tenant_id
        assert request.scope_id == profile.scope_id
        assert request.principal_id == profile.principal_id
        assert request.notice == notice and request.source is None
        assert request.expected_access_epoch == MAX_EPOCH
    assert lifecycle == ["enter", "exit"] and len(calls) == 1


def test_receiver_preserves_unknown_outcome_without_retry(
    profile, signing_key, monkeypatch, forbid_database
):
    attempts = []
    original = AdminError("admin_database_unavailable", outcome_unknown=True)

    def failed(url, request):
        attempts.append(request)
        raise original

    monkeypatch.setattr(ingress, "source_access", failed)
    with pytest.raises(AdminError) as failure:
        with receive_source_notice(
            UNAVAILABLE_URL, profile, signed(signing_key, profile), expected_access_epoch=1
        ):
            pytest.fail("Unknown commit outcome was presented as success")
    assert failure.value is original and failure.value.outcome_unknown
    assert len(attempts) == 1


@pytest.mark.integration
@pytest.mark.parametrize("reason", ["revoked", "unavailable", "deleted"])
def test_signed_allow_then_explicit_deny_uses_existing_durable_access_coordinator(
    env, profile, signing_key, reason
):
    bound = bind(env)
    profile = bound_profile(profile, env)
    notice = allow_notice(env)
    allowed = receive(env, profile, signed(signing_key, profile, notice), bound.access_epoch)
    assert allowed.notification_signature_verified is True
    assert allowed.source_authorization_verified is False
    assert allowed.access.source_authorization_verified is False
    assert allowed.access.operation == "apply"
    assert allowed.access.changed and not allowed.access.replayed
    assert allowed.access.sequence == 1 and allowed.access.reason == "authorized"
    assert allowed.access.permissions == allowed.access.effective_permissions == ["read"]
    assert allowed.access.expires_at == notice.valid_until
    assert allowed.access.access_epoch == bound.access_epoch + 1
    denial = deny_notice(reason=reason)
    denied = receive(
        env, profile, signed(signing_key, profile, denial), allowed.access.access_epoch
    )
    assert denied.notification_signature_verified is True
    assert denied.source_authorization_verified is False
    assert denied.access.changed and not denied.access.replayed
    assert denied.access.sequence == 2 and denied.access.reason == reason
    assert denied.access.decision == "deny"
    assert denied.access.permissions == denied.access.effective_permissions == []
    assert denied.access.expires_at is None
    assert denied.access.access_epoch == allowed.access.access_epoch + 1
    assert not regular_access(env).membership_exists
    assert [(row["sequence"], row["reason"]) for row in event_rows(env)] == [
        (1, "authorized"), (2, reason)
    ]
    snapshot = fingerprint(env)
    replay = receive(env, profile, signed(signing_key, profile, denial), bound.access_epoch)
    assert replay.access.replayed and not replay.access.changed
    assert fingerprint(env) == snapshot


@pytest.mark.integration
@pytest.mark.parametrize("mapping", ["unbound", "principal", "scope", "tenant"])
def test_signed_ingress_never_creates_or_redirects_a_source_binding(
    env, profile, signing_key, mapping
):
    if mapping != "unbound":
        bind(env)
    changes = {
        "unbound": {},
        "principal": {"principal_id": env.principals[0]},
        "scope": {"scope_id": env.scopes[2]},
        "tenant": {"tenant_id": env.tenants[1]},
    }[mapping]
    profile = bound_profile(profile, env, **changes)
    before = fingerprint(env)
    with pytest.raises(AdminError, match="^not_found$"):
        receive(env, profile, signed(signing_key, profile, allow_notice(env)), epoch=1)
    assert fingerprint(env) == before


@pytest.mark.integration
def test_valid_signature_cannot_override_existing_different_source_binding(
    env, profile, signing_key
):
    bound = bind(env)
    other = SourceIdentity(**(SOURCE | {"source_subject": "other-synthetic-reader"}))
    profile = bound_profile(profile, env, source=other)
    notice = allow_notice(env, source=other)
    before = fingerprint(env)
    with pytest.raises(AdminError, match="^source_binding_conflict$"):
        receive(env, profile, signed(signing_key, profile, notice), bound.access_epoch)
    assert fingerprint(env) == before


@pytest.mark.integration
def test_signed_sequence_gap_is_durable_denial_and_not_an_ingress_retry(
    env, profile, signing_key
):
    bind(env)
    profile = bound_profile(profile, env)
    first = receive(env, profile, signed(signing_key, profile, allow_notice(env)))
    skipped = signed(signing_key, profile, allow_notice(env, sequence=3))
    denied = receive(env, profile, skipped, first.access.access_epoch)
    assert denied.access.changed and denied.access.sequence == 3
    assert denied.access.decision == "deny" and denied.access.reason == "sequence_gap"
    assert denied.access.effective_permissions == []
    assert not regular_access(env).membership_exists
    before = fingerprint(env)
    replay = receive(env, profile, skipped, first.access.access_epoch)
    assert replay.access.replayed and replay.access.reason == "sequence_gap"
    assert fingerprint(env) == before
    with pytest.raises(AdminError, match="^source_sequence_conflict$"):
        receive(env, profile, signed(signing_key, profile, allow_notice(env, sequence=2)))
    fresh = receive(env, profile, signed(signing_key, profile, allow_notice(env, sequence=4)))
    assert fresh.access.effective_permissions == ["read"]
    assert [(row["sequence"], row["reason"]) for row in event_rows(env)] == [
        (1, "authorized"), (3, "sequence_gap"), (4, "authorized")
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("verified_offset", "until_offset"),
    [(60, 120), (-120, -60), (-60, -60), (-1, 300)],
)
def test_current_signed_envelope_does_not_replace_database_clock_lease_validation(
    env, profile, signing_key, verified_offset, until_offset
):
    bind(env)
    profile = bound_profile(profile, env)
    first = receive(env, profile, signed(signing_key, profile, allow_notice(env)))
    now = database_now(env)
    notice = SourceNotice.model_validate(
        allow_notice(env, sequence=2).model_dump()
        | {
            "verified_at": now + timedelta(seconds=verified_offset),
            "valid_until": now + timedelta(seconds=until_offset),
        }
    )
    denied = receive(env, profile, signed(signing_key, profile, notice))
    assert denied.notification_signature_verified is True
    assert denied.access.reason == "invalid_lease" and denied.access.decision == "deny"
    assert denied.access.changed and denied.access.sequence == 2
    assert denied.access.access_epoch == first.access.access_epoch + 1
    assert denied.access.effective_permissions == [] and denied.access.expires_at is None
    assert not regular_access(env).membership_exists
    assert len(event_rows(env)) == 2


@pytest.mark.integration
def test_signed_deletion_is_terminal_but_does_not_purge_native_payload(
    env, profile, signing_key
):
    memory = env.observe(content="Synthetic source payload remains for Native maintenance").json()
    bind(env)
    profile = bound_profile(profile, env)
    receive(env, profile, signed(signing_key, profile, allow_notice(env)))
    deleted = receive(env, profile, signed(signing_key, profile, deny_notice(reason="deleted")))
    assert deleted.access.reason == "deleted" and deleted.access.effective_permissions == []
    before = fingerprint(env)
    with pytest.raises(AdminError, match="^source_deleted$"):
        receive(env, profile, signed(signing_key, profile, allow_notice(env, sequence=3)))
    assert fingerprint(env) == before
    assert not env.recall(2, scope_ids=[str(env.scopes[0])]).json()["items"]
    assert env.recall().json()["items"][0]["memory_id"] == memory["memory_id"]
    forgotten = env.client.post(
        "/v1/forget",
        json={"memory_ids": [memory["memory_id"]], "reason": "Independent Native maintenance"},
        headers=env.headers(),
    )
    assert forgotten.status_code == 202, forgotten.text
    assert execute(env).reason == "deleted"


@pytest.mark.integration
def test_signed_notice_epoch_conflict_is_not_automatically_retried(
    env, profile, signing_key
):
    bound = bind(env)
    profile = bound_profile(profile, env)
    current = regular_access(
        env, "set", scope_id=env.scopes[2], expected_access_epoch=bound.access_epoch,
        permissions=("read", "write"), no_expiry=True,
    )
    delivery = signed(signing_key, profile, allow_notice(env))
    before = fingerprint(env)
    with pytest.raises(AdminError, match="^access_epoch_conflict$"):
        receive(env, profile, delivery, bound.access_epoch)
    assert fingerprint(env) == before and not event_rows(env)
    accepted = receive(env, profile, delivery, current.access_epoch)
    assert accepted.access.effective_permissions == ["read"] and len(event_rows(env)) == 1


@pytest.mark.integration
def test_freshly_resigned_latest_notice_preserves_digest_state_epoch_and_lease(
    env, profile, signing_key
):
    bound = bind(env)
    profile = bound_profile(profile, env)
    notice = allow_notice(env)
    now = int(time.time())
    original = signed(
        signing_key, profile, claims=claims_for(profile, notice, iat=now - 60, exp=now + 60)
    )
    first = receive(env, profile, original, bound.access_epoch)
    before = fingerprint(env)
    fresh = signed(
        signing_key, profile, claims=claims_for(profile, notice, iat=now - 1, exp=now + 180)
    )
    assert original.token != fresh.token
    for delivery in (original, fresh):
        replay = receive(env, profile, delivery, bound.access_epoch)
        assert replay.access.replayed and not replay.access.changed
        assert replay.access.expires_at == first.access.expires_at == notice.valid_until
        assert replay.access.access_epoch == first.access.access_epoch
        assert fingerprint(env) == before
    assert len(event_rows(env)) == 1
    altered_notice = notice.model_copy(
        update={"valid_until": notice.valid_until + timedelta(seconds=1)}
    )
    with pytest.raises(AdminError, match="^source_notice_conflict$"):
        receive(env, profile, signed(signing_key, profile, altered_notice), bound.access_epoch)
    assert fingerprint(env) == before
    regular_access(env, "revoke", expected_access_epoch=first.access.access_epoch)
    revoked = fingerprint(env)
    replay = receive(env, profile, fresh, bound.access_epoch)
    assert replay.access.replayed and not replay.access.changed
    assert replay.access.permissions == replay.access.effective_permissions == []
    assert replay.access.expires_at is None and fingerprint(env) == revoked


@pytest.mark.integration
def test_expired_envelope_cannot_bypass_verification_as_latest_notice_replay(
    env, profile, signing_key, monkeypatch
):
    bound = bind(env)
    profile = bound_profile(profile, env)
    notice = allow_notice(env)
    first = receive(env, profile, signed(signing_key, profile, notice), bound.access_epoch)
    now = int(time.time())
    expired = signed(
        signing_key, profile, claims=claims_for(profile, notice, iat=now - 180, exp=now - 60)
    )
    before = fingerprint(env)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        pytest.fail("An expired signed replay attempted a database connection")

    with monkeypatch.context() as patch:
        patch.setattr(psycopg, "connect", forbidden)
        with pytest.raises(AdminError, match="^invalid_signed_source_notice$"):
            receive(env, profile, expired, bound.access_epoch)
    assert not calls and fingerprint(env) == before
    assert execute(env).expires_at == first.access.expires_at


@pytest.mark.integration
def test_resigning_does_not_renew_an_expired_database_lease(env, profile, signing_key):
    bind(env)
    profile = bound_profile(profile, env)
    now = database_now(env)
    notice = SourceNotice.model_validate(
        allow_notice(env).model_dump()
        | {"verified_at": now, "valid_until": now + timedelta(seconds=2)}
    )
    first = receive(env, profile, signed(signing_key, profile, notice))
    assert first.access.effective_permissions == ["read"]
    deadline = time.monotonic() + 8
    while database_now(env) < notice.valid_until:
        assert time.monotonic() < deadline, "Database clock did not reach the source lease"
        time.sleep(0.05)
    before = fingerprint(env)
    replay = receive(env, profile, signed(signing_key, profile, notice))
    assert replay.access.replayed and not replay.access.changed
    assert replay.access.expires_at == notice.valid_until
    assert replay.access.effective_permissions == [] and fingerprint(env) == before


@pytest.mark.integration
def test_signed_ingress_preserves_uncertain_actual_commit_without_retry(
    env, profile, signing_key, monkeypatch
):
    bound = bind(env)
    profile = bound_profile(profile, env)
    delivery = signed(signing_key, profile, allow_notice(env))
    transaction = psycopg.Connection.transaction
    commits = []

    @contextmanager
    def lost_response(conn, *args, **kwargs):
        with transaction(conn, *args, **kwargs) as result:
            yield result
        commits.append(True)
        raise psycopg.OperationalError(SENSITIVE + " after actual commit")

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "transaction", lost_response)
        with pytest.raises(AdminError, match="^admin_database_unavailable$") as failure:
            receive(env, profile, delivery, bound.access_epoch)
        assert failure.value.outcome_unknown and SENSITIVE not in str(failure.value)
    assert len(commits) == 1 and len(event_rows(env)) == 1
    assert execute(env).effective_permissions == ["read"]
    before = fingerprint(env)
    replay = receive(env, profile, delivery, bound.access_epoch)
    assert replay.access.replayed and not replay.access.changed
    assert fingerprint(env) == before


@pytest.mark.integration
def test_signed_result_delivery_retains_tenant_barrier_after_commit(
    env, profile, signing_key
):
    bound = bind(env)
    profile = bound_profile(profile, env)
    with pytest.raises(RuntimeError, match="consumer lost output"):
        with receive_source_notice(
            env.admin_url, profile, signed(signing_key, profile, allow_notice(env)),
            expected_access_epoch=bound.access_epoch,
        ) as result:
            assert result.notification_signature_verified and result.access.changed
            with psycopg.connect(env.admin_url, autocommit=True) as conn:
                assert conn.execute(
                    """SELECT sequence,decision FROM memory_ops.source_access_state
                       WHERE tenant_id=%s AND scope_id=%s AND principal_id=%s""",
                    (env.tenants[0], env.scopes[0], env.principals[2]),
                ).fetchone() == (1, "allow")
                assert not conn.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (str(env.tenants[0]),)
                ).fetchone()[0]
                assert conn.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (str(env.tenants[1]),)
                ).fetchone()[0]
            raise RuntimeError("consumer lost output")
    assert execute(env).effective_permissions == ["read"] and len(event_rows(env)) == 1


@pytest.mark.integration
def test_real_http_reader_loses_signed_revoked_source_but_keeps_task_and_maintenance(
    env, api_process, profile, signing_key
):
    source = env.observe(content="PRIVATE_SIGNED_SOURCE Gold").json()["memory_id"]
    own = env.observe(index=2, content="Independent writable task context").json()["memory_id"]
    checkpoint = env.client.post(
        "/v1/checkpoints",
        headers=env.headers(),
        json={
            "scope_id": str(env.scopes[0]),
            "run_id": str(uuid4()), "branch_id": str(uuid4()), "expected_head": None,
            "harness_id": "signed-source-test", "harness_version": "1",
            "event_watermark": 1, "state": {"goal": "PRIVATE_SIGNED_SOURCE"},
            "memory_refs": [{"memory_id": source}],
        },
    )
    assert checkpoint.status_code == 201, checkpoint.text
    checkpoint_path = "/v1/checkpoints/" + checkpoint.json()["checkpoint_id"]
    bind(env)
    profile = bound_profile(profile, env)
    with api_process("source-notice-http.log") as (http, _):
        recall_body = {
            "scope_ids": [str(env.scopes[0]), str(env.scopes[2])],
            "purpose": "signed-source-test", "query": "", "token_budget": 8000,
        }
        allowed = receive(env, profile, signed(signing_key, profile, allow_notice(env)))
        visible = http.post("/v1/recall", json=recall_body, headers=env.headers(2))
        assert visible.status_code == 200, visible.text
        assert {item["memory_id"] for item in visible.json()["items"]} == {source, own}
        assert http.get(checkpoint_path, headers=env.headers(2)).status_code == 200
        receive(
            env, profile, signed(signing_key, profile, deny_notice()), allowed.access.access_epoch
        )
        hidden = http.post("/v1/recall", json=recall_body, headers=env.headers(2))
        assert hidden.status_code == 200
        assert {item["memory_id"] for item in hidden.json()["items"]} == {own}
        assert "PRIVATE_SIGNED_SOURCE" not in hidden.text
        assert http.get(checkpoint_path, headers=env.headers(2)).status_code == 404
        assert http.get(checkpoint_path, headers=env.headers()).status_code == 200
        assert http.post(
            "/v1/explain", json={"memory_id": source}, headers=env.headers(2)
        ).status_code == 404
        assert http.post(
            "/v1/explain", json={"memory_id": source}, headers=env.headers()
        ).status_code == 200
        assert http.post(
            "/v1/source-notice", json={"token": "synthetic"}, headers=env.headers(2)
        ).status_code == 404
        fresh = http.post(
            "/v1/observe",
            headers=env.headers(2),
            json={
                "scope_id": str(env.scopes[2]), "source_namespace": "signed-source-test",
                "source_event_id": str(uuid4()), "occurred_at": "2026-09-23T00:00:00Z",
                "content": "Own task remains writable", "consent_reference": "synthetic-consent",
            },
        )
        assert fresh.status_code == 201, fresh.text
        forgotten = http.post(
            "/v1/forget",
            json={"memory_ids": [source], "reason": "Independent Native maintenance"},
            headers=env.headers(),
        )
        assert forgotten.status_code == 202, forgotten.text
        assert execute(env).reason == "revoked"


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["apply"],
        ["unknown-DO_NOT_ECHO"],
        ["apply", "--tenant-id", "DO_NOT_ECHO"],
        ["apply", "--profile", "DO_NOT_ECHO"],
        ["apply", "--expected-access-epoch", "DO_NOT_ECHO"],
        ["apply", "--public-key", "DO_NOT_ECHO"],
        ["apply", "--profile", "DO_NOT_ECHO", "--delivery-file", "DO_NOT_ECHO",
         "--expected-access-epoch", "1", "--issuer", "DO_NOT_ECHO"],
    ],
)
def test_cli_argument_errors_are_redacted(arguments):
    assert_safe_cli_failure(
        cli(*arguments, url=UNAVAILABLE_URL), "invalid_source_notice_arguments", exit_code=2
    )


@pytest.mark.parametrize("which", ["profile", "delivery"])
@pytest.mark.parametrize(
    "kind", ["oversized", "invalid-json", "invalid-utf8", "duplicate", "nonfinite",
             "missing", "directory", "fifo", "symlink"]
)
def test_cli_files_are_bounded_regular_strict_json_and_errors_are_redacted(
    profile, signing_key, input_files, which, kind
):
    write_inputs(input_files, profile, signed(signing_key, profile))
    path = input_files[which]
    data = path.read_bytes()
    if kind == "oversized":
        limit = MAX_SOURCE_PROFILE_BYTES if which == "profile" else MAX_SOURCE_DELIVERY_BYTES
        path.write_bytes(data + b" " * (limit + 1 - len(data)))
    elif kind == "invalid-json":
        path.write_text('{"DO_NOT_ECHO":', encoding="utf-8")
    elif kind == "invalid-utf8":
        path.write_bytes(b"\xffDO_NOT_ECHO")
    elif kind == "duplicate":
        key = "issuer" if which == "profile" else "token"
        value = json.loads(data)[key]
        duplicate = json_bytes({key: value})[1:-1]
        path.write_bytes(data[:-1] + b"," + duplicate + b"}")
    elif kind == "nonfinite":
        path.write_bytes(data[:-1] + b',"DO_NOT_ECHO":NaN}')
    else:
        path.unlink()
        if kind == "directory":
            path.mkdir()
        elif kind == "fifo":
            os.mkfifo(path)
        elif kind == "symlink":
            input_files["target"].write_bytes(data)
            path.symlink_to(input_files["target"].name)
    result = cli(*cli_arguments(input_files), url=UNAVAILABLE_URL)
    assert_safe_cli_failure(result, "invalid_source_notice_arguments", exit_code=2)
    assert "admin_database_unavailable" not in result.stdout + result.stderr


def test_cli_nested_profile_duplicates_are_rejected(profile, signing_key, input_files):
    write_inputs(input_files, profile, signed(signing_key, profile))
    path = input_files["profile"]
    payload = path.read_bytes()
    marker = json_bytes({"dataset_id": SOURCE["dataset_id"]})[1:-1]
    assert marker in payload
    path.write_bytes(payload.replace(marker, marker + b"," + marker, 1))
    assert_safe_cli_failure(
        cli(*cli_arguments(input_files), url=UNAVAILABLE_URL),
        "invalid_source_notice_arguments", exit_code=2,
    )


def test_cli_accepts_exact_profile_and_delivery_file_byte_limits(
    profile, signing_key, input_files
):
    write_inputs(input_files, profile, signed(signing_key, profile))
    for which, limit in (
        ("profile", MAX_SOURCE_PROFILE_BYTES), ("delivery", MAX_SOURCE_DELIVERY_BYTES)
    ):
        path = input_files[which]
        data = path.read_bytes()
        path.write_bytes(data + b" " * (limit - len(data)))
        assert path.stat().st_size == limit
    result = cli(*cli_arguments(input_files), url=UNAVAILABLE_URL)
    assert_safe_cli_failure(result, "admin_database_unavailable")
    assert json.loads(result.stdout)["error"]["outcome_unknown"] is False


@pytest.mark.parametrize("mutation", ["wrong-key", "source", "audience", "extra-header", "expired"])
def test_cli_loaded_but_unauthentic_delivery_returns_safe_admin_json(
    profile, signing_key, unrelated_key, input_files, mutation
):
    claims = claims_for(profile)
    header = header_for(profile)
    key = signing_key
    if mutation == "wrong-key":
        key = unrelated_key
    elif mutation == "source":
        claims["notice"]["source"]["source_subject"] = SENSITIVE
    elif mutation == "audience":
        claims["aud"] = [profile.audience]
    elif mutation == "extra-header":
        header["jku"] = "https://not-contacted.invalid/DO_NOT_ECHO"
    else:
        claims["iat"] -= 300
        claims["exp"] -= 300
    delivery = signed(key, profile, claims=claims, header=header)
    write_inputs(input_files, profile, delivery)
    result = cli(*cli_arguments(input_files), url=UNAVAILABLE_URL)
    assert_safe_cli_failure(result, "invalid_signed_source_notice")
    assert delivery.token not in result.stdout + result.stderr
    assert json.loads(result.stdout)["error"]["outcome_unknown"] is False


def test_cli_requires_admin_database_url_after_valid_loading(profile, signing_key, input_files):
    write_inputs(input_files, profile, signed(signing_key, profile))
    result = cli(*cli_arguments(input_files))
    assert result.returncode == 2 and result.stdout == ""
    assert "PGAG_ADMIN_DATABASE_URL" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_help_does_not_require_httpx_or_sdk():
    script = """
import importlib.abc
import sys

class NoOptionalClients(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "httpx" or fullname.startswith(("httpx.", "pg_agmemory.sdk")):
            raise AssertionError("signed source ingress imported an optional HTTP client")

sys.meta_path.insert(0, NoOptionalClients())
from pg_agmemory.cli import main
sys.argv = ["pg-agmemory", "source-notice", "--help"]
main()
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
    assert "--profile" in result.stdout and "--delivery-file" in result.stdout
    assert "--expected-access-epoch" in result.stdout


def test_cli_unknown_commit_outcome_remains_explicit_without_retry(
    profile, signing_key, input_files, monkeypatch, capsys
):
    write_inputs(input_files, profile, signed(signing_key, profile))
    attempts = []

    def failed(url, profile, delivery, *, expected_access_epoch):
        attempts.append(expected_access_epoch)
        raise AdminError("admin_database_unavailable", outcome_unknown=True)

    monkeypatch.setattr(ingress, "receive_source_notice", failed)
    monkeypatch.setenv("PGAG_ADMIN_DATABASE_URL", UNAVAILABLE_URL)
    with pytest.raises(SystemExit) as exited:
        ingress.main(cli_arguments(input_files))
    output = capsys.readouterr()
    assert exited.value.code == 1 and output.err == ""
    assert attempts == [1]
    assert json.loads(output.out) == {
        "error": {"code": "admin_database_unavailable", "outcome_unknown": True}
    }
    assert "DO_NOT_ECHO" not in output.out and "BEGIN PUBLIC KEY" not in output.out


@pytest.mark.integration
@pytest.mark.parametrize("outcome", ["allow", "deny", "gap", "invalid-lease"])
def test_actual_signed_cli_returns_access_result_and_correct_exit_semantics(
    env, profile, signing_key, input_files, outcome
):
    bound = bind(env)
    profile = bound_profile(profile, env)
    if outcome == "deny":
        notice = deny_notice(sequence=1)
    elif outcome == "gap":
        notice = allow_notice(env, sequence=2)
    elif outcome == "invalid-lease":
        now = database_now(env)
        notice = SourceNotice.model_validate(
            allow_notice(env).model_dump()
            | {
                "verified_at": now - timedelta(seconds=120),
                "valid_until": now - timedelta(seconds=60),
            }
        )
    else:
        notice = allow_notice(env)
    delivery = signed(signing_key, profile, notice)
    write_inputs(input_files, profile, delivery)
    arguments = cli_arguments(input_files, bound.access_epoch)
    result = cli(*arguments, url=env.admin_url)
    assert result.returncode == (1 if outcome in ("gap", "invalid-lease") else 0)
    assert result.stderr == "", result.stderr
    payload = json.loads(result.stdout)
    assert payload["notification_signature_verified"] is True
    assert payload["source_authorization_verified"] is False
    assert payload["access"]["source_authorization_verified"] is False
    assert payload["access"]["reason"] == {
        "allow": "authorized", "deny": "revoked", "gap": "sequence_gap",
        "invalid-lease": "invalid_lease",
    }[outcome]
    assert payload["access"]["effective_permissions"] == (["read"] if outcome == "allow" else [])
    assert payload["access"]["changed"] and not payload["access"]["replayed"]
    assert delivery.token not in result.stdout
    assert "BEGIN PUBLIC KEY" not in result.stdout and env.admin_url not in result.stdout
    before = fingerprint(env)
    replay = cli(*arguments, url=env.admin_url)
    assert replay.returncode == result.returncode and replay.stderr == ""
    replayed = json.loads(replay.stdout)
    assert replayed["access"]["replayed"] and not replayed["access"]["changed"]
    assert replayed["access"]["expires_at"] == payload["access"]["expires_at"]
    assert fingerprint(env) == before


@pytest.mark.integration
def test_actual_signed_cli_reports_epoch_and_mapping_errors_without_changes(
    env, profile, signing_key, input_files
):
    bound = bind(env)
    profile = bound_profile(profile, env)
    write_inputs(input_files, profile, signed(signing_key, profile, allow_notice(env)))
    allowed = cli(*cli_arguments(input_files, bound.access_epoch), url=env.admin_url)
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    write_inputs(input_files, profile, signed(signing_key, profile, deny_notice()))
    before = fingerprint(env)
    conflict = cli(*cli_arguments(input_files, bound.access_epoch), url=env.admin_url)
    assert_safe_cli_failure(conflict, "access_epoch_conflict")
    assert json.loads(conflict.stdout)["error"]["outcome_unknown"] is False
    assert fingerprint(env) == before
    changed = bound_profile(profile, env, principal_id=env.principals[0])
    write_inputs(input_files, changed, signed(signing_key, changed, deny_notice()))
    absent = cli(
        *cli_arguments(input_files, json.loads(allowed.stdout)["access"]["access_epoch"]),
        url=env.admin_url,
    )
    assert_safe_cli_failure(absent, "not_found")
    assert fingerprint(env) == before
