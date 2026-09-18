"""Azure contracts tested with mocks and SYNTHETIC SQL, never a live Azure extension."""

import asyncio
import json
from copy import deepcopy
from dataclasses import dataclass
from unittest.mock import AsyncMock

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from pg_agmemory.azure_inference import CATALOG_QUERY, AzureAIProvider
from pg_agmemory.providers import (
    MAX_PROVIDER_RESPONSE_BYTES,
    InferenceInput,
    ProviderFailure,
    ProviderSettings,
)

VERSION = "synthetic-test-1"
PRODUCTS = ("flexible_server", "horizondb")
SOURCE = InferenceInput(text=" \nDo not approve deployment; approval remains uncertain.\n ")
VECTOR = [1.0] + [0.0] * 767
INJECTION = "synthetic'); DROP SCHEMA memory CASCADE; --"


def settings(**changes):
    return ProviderSettings.model_validate(
        {
            "backend": "azure_ai",
            "database_url_env": "PGAG_SYNTHETIC_AZURE_DSN",
            "azure_product": "flexible_server",
            "azure_extension_version": VERSION,
            "azure_summary_mode": "generate",
            "text_model": {"name": "synthetic-summary", "revision": "1"},
            "embedding_model": {"name": "synthetic-embedding", "revision": "1"},
            **changes,
        }
    )


def language_settings(**changes):
    return settings(
        **{
            "azure_summary_mode": "language",
            "text_model": {"name": "azure_cognitive.summarize_abstractive", "revision": "1"},
            "language": "ja",
            **changes,
        }
    )


def catalog_rows(product="flexible_server", generate_type="text"):
    return [
        {
            "schema": "azure_openai",
            "name": "create_embeddings",
            "names": [
                "deployment_name" if product == "flexible_server" else "model",
                "input",
                "dimensions",
                "timeout_ms",
                "throw_on_error",
                "max_attempts",
            ],
            "types": ["text", "text", "integer", "integer", "boolean", "integer"],
            "required": 2,
            "result": "real[]",
            "allowed": True,
        },
        {
            "schema": "azure_ai",
            "name": "generate",
            "names": ["prompt", "model", "json_schema", "system_prompt"],
            "types": ["text", "text", "jsonb", "text"],
            "required": 2,
            "result": generate_type,
            "allowed": True,
        },
        {
            "schema": "azure_cognitive",
            "name": "summarize_abstractive",
            "names": [
                "text",
                "language",
                "sentence_count",
                "disable_service_logs",
                "timeout_ms",
                "throw_on_error",
                "max_attempts",
            ],
            "types": ["text", "text", "integer", "boolean", "integer", "boolean", "integer"],
            "required": 1,
            "result": "text[]",
            "allowed": True,
        },
    ]


class Cursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.rows


class MockConnection:
    def __init__(self, *, result=None, rows=None, privileged=False, version=VERSION, failure=None):
        self.result = result
        self.rows = catalog_rows() if rows is None else rows
        self.privileged = privileged
        self.version = version
        self.failure = failure
        self.calls = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def execute(self, statement, params=None):
        self.calls.append((statement, params))
        if statement.startswith("SET "):
            return Cursor()
        if "SELECT rolsuper" in statement:
            return Cursor(row={"privileged": self.privileged})
        if statement.startswith("SELECT extversion"):
            return Cursor(row=None if self.version is None else {"extversion": self.version})
        if statement == CATALOG_QUERY:
            return Cursor(rows=self.rows)
        assert statement.startswith("WITH inference AS MATERIALIZED (")
        if self.failure:
            raise self.failure
        return Cursor(row={"result": self.result})

    @property
    def inference_calls(self):
        return [call for call in self.calls if call[0].startswith("WITH inference")]


def mock_provider(monkeypatch, config=None, **connection_kwargs):
    conn = MockConnection(**connection_kwargs)
    connect = AsyncMock(return_value=conn)
    monkeypatch.setattr(AzureAIProvider, "connect", connect)
    return AzureAIProvider(config or settings()), conn, connect


