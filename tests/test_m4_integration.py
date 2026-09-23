"""Explicit-retention pilot acceptance, not qualification of a live upstream deployment.

The caller-owned producer supplies approved synthetic snapshots. Native storage,
HTTP, delegated identities, RS256 notices, and maintenance are real; there is no
model, connector, external tool execution, or automatic retention in this pilot.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from test_external_source import assert_no_automatic_processing, assert_own_task_usable
from test_source_notice import public_pem, signed

from pg_agmemory.admin import AdminError
from pg_agmemory.capture_policy import CapturePolicy, CapturePolicyRequest, capture_policy
from pg_agmemory.external_source import (
    SOURCE_FORMAT,
    ExternalSnapshot,
    ExternalSourceMemory,
    SourceBinding,
    SourceCaptureOutcome,
    snapshot_digest,
)
from pg_agmemory.models import (
    CheckpointState,
    CreateCheckpoint,
    Evidence,
    Explain,
    Forget,
    MemoryReference,
    PlanToolEffect,
    QueryEpisodes,
    Recall,
    Remember,
    RestoreCheckpoint,
    TransitionToolEffect,
)
from pg_agmemory.native_client import NativeSettings
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError
from pg_agmemory.source_access import (
    SourceAccessRequest,
    SourceDatasetIdentity,
    SourceIdentity,
    SourceNotice,
    source_access,
)
from pg_agmemory.source_dataset import SourceDatasetRequest, source_dataset
from pg_agmemory.source_notice import SourceNoticeProfile, receive_source_notice
from pg_agmemory.source_purge import SourcePurgeRequest, source_purge

CONSENT = "operator-approved-synthetic-m4-pilot-v1"
HARNESS = "m4-explicit-retention-pilot"


class SyntheticCaller:
    def __init__(self):
        self.query_calls = 0
        self.planner_calls = 0
        self.effect_calls = 0

    async def query(self, query_id, *, acl_version="acl-1", text="Synthetic Cedar tier Gold"):
        self.query_calls += 1
        return ExternalSnapshot(
            semantic_revision="synthetic-contract-query-v1",
            query_id=query_id,
            observed_at=datetime(2026, 9, 23, tzinfo=UTC),
            acl_version=acl_version,
            snapshot_text=text,
            result_digest=snapshot_digest(text),
            requires_refresh=True,
            source_authority="external_observation",
        )

    async def planner(self, state, recalled):
        self.planner_calls += 1
        assert recalled.items
        return CheckpointState.model_validate(
            state.model_dump()
            | {"completed_actions": ["Reviewed historical evidence; source refresh still required"]}
        )

    async def execute_effect(self):
        self.effect_calls += 1
        pytest.fail("The pilot must never execute an external effect")


def source_state(env, pilot, operation="get", **changes):
    with source_access(
        env.admin_url,
        SourceAccessRequest(
            operation=operation,
            tenant_id=pilot.profile.tenant_id,
            scope_id=pilot.profile.scope_id,
            principal_id=pilot.profile.principal_id,
            **changes,
        ),
    ) as result:
        return result


def access_epoch(env):
    with psycopg.connect(env.admin_url) as conn:
        return conn.execute(
            "SELECT access_epoch FROM memory.tenant WHERE id=%s", (env.tenants[0],)
        ).fetchone()[0]


@pytest.fixture
def pilot(env):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    source = SourceBinding(
        scope_id=env.scopes[0],
        source_system="synthetic-warehouse",
        dataset_id="contracts",
        source_subject="m4-caller-owned-reader",
    )
    identity = SourceIdentity(**source.model_dump(exclude={"scope_id"}))
    value = SimpleNamespace(
        binding=source,
        dataset=SourceDatasetIdentity(**identity.model_dump(exclude={"source_subject"})),
        signing_key=key,
        caller=SyntheticCaller(),
        profile=SourceNoticeProfile(
            issuer="synthetic-local-source-notifier",
            audience="m4-memory-ingress",
            subject="synthetic-source-administrator",
            key_id="m4-source-signing-key",
            public_key=public_pem(key),
            tenant_id=env.tenants[0],
            scope_id=source.scope_id,
            principal_id=env.principals[2],
            source=identity,
        ),
    )
    bound = source_state(
        env, value, "bind", source=identity, expected_access_epoch=access_epoch(env)
    )
    assert bound.reason == "bound" and bound.effective_permissions == []
    return value


def notice(env, pilot, sequence, reason="authorized"):
    arguments = {}
    if reason == "authorized":
        with psycopg.connect(env.admin_url) as conn:
            now = conn.execute("SELECT clock_timestamp()").fetchone()[0]
        arguments = {
            "acl_version": f"acl-{sequence}",
            "verified_at": now - timedelta(seconds=1),
            "valid_until": now + timedelta(seconds=240),
        }
    return SourceNotice(
        source=pilot.profile.source,
        sequence=sequence,
        decision="allow" if reason == "authorized" else "deny",
        reason=reason,
        **arguments,
    )


def deliver(env, pilot, value):
    with receive_source_notice(
        env.admin_url,
        pilot.profile,
        signed(pilot.signing_key, pilot.profile, value),
        expected_access_epoch=access_epoch(env),
    ) as result:
        assert result.access.source == pilot.profile.source
        assert result.access.principal_id == env.principals[2]
        assert not result.access.source_authorization_verified
        return result.access


async def capture(memory, value, key):
    outcome = await memory.capture(value, consent_reference=CONSENT, idempotency_key=key)
    assert isinstance(outcome, SourceCaptureOutcome)
    assert outcome.source_query_status == "succeeded" and outcome.error is None
    assert outcome.memory_capture_status == "stored" and outcome.memory is not None
    assert outcome.memory.synthesis_job_id is None and outcome.memory.embedding_job_id is None
    return outcome.memory


async def denied(awaitable):
    with pytest.raises(MemoryClientError) as caught:
        await awaitable
    assert caught.value.error.code == "not_found"
    assert caught.value.error.native_status == 404
    assert not caught.value.error.outcome_unknown


def assert_references(actual, expected):
    actual_ids = {(ref.memory_id, ref.revision) for ref in actual}
    expected_ids = {(ref.memory_id, ref.revision) for ref in expected}
    assert len(actual) == len(actual_ids)
    assert len(expected) == len(expected_ids)
    assert actual_ids == expected_ids


async def source_hidden(reader, pilot, roots, derived, checkpoint):
    memory = ExternalSourceMemory(reader, binding=pilot.binding)
    for root in roots:
        await denied(memory.read_snapshot(root))
    await denied(reader.explain(Explain(memory_id=derived)))
    await denied(reader.get_checkpoint(checkpoint))
    recalled = await reader.recall(
        Recall(scope_ids=[pilot.binding.scope_id], purpose="Check current source access")
    )
    assert recalled.items == [] and recalled.context_pack.text == ""
    assert not (
        await reader.query_episodes(QueryEpisodes(scope_ids=[pilot.binding.scope_id]))
    ).episodes


async def derive(client, pilot, root):
    return await client.remember(
        Remember(
            scope_id=pilot.binding.scope_id,
            subject="Cedar",
            predicate="contract_tier",
            value="Gold",
            explicit_intent=True,
            evidence=[Evidence(memory_id=root.memory_id, quote="Gold")],
        ),
        idempotency_key="m4-derived",
    )


async def purge_source(env, pilot, root_ids, sequence):
    deleted = deliver(env, pilot, notice(env, pilot, sequence, "deleted"))
    assert deleted.reason == "deleted" and deleted.effective_permissions == []
    with capture_policy(
        env.admin_url,
        CapturePolicyRequest(
            operation="set",
            tenant_id=env.tenants[0],
            scope_id=pilot.binding.scope_id,
            expected_access_epoch=access_epoch(env),
            policy=CapturePolicy.model_validate(
                CapturePolicy.legacy().model_dump() | {"enabled": False}
            ),
        ),
    ) as disabled:
        assert disabled.configured and not disabled.policy.enabled
    routing = {
        "tenant_id": env.tenants[0],
        "dataset": pilot.dataset,
        "maintenance_principal_id": env.principals[0],
    }
    async with source_purge(
        env.admin_url, SourcePurgeRequest(operation="plan", **routing)
    ) as planned:
        assert not planned.changed and planned.receipt is None
        assert planned.plan.format == "pgag-source-purge-plan-v1"
        assert {root.memory_id for root in planned.plan.roots} == root_ids
        assert {root.scope_id for root in planned.plan.roots} == {pilot.binding.scope_id}
        assert planned.plan.bindings[0].sequence == deleted.sequence
    apply = SourcePurgeRequest(operation="apply", expected_plan=planned.plan, **routing)
    async with source_purge(env.admin_url, apply) as purged:
        assert purged.changed and not purged.replayed and purged.receipt is not None
        assert not purged.capture_reenabled
    async with source_purge(env.admin_url, apply) as replayed:
        assert replayed.replayed and not replayed.changed
        assert replayed.receipt == purged.receipt
    assert source_state(env, pilot).sequence == deleted.sequence
    return purged


def assert_purged_rows(env, pilot, root_ids, targets):
    with psycopg.connect(env.admin_url) as conn:
        tombstones = conn.execute(
            """SELECT object_id FROM memory_ops.object_tombstone
               WHERE tenant_id=%s AND object_id=ANY(%s)""",
            (env.tenants[0], list(targets)),
        ).fetchall()
        assert {row[0] for row in tombstones} == targets
        events = conn.execute(
            """SELECT object_id FROM memory_ops.source_event
               WHERE tenant_id=%s AND scope_id=%s""",
            (env.tenants[0], pilot.binding.scope_id),
        ).fetchall()
        assert {row[0] for row in events} == root_ids
        for table in ("episode", "assertion", "checkpoint", "tool_effect"):
            assert conn.execute(
                psycopg.sql.SQL(
                    "SELECT count(*) FROM memory.{} WHERE tenant_id=%s AND scope_id=%s"
                ).format(psycopg.sql.Identifier(table)),
                (env.tenants[0], pilot.binding.scope_id),
            ).fetchone()[0] == 0


@pytest.mark.integration
@pytest.mark.parametrize("deny_reason", ["revoked", "unavailable"])
def test_http_signed_access_graph_restore_and_explicit_source_refresh(
    env, pilot, api_process, monkeypatch, deny_reason
):
    for name in (
        "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGSMITH_TRACING_V2"
    ):
        monkeypatch.setenv(name, "false")
    pytest.importorskip("langgraph")
    from langgraph.graph.state import CompiledStateGraph

    from pg_agmemory.langgraph import (
        HARNESS_ID,
        HARNESS_VERSION,
        LangGraphMemory,
        build_turn_graph,
    )

    calls = []
    original_client = NativeSettings.client

    async def record(request):
        calls.append((request.method, request.url.path))

    def tracked_client(settings):
        client = original_client(settings)
        client.event_hooks["request"].append(record)
        return client

    monkeypatch.setattr(NativeSettings, "client", tracked_client)

    async def scenario(url):
        async with (
            AsyncMemoryClient(url, env.token()) as maintenance,
            AsyncMemoryClient(url, env.token(index=2)) as reader,
            AsyncMemoryClient(url, env.token(index=1)) as other_tenant,
        ):
            source_memory = ExternalSourceMemory(maintenance, binding=pilot.binding)
            value = await pilot.caller.query("m4-query-1")
            root = await capture(source_memory, value, "m4-capture-1")
            derived = await derive(maintenance, pilot, root)
            read_source = ExternalSourceMemory(reader, binding=pilot.binding)
            await denied(read_source.read_snapshot(root.memory_id))
            await denied(other_tenant.explain(Explain(memory_id=root.memory_id)))
            allowed = deliver(env, pilot, notice(env, pilot, 1))
            assert allowed.effective_permissions == ["read"]
            saved_source = await read_source.read_snapshot(root.memory_id)
            assert saved_source.format == SOURCE_FORMAT
            assert saved_source.snapshot == value and saved_source.snapshot.requires_refresh
            await denied(
                reader.forget(
                    Forget(memory_ids=[root.memory_id], reason="Reader is not maintenance"),
                    idempotency_key="m4-reader-forget-denied",
                )
            )

            run, branch, operation = uuid4(), uuid4(), uuid4()
            memory = LangGraphMemory(
                maintenance, scope_id=pilot.binding.scope_id, run_id=run, branch_id=branch
            )
            root_ref = MemoryReference(memory_id=root.memory_id, revision=root.revision)
            derived_ref = MemoryReference(memory_id=derived.memory_id, revision=derived.revision)
            graph = build_turn_graph(memory, pilot.caller.planner)
            assert isinstance(graph, CompiledStateGraph)
            turn = {
                "state": CheckpointState(
                    goal="Plan from historical evidence, not delegated judgment",
                    constraints=["Refresh the source before acting", "Require operator approval"],
                    next_actions=["Explicitly requery the source"],
                    pending_approvals=["Operator approval before any effect"],
                    versions=["synthetic-contract-query-v1", "acl-1"],
                ),
                "recall_request": Recall(
                    scope_ids=[pilot.binding.scope_id],
                    purpose="Prepare a synthetic proposal only",
                    required_memory_refs=[root_ref, derived_ref],
                    token_budget=8000,
                    max_items=2,
                ),
                "expected_head": None,
                "event_watermark": 1,
                "memory_refs": [root_ref],
                "idempotency_key": "m4-graph-boundary",
            }
            result = (await graph.ainvoke(turn, version="v2")).value
            checkpoint = result["checkpoint_receipt"]
            saved = await reader.get_checkpoint(checkpoint.checkpoint_id)
            assert saved.harness_id == HARNESS_ID and saved.harness_version == HARNESS_VERSION
            assert saved.state_schema_version == 1
            assert saved.state == result["state"]
            assert_references(saved.memory_refs, result["memory_refs"])
            assert_references(saved.memory_refs, [root_ref, derived_ref])
            assert pilot.caller.query_calls == pilot.caller.planner_calls == 1

            effect = await maintenance.plan_tool_effect(
                PlanToolEffect(
                    scope_id=pilot.binding.scope_id,
                    run_id=run,
                    operation_id=operation,
                    tool_name="synthetic.never_execute",
                    action_hash="a" * 64,
                    memory_refs=saved.memory_refs,
                ),
                idempotency_key="m4-effect",
            )
            await maintenance.transition_tool_effect(
                effect.memory_id,
                TransitionToolEffect(
                    expected_revision=1, status="dispatched", reason="Synthetic lost tool receipt"
                ),
                idempotency_key="m4-dispatched-metadata",
            )
            calls.clear()
            restored = await memory.restore(
                checkpoint.checkpoint_id, target_branch_id=uuid4(), idempotency_key="m4-restore"
            )
            assert not restored.resume_allowed and not restored.automatic_reexecution
            assert restored.requires_reconciliation == [operation]
            assert restored.untracked_effects == []
            assert len(restored.tool_effects) == 1
            assert restored.tool_effects[0].memory_id == effect.memory_id
            assert restored.tool_effects[0].status == "unknown"
            assert restored.state == saved.state
            assert_references(restored.memory_refs, saved.memory_refs)
            assert calls == [
                ("GET", f"/v1/checkpoints/{checkpoint.checkpoint_id}"),
                ("POST", "/v1/checkpoints/restore"),
            ]
            assert pilot.caller.query_calls == pilot.caller.planner_calls == 1
            assert pilot.caller.effect_calls == 0

            revoked = deliver(env, pilot, notice(env, pilot, 2, deny_reason))
            assert revoked.reason == deny_reason and revoked.effective_permissions == []
            await source_hidden(
                reader, pilot, [root.memory_id], derived.memory_id, checkpoint.checkpoint_id
            )
            await denied(reader.get_tool_effect(effect.memory_id))
            own = await assert_own_task_usable(reader, env.scopes[2])
            mixed = await reader.recall(
                Recall(
                    scope_ids=[pilot.binding.scope_id, env.scopes[2]],
                    purpose="Continue an independent task",
                )
            )
            assert {item.memory_id for item in mixed.items} == {own}
            assert pilot.caller.query_calls == pilot.caller.planner_calls == 1
            assert pilot.caller.effect_calls == 0

            refreshed = await pilot.caller.query(
                "m4-query-2", acl_version="acl-3", text="Synthetic Cedar tier Platinum"
            )
            renewed = deliver(env, pilot, notice(env, pilot, 3))
            assert renewed.effective_permissions == ["read"]
            fresh_root = await capture(source_memory, refreshed, "m4-capture-2")
            assert fresh_root.memory_id != root.memory_id
            for receipt, expected in ((root, value), (fresh_root, refreshed)):
                historical = await read_source.read_snapshot(receipt.memory_id)
                assert historical.snapshot == expected
                assert historical.snapshot.requires_refresh
                assert historical.snapshot.source_authority == "external_observation"
            assert (await reader.get_checkpoint(checkpoint.checkpoint_id)).state == saved.state
            assert (await reader.get_tool_effect(effect.memory_id)).status == "unknown"
            assert pilot.caller.query_calls == 2 and pilot.caller.planner_calls == 1
            assert pilot.caller.effect_calls == 0

            root_ids = {root.memory_id, fresh_root.memory_id}
            purged = await purge_source(env, pilot, root_ids, sequence=4)
            assert purged.receipt.object_count == 6
            for client in (reader, maintenance):
                await source_hidden(
                    client, pilot, root_ids, derived.memory_id, checkpoint.checkpoint_id
                )
                await denied(client.get_checkpoint(restored.checkpoint_id))
                await denied(client.get_tool_effect(effect.memory_id))
            for checkpoint_id in (checkpoint.checkpoint_id, restored.checkpoint_id):
                await denied(
                    memory.restore(
                        checkpoint_id,
                        target_branch_id=uuid4(),
                        idempotency_key=f"m4-blocked-graph-restore-{checkpoint_id}",
                    )
                )
            assert_purged_rows(
                env, pilot, root_ids,
                root_ids | {
                    derived.memory_id, checkpoint.checkpoint_id,
                    restored.checkpoint_id, effect.memory_id,
                },
            )
            await assert_own_task_usable(reader, env.scopes[2])
            assert pilot.caller.query_calls == 2 and pilot.caller.planner_calls == 1
            assert pilot.caller.effect_calls == 0

    with api_process(f"m4-graph-{deny_reason}.log") as (http, _):
        asyncio.run(scenario(str(http.base_url)))
    assert_no_automatic_processing(env)


@pytest.mark.integration
def test_http_dataset_revoke_terminal_notice_and_bounded_native_purge(env, pilot, api_process):
    async def scenario(url):
        async with (
            AsyncMemoryClient(url, env.token()) as maintenance,
            AsyncMemoryClient(url, env.token(index=2)) as reader,
        ):
            memory = ExternalSourceMemory(maintenance, binding=pilot.binding)
            values = [await pilot.caller.query(f"m4-query-{index}") for index in (1, 2)]
            roots = [
                await capture(memory, value, f"m4-capture-{index}")
                for index, value in enumerate(values, 1)
            ]
            derived = await derive(maintenance, pilot, roots[0])
            refs = [
                MemoryReference(memory_id=derived.memory_id, revision=derived.revision),
                MemoryReference(memory_id=roots[1].memory_id, revision=roots[1].revision),
            ]
            run = uuid4()
            checkpoint = await maintenance.create_checkpoint(
                CreateCheckpoint(
                    scope_id=pilot.binding.scope_id,
                    run_id=run,
                    branch_id=uuid4(),
                    expected_head=None,
                    harness_id=HARNESS,
                    harness_version="1",
                    event_watermark=2,
                    state=CheckpointState(goal="Refresh before considering a proposal"),
                    memory_refs=refs,
                ),
                idempotency_key="m4-purge-checkpoint",
            )
            effect = await maintenance.plan_tool_effect(
                PlanToolEffect(
                    scope_id=pilot.binding.scope_id,
                    run_id=run,
                    operation_id=uuid4(),
                    tool_name="synthetic.never_execute",
                    action_hash="b" * 64,
                    memory_refs=refs,
                ),
                idempotency_key="m4-purge-effect",
            )
            allowed_notice = notice(env, pilot, 1)
            deliver(env, pilot, allowed_notice)
            saved = await reader.get_checkpoint(checkpoint.checkpoint_id)
            assert_references(saved.memory_refs, refs)
            with source_dataset(
                env.admin_url,
                SourceDatasetRequest(
                    operation="get", tenant_id=env.tenants[0], dataset=pilot.dataset
                ),
            ) as inventory:
                assert inventory.matched_targets == 1
            with source_dataset(
                env.admin_url,
                SourceDatasetRequest(
                    operation="revoke",
                    tenant_id=env.tenants[0],
                    dataset=pilot.dataset,
                    expected_access_epoch=inventory.access_epoch,
                    expected_target_digest=inventory.target_digest,
                ),
            ) as revoked:
                assert revoked.changed_targets == 1
                assert not revoked.physical_purge and not revoked.source_notices_changed
            replayed_allow = deliver(env, pilot, allowed_notice)
            assert replayed_allow.replayed and replayed_allow.effective_permissions == []
            root_ids = {root.memory_id for root in roots}
            await source_hidden(
                reader, pilot, root_ids, derived.memory_id, checkpoint.checkpoint_id
            )
            await assert_own_task_usable(reader, env.scopes[2])
            assert (await memory.read_snapshot(roots[0].memory_id)).snapshot == values[0]

            purged = await purge_source(env, pilot, root_ids, sequence=2)
            assert purged.receipt.object_count == 5
            for client in (reader, maintenance):
                await source_hidden(
                    client, pilot, root_ids, derived.memory_id, checkpoint.checkpoint_id
                )
                await denied(client.get_tool_effect(effect.memory_id))
                await denied(
                    client.restore_checkpoint(
                        RestoreCheckpoint(
                            checkpoint_id=checkpoint.checkpoint_id,
                            target_branch_id=uuid4(),
                            harness_id=HARNESS,
                            harness_version="1",
                        ),
                        idempotency_key=f"m4-blocked-restore-{uuid4()}",
                    )
                )
            with pytest.raises(AdminError, match="^source_deleted$"):
                deliver(env, pilot, notice(env, pilot, 3))
            for key in ("m4-capture-1", "m4-new-key-after-purge"):
                outcome = await memory.capture(
                    values[0], consent_reference=CONSENT, idempotency_key=key
                )
                assert outcome.source_query_status == "succeeded"
                assert outcome.memory_capture_status == "failed" and outcome.memory is None
                assert outcome.error.code == "capture_policy_denied"
                assert not outcome.error.outcome_unknown
            await assert_own_task_usable(reader, env.scopes[2])
            targets = root_ids | {derived.memory_id, checkpoint.checkpoint_id, effect.memory_id}
            assert_purged_rows(env, pilot, root_ids, targets)
            assert pilot.caller.query_calls == 2
            assert pilot.caller.planner_calls == pilot.caller.effect_calls == 0

    with api_process("m4-dataset-purge.log") as (http, _):
        asyncio.run(scenario(str(http.base_url)))
    assert_no_automatic_processing(env)


@pytest.mark.integration
def test_http_partial_capture_reconciles_without_requery_or_implicit_retry(
    env, pilot, api_process, monkeypatch
):
    valid_notice = notice(env, pilot, 1)
    unrelated_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    invalid_delivery = signed(unrelated_key, pilot.profile, valid_notice)
    before = source_state(env, pilot)

    def forbid_database(*args, **kwargs):
        pytest.fail("Invalid signed notice reached the database")

    with monkeypatch.context() as patch:
        patch.setattr(psycopg, "connect", forbid_database)
        with pytest.raises(AdminError, match="^invalid_signed_source_notice$"):
            with receive_source_notice(
                env.admin_url, pilot.profile, invalid_delivery,
                expected_access_epoch=before.access_epoch,
            ):
                pytest.fail("Invalid signature was accepted")
    unchanged = source_state(env, pilot)
    assert unchanged.sequence == 0 and unchanged.access_epoch == before.access_epoch
    assert unchanged.effective_permissions == []

    class CaptureFaults(httpx.AsyncHTTPTransport):
        observe_calls = 0
        committed = None
        unavailable = False

        async def handle_async_request(self, request):
            if request.url.path != "/v1/observe":
                return await super().handle_async_request(request)
            self.observe_calls += 1
            if self.unavailable:
                raise httpx.ConnectError("Synthetic memory transport unavailable", request=request)
            response = await super().handle_async_request(request)
            if self.observe_calls == 1:
                await response.aread()
                assert response.status_code == 201
                self.committed = response.json()
                await response.aclose()
                raise httpx.ReadError("Synthetic post-commit disconnect", request=request)
            return response

    transport = CaptureFaults()
    original_client = NativeSettings.client

    def fault_client(settings):
        if settings.api_token != maintenance_token:
            return original_client(settings)
        return httpx.AsyncClient(
            base_url=settings.api_url,
            transport=transport,
            headers={"Authorization": f"Bearer {settings.api_token}"},
            follow_redirects=False,
            trust_env=False,
        )

    maintenance_token = env.token()
    monkeypatch.setattr(NativeSettings, "client", fault_client)

    async def scenario(url):
        async with (
            AsyncMemoryClient(url, maintenance_token) as maintenance,
            AsyncMemoryClient(url, env.token(index=2)) as reader,
        ):
            memory = ExternalSourceMemory(maintenance, binding=pilot.binding)
            value = await pilot.caller.query("m4-durable-query")
            unknown = await memory.capture(
                value, consent_reference=CONSENT, idempotency_key="m4-durable-key"
            )
            assert unknown.source_query_status == "succeeded"
            assert unknown.memory_capture_status == "outcome_unknown" and unknown.memory is None
            assert unknown.error.code == "native_api_unavailable"
            assert unknown.error.retryable and unknown.error.outcome_unknown
            assert transport.observe_calls == pilot.caller.query_calls == 1
            assert transport.committed is not None
            committed_id = UUID(transport.committed["memory_id"])
            await denied(
                ExternalSourceMemory(reader, binding=pilot.binding).read_snapshot(committed_id)
            )
            reconciled = await capture(memory, value, "m4-durable-key")
            assert reconciled.memory_id == committed_id
            assert transport.observe_calls == 2 and pilot.caller.query_calls == 1
            deliver(env, pilot, valid_notice)
            read_source = ExternalSourceMemory(reader, binding=pilot.binding)
            assert (await read_source.read_snapshot(committed_id)).snapshot == value
            page = await maintenance.query_episodes(
                QueryEpisodes(scope_ids=[pilot.binding.scope_id])
            )
            assert [episode.memory_id for episode in page.episodes] == [committed_id]

            fresh_value = await pilot.caller.query("m4-explicit-new-query")
            transport.unavailable = True
            unavailable = await memory.capture(
                fresh_value, consent_reference=CONSENT, idempotency_key="m4-new-durable-key"
            )
            assert unavailable.source_query_status == "succeeded"
            assert unavailable.memory_capture_status == "outcome_unknown"
            assert unavailable.memory is None and unavailable.error.outcome_unknown
            assert transport.observe_calls == 3 and pilot.caller.query_calls == 2
            transport.unavailable = False
            page = await maintenance.query_episodes(
                QueryEpisodes(scope_ids=[pilot.binding.scope_id])
            )
            assert [episode.memory_id for episode in page.episodes] == [committed_id]
            deliver(env, pilot, notice(env, pilot, 2, "unavailable"))
            await denied(
                ExternalSourceMemory(reader, binding=pilot.binding).read_snapshot(committed_id)
            )
            await assert_own_task_usable(reader, env.scopes[2])
            assert transport.observe_calls == 3 and pilot.caller.query_calls == 2
            assert pilot.caller.planner_calls == pilot.caller.effect_calls == 0

    with api_process("m4-partial-outcome.log") as (http, _):
        asyncio.run(scenario(str(http.base_url)))
    assert_no_automatic_processing(env)
