"""Real local libpq TLS handshakes; no Azure service or SQL-function qualification."""

import asyncio
import socket
import ssl
import struct
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import psycopg
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from psycopg.conninfo import make_conninfo

from pg_agmemory.azure_inference import AzureAIProvider
from pg_agmemory.providers import ProviderFailure, ProviderSettings

HOST = "provider.test.invalid"
SSL_REQUEST = struct.pack("!II", 8, 80877103)


def certificate(subject, key, *, issuer=None, issuer_key=None, ca=False):
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer.subject if issuer else name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=0 if ca else None), critical=True)
    )
    if not ca:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False
        ).add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    return builder.sign(issuer_key or key, hashes.SHA256())


def receive(sock, size):
    data = bytearray()
    while len(data) < size:
        part = sock.recv(size - len(data))
        if not part:
            raise EOFError("Local PostgreSQL TLS fixture received an incomplete message")
        data.extend(part)
    return bytes(data)


def message(kind, payload):
    return kind + struct.pack("!I", len(payload) + 4) + payload


@pytest.fixture
def tls_server(tmp_path, monkeypatch):
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca = certificate("Ephemeral provider test CA", ca_key, ca=True)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_ca = certificate("Untrusted provider test CA", other_key, ca=True)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_cert = certificate(HOST, server_key, issuer=ca, issuer_key=ca_key)
    for name, cert in (("ca.pem", ca), ("other-ca.pem", other_ca), ("server.pem", server_cert)):
        (tmp_path / name).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path = tmp_path / "server.key"
    key_path.touch(mode=0o600)
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    empty_certs = tmp_path / "empty-certs"
    empty_certs.mkdir()
    monkeypatch.setenv("SSL_CERT_DIR", str(empty_certs))
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "ca.pem"))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(tmp_path / "server.pem", key_path)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(5)
    state = SimpleNamespace(
        port=listener.getsockname()[1],
        ca=tmp_path / "ca.pem",
        untrusted=tmp_path / "other-ca.pem",
        missing=tmp_path / "missing-ca.pem",
        ssl_request=None,
        startup=None,
        terminated=False,
        tls_error=None,
        errors=[],
    )

    def serve():
        try:
            with listener.accept()[0] as transport:
                transport.settimeout(5)
                state.ssl_request = receive(transport, 8)
                assert state.ssl_request == SSL_REQUEST
                transport.sendall(b"S")
                try:
                    secured = context.wrap_socket(transport, server_side=True)
                except (ssl.SSLError, ConnectionResetError) as exc:
                    state.tls_error = exc
                    return
                with secured:
                    length = struct.unpack("!I", receive(secured, 4))[0]
                    assert 8 <= length <= 4096
                    state.startup = receive(secured, length - 4)
                    assert state.startup[:4] == struct.pack("!I", 196608)
                    secured.sendall(
                        message(b"R", struct.pack("!I", 0))
                        + message(b"S", b"client_encoding\0UTF8\0")
                        + message(b"S", b"server_version\x0018.6\0")
                        + message(b"Z", b"I")
                    )
                    assert receive(secured, 5) == message(b"X", b"")
                    state.terminated = True
        except (EOFError, ssl.SSLError, ConnectionResetError) as exc:
            if state.ssl_request == SSL_REQUEST and state.startup is None:
                state.tls_error = exc
            else:
                state.errors.append(exc)
        except Exception as exc:
            # Exceptions in the helper thread must fail the owning test, not disappear.
            state.errors.append(exc)

    worker = threading.Thread(target=serve, daemon=True, name="provider-local-tls")
    worker.start()
    try:
        yield state
    finally:
        worker.join(timeout=7)
        listener.close()
        assert not worker.is_alive(), "Local TLS fixture thread did not stop within its bound"
        assert not state.errors, state.errors
        assert state.ssl_request == SSL_REQUEST
        if state.startup is None:
            assert state.tls_error is not None, "Expected an explicit rejected TLS handshake"
        else:
            assert state.terminated, "Client did not close its local TLS connection"


def provider(monkeypatch, server, *, root=None, host=HOST):
    parameters = {
        "host": host,
        "hostaddr": "127.0.0.1",
        "port": server.port,
        "dbname": "synthetic",
        "user": "synthetic",
        "gssencmode": "disable",
        "sslmode": "require",
    }
    if root is not None:
        parameters["sslrootcert"] = str(root)
    monkeypatch.setenv("PGAG_LOCAL_TLS_DSN", make_conninfo(**parameters))
    return AzureAIProvider(
        ProviderSettings.model_validate(
            {
                "backend": "azure_ai",
                "database_url_env": "PGAG_LOCAL_TLS_DSN",
                "azure_product": "flexible_server",
                "azure_extension_version": "2.0.1",
                "embedding_model": {"name": "synthetic", "revision": "1"},
                "timeout_seconds": 5,
            }
        )
    )


async def connect_and_close(client, expected_root):
    conn = await client.connect()
    try:
        assert conn.autocommit
        assert conn.info.server_version == 180006
        assert conn.info.get_parameters()["sslmode"] == "verify-full"
        assert conn.info.get_parameters()["sslrootcert"] == str(expected_root)
    finally:
        await conn.close()
    assert conn.closed


def test_system_trust_uses_ssl_cert_file_for_real_libpq_handshake(tls_server, monkeypatch):
    asyncio.run(connect_and_close(provider(monkeypatch, tls_server), "system"))


def test_explicit_dsn_ca_is_independent_of_process_trust(tls_server, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", str(tls_server.untrusted))
    asyncio.run(
        connect_and_close(provider(monkeypatch, tls_server, root=tls_server.ca), tls_server.ca)
    )


@pytest.mark.parametrize("trust", ["missing", "untrusted"])
@pytest.mark.parametrize("root_source", ["system", "explicit"])
def test_missing_or_untrusted_ca_cannot_downgrade_to_require(
    tls_server, monkeypatch, trust, root_source
):
    root = getattr(tls_server, trust)
    monkeypatch.setenv("SSL_CERT_FILE", str(root))
    client = provider(monkeypatch, tls_server, root=root if root_source == "explicit" else None)
    with pytest.raises(psycopg.OperationalError, match="certificate"):
        asyncio.run(client.connect())
    assert tls_server.startup is None


@pytest.mark.parametrize("root_source", ["system", "explicit"])
def test_trusted_ca_still_requires_matching_hostname(tls_server, monkeypatch, root_source):
    client = provider(
        monkeypatch,
        tls_server,
        root=tls_server.ca if root_source == "explicit" else None,
        host="wrong.test.invalid",
    )
    with pytest.raises(psycopg.OperationalError, match="does not match host name"):
        asyncio.run(client.connect())
    assert tls_server.startup is None


def test_connection_context_sanitizes_real_tls_failure(tls_server, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", str(tls_server.untrusted))
    client = provider(monkeypatch, tls_server)

    async def attempt():
        async with client.connection():
            pytest.fail("An untrusted local server must not yield a connection")

    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(attempt())
    assert str(failure.value) == "provider_unavailable"
    assert failure.value.error.model_dump() == {
        "code": "provider_unavailable",
        "retryable": True,
        "billing_unknown": False,
    }
    assert tls_server.startup is None