def assert_failure(failure, code, *, unknown=False, retryable=False):
    assert failure.value.error.model_dump() == {
        "code": code,
        "retryable": retryable,
        "billing_unknown": unknown,
    }
    assert str(failure.value) == code


@pytest.mark.parametrize(
    "changes",
    [
        {"azure_product": None},
        {"azure_product": "postgres"},
        {"azure_extension_version": None},
        {"azure_extension_version": ""},
        {"database_url_env": None},
        {"database_url_env": "postgresql://private:secret@example/db"},
        {"endpoint": "https://example.test"},
        {"api_key_env": "AZURE_KEY"},
        {"auth_header": "api-key"},
        {"max_output_tokens": 1024},
        {"azure_summary_mode": None},
        {"azure_summary_mode": "automatic"},
        {"text_model": None},
        {"language": "en"},
        {"text_model": None, "embedding_model": None, "azure_summary_mode": None},
        {"embedding_model": None, "embedding_target": "deployment"},
        {"embedding_model": {"name": "synthetic", "revision": "1", "dimensions": 1536}},
        {"database_url": "postgresql://private:secret@example/db"},
        {"api_key": "private"},
    ],
)
def test_azure_settings_reject_implicit_or_mixed_contracts(changes):
    with pytest.raises(ValidationError):
        settings(**changes)


@pytest.mark.parametrize("product", PRODUCTS)
def test_azure_settings_allow_explicit_single_operation_and_target_identity(product):
    embed = settings(
        azure_product=product,
        text_model=None,
        azure_summary_mode=None,
        embedding_target="synthetic-deployment",
    )
    assert embed.embedding_target != embed.embedding_model.name
    assert embed.embedding_model.dimensions == 768
    assert settings(azure_product=product, embedding_model=None).text_model
    with pytest.raises(ValidationError):
        embed.azure_product = "flexible_server"


@pytest.mark.parametrize(
    "changes",
    [
        {"azure_product": "horizondb"},
        {"text_model": {"name": "some-model", "revision": "1"}},
        {"language": "English"},
        {"language": "en');select 1;--"},
        {"sentence_count": 0},
        {"sentence_count": 21},
        {"sentence_count": True},
    ],
)
def test_language_requires_flexible_server_and_exact_function_identity(changes):
    with pytest.raises(ValidationError):
        language_settings(**changes)


@pytest.mark.parametrize("product", PRODUCTS)
@pytest.mark.parametrize("generate_type", ["text", "jsonb"])
def test_supported_catalog_contracts_and_default_argument_matching(product, generate_type):
    provider = AzureAIProvider(settings(azure_product=product))
    rows = catalog_rows(product, generate_type)
    for operation, index in [("embed", 0), ("summarize", 1)]:
        provider.compatible(rows, operation)
        extended = deepcopy(rows)
        extended[index]["names"].append("future_optional")
        extended[index]["types"].append("text")
        provider.compatible(extended, operation)
        extended[index]["required"] = len(extended[index]["names"])
        with pytest.raises(ProviderFailure) as failure:
            provider.compatible(extended, operation)
        assert_failure(failure, "provider_capability_unavailable")
    AzureAIProvider(language_settings()).compatible(rows, "summarize")


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "public"},
        {"name": "embedding"},
        {"result": "double precision[]"},
        {"names": None},
        {"names": ["deployment_name", "input"]},
        {"names": ["model", "input", "dimensions", "timeout_ms", "throw_on_error", "max_attempts"]},
        {"types": ["text", "text", "bigint", "integer", "boolean", "integer"]},
        {"types": ["jsonb", "text", "integer", "integer", "boolean", "integer"]},
    ],
)
def test_catalog_rejects_wrong_embedding_shapes(change):
    rows = catalog_rows()
    rows[0].update(change)
    with pytest.raises(ProviderFailure) as failure:
        AzureAIProvider(settings()).compatible(rows, "embed")
    assert_failure(failure, "provider_capability_unavailable")


@pytest.mark.parametrize("index,operation", [(0, "embed"), (1, "summarize")])
def test_catalog_rejects_missing_ambiguous_and_unprivileged_functions(index, operation):
    rows = catalog_rows()
    for candidates in ([], rows + [deepcopy(rows[index])]):
        with pytest.raises(ProviderFailure) as failure:
            AzureAIProvider(settings()).compatible(candidates, operation)
        assert_failure(failure, "provider_capability_unavailable")
    rows[index]["allowed"] = False
    with pytest.raises(ProviderFailure) as failure:
        AzureAIProvider(settings()).compatible(rows, operation)
    assert_failure(failure, "provider_permission_denied")


@pytest.mark.parametrize("product", PRODUCTS)
def test_inspect_is_read_only_and_never_submits_inference(monkeypatch, product):
    provider, conn, connect = mock_provider(
        monkeypatch, settings(azure_product=product), rows=catalog_rows(product)
    )
    assert asyncio.run(provider.inspect()) == {
        "backend": "azure_ai",
        "azure_product": product,
        "extension_version": VERSION,
        "operations": ["summarize", "embed"],
        "sql_contract_verified": True,
        "inference_tested": False,
    }
    assert conn.calls[0] == ("SET default_transaction_read_only=on", None)
    assert not conn.inference_calls and conn.closed
    connect.assert_awaited_once()


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"privileged": True}, "provider_role_invalid"),
        ({"version": None}, "provider_extension_mismatch"),
        ({"version": "another-version"}, "provider_extension_mismatch"),
        ({"rows": []}, "provider_capability_unavailable"),
    ],
)
def test_preflight_failure_never_submits_inference(monkeypatch, kwargs, code):
    provider, conn, connect = mock_provider(monkeypatch, **kwargs)
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(provider.embed(SOURCE))
    assert_failure(failure, code)
    assert not conn.inference_calls and conn.closed
    connect.assert_awaited_once()


def test_connection_forces_tls_separate_autocommit_and_bounded_timeouts(monkeypatch):
    dsn = "host=synthetic.invalid dbname=synthetic user=limited password=private sslmode=disable"
    monkeypatch.setenv("PGAG_SYNTHETIC_AZURE_DSN", dsn)
    connect = AsyncMock(return_value=object())
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
    asyncio.run(AzureAIProvider(settings(timeout_seconds=7)).connect())
    connect.assert_awaited_once_with(
        dsn,
        autocommit=True,
        row_factory=dict_row,
        connect_timeout=5,
        sslmode="verify-full",
        sslrootcert="system",
        application_name="pg_agmemory_inference",
        options="-c statement_timeout=7000 -c lock_timeout=5000",
    )


@pytest.mark.parametrize(
    "dsn",
    [
        None,
        "",
        "host=/var/run/postgresql user=private",
        "dbname=synthetic user=private",
        "not a DSN private",
        "host=synthetic.invalid\npassword=private",
    ],
)
def test_connection_rejects_missing_malformed_and_socket_dsn_without_connecting(monkeypatch, dsn):
    monkeypatch.delenv("PGAG_SYNTHETIC_AZURE_DSN", raising=False)
    if dsn is not None:
        monkeypatch.setenv("PGAG_SYNTHETIC_AZURE_DSN", dsn)
    connect = AsyncMock()
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(AzureAIProvider(settings()).connect())
    assert_failure(failure, "invalid_provider_configuration")
    connect.assert_not_awaited()


@pytest.mark.parametrize("product", PRODUCTS)
def test_embedding_binds_input_and_target_with_one_bounded_call(monkeypatch, product):
    config = settings(azure_product=product, embedding_target=INJECTION, timeout_seconds=9)
    provider, conn, connect = mock_provider(
        monkeypatch, config, result=VECTOR, rows=catalog_rows(product)
    )
    data = InferenceInput(text=INJECTION)
    result = asyncio.run(provider.embed(data))
    assert result.values == VECTOR and result.model == config.embedding_model
    assert result.input_digest == data.digest()
    statement, parameters = conn.inference_calls[0]
    assert INJECTION not in statement
    assert parameters == (INJECTION, INJECTION, 768, 9000)
    assert "dimensions=>%s::integer" in statement
    assert "max_attempts=>1" in statement and "throw_on_error=>true" in statement
    assert "AS MATERIALIZED" in statement
    assert f"octet_length(to_jsonb(result)::text)<={MAX_PROVIDER_RESPONSE_BYTES}" in statement
    assert "ELSE NULL" in statement
    assert statement.count("azure_openai.create_embeddings(") == 1
    assert len(conn.inference_calls) == 1 and conn.closed
    connect.assert_awaited_once()


@pytest.mark.parametrize(
    "result",
    [
        None,
        [],
        [1.0] * 767,
        [1.0] * 769,
        [0.0] * 768,
        [float("nan")] * 768,
        [float("inf")] * 768,
        ["1"] * 768,
        {"embedding": VECTOR},
    ],
)
def test_invalid_embeddings_fail_closed_without_dimension_fallback(monkeypatch, result):
    provider, conn, connect = mock_provider(monkeypatch, result=result)
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(provider.embed(SOURCE))
    assert_failure(failure, "invalid_provider_response", unknown=True)
    assert len(conn.inference_calls) == 1
    assert conn.inference_calls[0][1][2] == 768
    connect.assert_awaited_once()


@pytest.mark.parametrize(
    "error,code,retryable",
    [
        (psycopg.OperationalError("private DSN and text"), "provider_unavailable", True),
        (psycopg.errors.QueryCanceled("private timeout details"), "provider_unavailable", True),
        (TimeoutError("private prompt"), "provider_unavailable", True),
        (
            psycopg.errors.InvalidParameterValue("768 unsupported private"),
            "provider_sql_error",
            False,
        ),
        (psycopg.errors.InsufficientPrivilege("private credentials"), "provider_sql_error", False),
    ],
)
def test_submitted_errors_are_sanitized_unknown_and_not_retried(
    monkeypatch, error, code, retryable
):
    provider, conn, connect = mock_provider(monkeypatch, failure=error)
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(provider.embed(SOURCE))
    assert_failure(failure, code, unknown=True, retryable=retryable)
    assert failure.value.__suppress_context__
    assert len(conn.inference_calls) == 1 and conn.closed
    connect.assert_awaited_once()


def test_connection_failure_has_no_inference_billing_uncertainty(monkeypatch):
    connect = AsyncMock(side_effect=psycopg.OperationalError("private credentials"))
    monkeypatch.setattr(AzureAIProvider, "connect", connect)
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(AzureAIProvider(settings()).embed(SOURCE))
    assert_failure(failure, "provider_unavailable", retryable=True)
    connect.assert_awaited_once()


@pytest.mark.parametrize("result", [["First.", "Second.", "第三部。"], ["Only."]])
def test_language_preserves_every_array_part_and_explicit_controls(monkeypatch, result):
    config = language_settings(sentence_count=5, timeout_seconds=6)
    provider, conn, _ = mock_provider(monkeypatch, config, result=result)
    summary = asyncio.run(provider.summarize(SOURCE))
    assert summary.summary == "\n\n".join(result)
    assert summary.status == "untrusted" and summary.input_digest == SOURCE.digest()
    statement, params = conn.inference_calls[0]
    assert params == (SOURCE.text, "ja", 5, 6000)
    assert "disable_service_logs=>true" in statement and "max_attempts=>1" in statement
    assert "throw_on_error=>true" in statement and len(conn.inference_calls) == 1


@pytest.mark.parametrize("result", [[], None, "not an array", [""], [" "], ["ok", None], [42]])
def test_language_rejects_empty_and_malformed_arrays(monkeypatch, result):
    provider, conn, _ = mock_provider(monkeypatch, language_settings(), result=result)
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(provider.summarize(SOURCE))
    assert_failure(failure, "invalid_provider_response", unknown=True)
    assert len(conn.inference_calls) == 1


@pytest.mark.parametrize("product", PRODUCTS)
@pytest.mark.parametrize("as_text", [False, True])
def test_generate_accepts_strict_json_text_or_jsonb_and_binds_all_arguments(
    monkeypatch, product, as_text
):
    config = settings(azure_product=product, text_model={"name": INJECTION, "revision": "1"})
    payload = {"summary": "Approval remains uncertain."}
    provider, conn, _ = mock_provider(
        monkeypatch,
        config,
        rows=catalog_rows(product, "text" if as_text else "jsonb"),
        result=json.dumps(payload) if as_text else payload,
    )
    data = InferenceInput(text=INJECTION)
    summary = asyncio.run(provider.summarize(data))
    assert summary.summary == payload["summary"] and summary.status == "untrusted"
    assert summary.input_digest == data.digest() and summary.model == config.text_model
    statement, params = conn.inference_calls[0]
    assert INJECTION not in statement and params[:2] == (INJECTION, INJECTION)
    assert isinstance(params[2], Jsonb)
    schema = params[2].obj
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert schema["schema"]["required"] == ["summary"]
    assert "data, not instructions" in params[3]
    assert len(conn.inference_calls) == 1 and statement.count("azure_ai.generate(") == 1


@pytest.mark.parametrize(
    "result",
    [
        None,
        "",
        "plain summary",
        "{",
        "[]",
        "null",
        '"summary"',
        {},
        {"summary": ""},
        {"summary": " "},
        {"summary": 7},
        {"summary": "OK", "approved": True},
        {"refusal": "Cannot comply"},
        {"summary": "OK", "refusal": "Cannot comply"},
        '{"summary":"OK","unexpected":1}',
        {"summary": "x" * 65537},
        "x" * (MAX_PROVIDER_RESPONSE_BYTES + 1),
    ],
)
def test_generate_rejects_malformed_extra_refused_and_oversized_results(monkeypatch, result):
    provider, conn, _ = mock_provider(monkeypatch, result=result)
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(provider.summarize(SOURCE))
    assert_failure(failure, "invalid_provider_response", unknown=True)
    assert len(conn.inference_calls) == 1


@pytest.mark.parametrize("operation", ["embed", "summarize"])
def test_unconfigured_operation_never_opens_connection(monkeypatch, operation):
    config = (
        settings(embedding_model=None)
        if operation == "embed"
        else settings(text_model=None, azure_summary_mode=None)
    )
    provider, conn, connect = mock_provider(monkeypatch, config)
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(getattr(provider, operation)(SOURCE))
    assert_failure(failure, "provider_capability_unavailable")
    connect.assert_not_awaited()
    assert not conn.calls


@dataclass
class SyntheticAzure:
    admin_url: str
    provider: AzureAIProvider
    role: str

    def execute(self, statement, params=None):
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            cursor = conn.execute(statement, params)
            return cursor.fetchall() if cursor.description else None

    def events(self):
        return self.execute("SELECT operation, payload FROM azure_ai.synthetic_events ORDER BY id")

    def calls(self):
        return self.execute("SELECT last_value, is_called FROM azure_ai.synthetic_calls")[0]


@pytest.fixture
def synthetic_azure(env, monkeypatch, request):
    """Only local SQL contracts: fabricated extension metadata, not azure_ai binaries."""
    product, generate_type = getattr(request, "param", ("flexible_server", "text"))
    role = conninfo_to_dict(env.settings.database_url)["user"]
    with psycopg.connect(env.admin_url, autocommit=True) as admin:
        assert (
            admin.execute("SELECT 1 FROM pg_extension WHERE extname='azure_ai'").fetchone() is None
        ), "Never alter a preexisting Azure extension"
        assert (
            admin.execute(
                "SELECT nspname FROM pg_namespace "
                "WHERE nspname IN ('azure_ai','azure_openai','azure_cognitive')"
            ).fetchall()
            == []
        ), "Synthetic tests require unused vendor namespaces"
        with admin.transaction():
            for schema in ("azure_ai", "azure_openai", "azure_cognitive"):
                admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            # A newly allocated schema OID is unused in pg_extension. Direct catalog insertion
            # avoids installing/renaming any real extension or touching shared server files.
            admin.execute(
                """INSERT INTO pg_catalog.pg_extension
                   (oid,extname,extowner,extnamespace,extrelocatable,extversion)
                   SELECT n.oid,'azure_ai',n.nspowner,n.oid,false,%s
                   FROM pg_namespace n WHERE n.nspname='azure_ai'""",
                (VERSION,),
            )
            admin.execute("CREATE SEQUENCE azure_ai.synthetic_calls")
            admin.execute(
                "CREATE TABLE azure_ai.synthetic_events "
                "(id bigint, operation text NOT NULL, payload jsonb NOT NULL)"
            )
            first = "deployment_name" if product == "flexible_server" else "model"
            admin.execute(
                sql.SQL(
                    """CREATE FUNCTION azure_openai.create_embeddings(
                       {} text,input text,dimensions integer DEFAULT 1536,
                       timeout_ms integer DEFAULT 3600000,throw_on_error boolean DEFAULT false,
                       max_attempts integer DEFAULT 5,future_optional text DEFAULT NULL)
                       RETURNS real[] LANGUAGE plpgsql VOLATILE AS $synthetic$
                       BEGIN
                         INSERT INTO azure_ai.synthetic_events VALUES (
                           nextval('azure_ai.synthetic_calls'),'embed',
                           jsonb_build_object('target',$1,'input',$2,'dimensions',$3,
                           'timeout_ms',$4,'throw_on_error',$5,'max_attempts',$6));
                         IF input='unsupported-768' THEN
                           RAISE EXCEPTION 'SYNTHETIC unsupported 768 private'
                             USING ERRCODE='22023';
                         END IF;
                         RETURN ARRAY[1::real] || array_fill(0::real,ARRAY[767]);
                       END $synthetic$"""
                ).format(sql.Identifier(first))
            )
            admin.execute(
                sql.SQL(
                    """CREATE FUNCTION azure_ai.generate(
                       prompt text,model text,json_schema jsonb DEFAULT NULL,
                       system_prompt text DEFAULT NULL,future_optional text DEFAULT NULL)
                       RETURNS {} LANGUAGE plpgsql VOLATILE AS $synthetic$
                       BEGIN
                         INSERT INTO azure_ai.synthetic_events VALUES (
                           nextval('azure_ai.synthetic_calls'),'generate',
                           jsonb_build_object('prompt',$1,'model',$2,'json_schema',$3,
                           'system_prompt',$4));
                         IF prompt='oversized-response' THEN
                           RETURN jsonb_build_object('summary',repeat('x',2097153)){};
                         END IF;
                         RETURN jsonb_build_object('summary','Synthetic summary.'){};
                       END $synthetic$"""
                ).format(
                    sql.SQL(generate_type),
                    sql.SQL("::" + generate_type),
                    sql.SQL("::" + generate_type),
                )
            )
            admin.execute(
                """CREATE FUNCTION azure_cognitive.summarize_abstractive(
                   text text,language text DEFAULT NULL,sentence_count integer DEFAULT 3,
                   disable_service_logs boolean DEFAULT false,timeout_ms integer DEFAULT 3600000,
                   throw_on_error boolean DEFAULT false,max_attempts integer DEFAULT 5,
                   future_optional text DEFAULT NULL)
                   RETURNS text[] LANGUAGE plpgsql VOLATILE AS $synthetic$
                   BEGIN
                     INSERT INTO azure_ai.synthetic_events VALUES (
                       nextval('azure_ai.synthetic_calls'),'language',
                       jsonb_build_object('text',$1,'language',$2,'sentence_count',$3,
                       'disable_service_logs',$4,'timeout_ms',$5,'throw_on_error',$6,
                       'max_attempts',$7));
                     IF text='empty-response' THEN RETURN ARRAY[]::text[]; END IF;
                     RETURN ARRAY['First synthetic part.','Second synthetic part.','第三部。'];
                   END $synthetic$"""
            )
            for function in (
                "azure_openai.create_embeddings(text,text,integer,integer,boolean,integer,text)",
                "azure_ai.generate(text,text,jsonb,text,text)",
                "azure_cognitive.summarize_abstractive"
                "(text,text,integer,boolean,integer,boolean,integer,text)",
            ):
                admin.execute(sql.SQL("ALTER EXTENSION azure_ai ADD FUNCTION " + function))
            admin.execute(
                sql.SQL("GRANT USAGE ON SCHEMA azure_ai,azure_openai,azure_cognitive TO {}").format(
                    sql.Identifier(role)
                )
            )
            admin.execute(
                sql.SQL("GRANT INSERT ON azure_ai.synthetic_events TO {}").format(
                    sql.Identifier(role)
                )
            )
            admin.execute(
                sql.SQL("GRANT USAGE ON SEQUENCE azure_ai.synthetic_calls TO {}").format(
                    sql.Identifier(role)
                )
            )

    async def local_transport(self):
        return await psycopg.AsyncConnection.connect(
            env.settings.database_url,
            autocommit=True,
            row_factory=dict_row,
            options="-c statement_timeout=5000 -c lock_timeout=1000",
        )

    monkeypatch.setattr(AzureAIProvider, "connect", local_transport)
    fixture = SyntheticAzure(env.admin_url, AzureAIProvider(settings(azure_product=product)), role)
    try:
        yield fixture
    finally:
        with psycopg.connect(env.admin_url, autocommit=True) as admin:
            assert admin.execute(
                "SELECT extversion FROM pg_extension WHERE extname='azure_ai'"
            ).fetchone() == (VERSION,), "Only remove the synthetic extension owned by this fixture"
            admin.execute("DROP EXTENSION azure_ai CASCADE")
            for schema in ("azure_cognitive", "azure_openai", "azure_ai"):
                admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.integration
@pytest.mark.parametrize(
    "synthetic_azure",
    [(product, kind) for product in PRODUCTS for kind in ("text", "jsonb")],
    indirect=True,
)
def test_synthetic_postgres_catalog_and_bound_sql_round_trip(synthetic_azure):
    fixture = synthetic_azure
    inspected = asyncio.run(fixture.provider.inspect())
    assert inspected["sql_contract_verified"] is True
    assert inspected["inference_tested"] is False
    assert fixture.calls() == (1, False) and fixture.events() == []
    config = settings(
        azure_product=fixture.provider.settings.azure_product,
        text_model={"name": INJECTION, "revision": "1"},
        embedding_target=INJECTION,
    )
    provider = AzureAIProvider(config)
    data = InferenceInput(text=INJECTION)
    embedded = asyncio.run(provider.embed(data))
    summarized = asyncio.run(provider.summarize(data))
    assert embedded.values == VECTOR and embedded.model.name == "synthetic-embedding"
    assert summarized.summary == "Synthetic summary." and summarized.status == "untrusted"
    assert fixture.calls() == (2, True)
    assert fixture.events()[0] == (
        "embed",
        {
            "target": INJECTION,
            "input": INJECTION,
            "dimensions": 768,
            "timeout_ms": 30000,
            "throw_on_error": True,
            "max_attempts": 1,
        },
    )
    operation, payload = fixture.events()[1]
    assert operation == "generate" and payload["prompt"] == INJECTION
    assert payload["model"] == INJECTION and payload["json_schema"]["strict"] is True
    assert payload["json_schema"]["schema"]["additionalProperties"] is False
    assert fixture.execute("SELECT to_regnamespace('memory') IS NOT NULL") == [(True,)]


@pytest.mark.integration
def test_synthetic_language_array_contract_preserves_all_parts(synthetic_azure):
    fixture = synthetic_azure
    provider = AzureAIProvider(language_settings(sentence_count=4))
    assert asyncio.run(provider.inspect())["inference_tested"] is False
    summary = asyncio.run(provider.summarize(SOURCE))
    assert summary.summary == "First synthetic part.\n\nSecond synthetic part.\n\n第三部。"
    assert summary.status == "untrusted"
    assert fixture.events() == [
        (
            "language",
            {
                "text": SOURCE.text,
                "language": "ja",
                "sentence_count": 4,
                "disable_service_logs": True,
                "timeout_ms": 30000,
                "throw_on_error": True,
                "max_attempts": 1,
            },
        )
    ]
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(provider.summarize(InferenceInput(text="empty-response")))
    assert_failure(failure, "invalid_provider_response", unknown=True)
    assert fixture.calls() == (2, True)


@pytest.mark.integration
@pytest.mark.parametrize(
    "synthetic_azure", [("flexible_server", "text"), ("horizondb", "jsonb")], indirect=True
)
def test_synthetic_response_size_guard_evaluates_function_once(synthetic_azure):
    fixture = synthetic_azure
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(fixture.provider.summarize(InferenceInput(text="oversized-response")))
    assert_failure(failure, "invalid_provider_response", unknown=True)
    assert fixture.calls() == (1, True) and len(fixture.events()) == 1


@pytest.mark.integration
def test_synthetic_unsupported_dimensions_never_retry_or_switch_model(synthetic_azure):
    fixture = synthetic_azure
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(fixture.provider.embed(InferenceInput(text="unsupported-768")))
    assert_failure(failure, "provider_sql_error", unknown=True)
    # nextval survives statement rollback and therefore catches hidden retries.
    assert fixture.calls() == (1, True)
    assert fixture.events() == []


@pytest.mark.integration
def test_real_catalog_ignores_nonextension_lookalikes_and_legacy_names(synthetic_azure):
    fixture = synthetic_azure
    fixture.execute(
        "ALTER EXTENSION azure_ai DROP FUNCTION "
        "azure_openai.create_embeddings(text,text,integer,integer,boolean,integer,text)"
    )
    for name in ("azure_ai.create_embeddings", "azure_openai.embedding"):
        fixture.execute(
            sql.SQL(
                "CREATE FUNCTION "
                + name
                + "() RETURNS real[] LANGUAGE sql AS 'SELECT NULL::real[]'"
            )
        )

    async def catalog():
        async with await fixture.provider.connect() as conn:
            return await fixture.provider.catalog(conn)

    rows = asyncio.run(catalog())
    assert {(row["schema"], row["name"]) for row in rows} == {
        ("azure_ai", "generate"),
        ("azure_cognitive", "summarize_abstractive"),
    }
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(fixture.provider.embed(SOURCE))
    assert_failure(failure, "provider_capability_unavailable")
    assert fixture.calls() == (1, False)


@pytest.mark.integration
@pytest.mark.parametrize("privilege", ["schema", "function"])
def test_real_catalog_checks_schema_usage_and_function_execute(synthetic_azure, privilege):
    fixture = synthetic_azure
    if privilege == "schema":
        fixture.execute(
            sql.SQL("REVOKE USAGE ON SCHEMA azure_openai FROM {}").format(
                sql.Identifier(fixture.role)
            )
        )
    else:
        fixture.execute(
            "REVOKE EXECUTE ON FUNCTION azure_openai.create_embeddings"
            "(text,text,integer,integer,boolean,integer,text) FROM PUBLIC"
        )
    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(fixture.provider.embed(SOURCE))
    assert_failure(failure, "provider_permission_denied")
    assert fixture.calls() == (1, False)
