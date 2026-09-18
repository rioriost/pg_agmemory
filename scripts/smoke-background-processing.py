"""Installed-runtime smoke using three synthetic loopback inference calls only."""

import asyncio
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from pg_agmemory.models import (
    AppendWorkingEvent,
    CheckpointBranch,
    CheckpointState,
    CompactWorking,
    CreateCheckpoint,
    Explain,
    Forget,
    MemoryReference,
    Observe,
    PendingEffect,
    ProcessMemory,
    QueryWorkingEvents,
    Recall,
    RestoreCheckpoint,
)
from pg_agmemory.providers import (
    EXTRACTION_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    extraction_schema,
)
from pg_agmemory.recall_hook import SnapshotHookOutput
from pg_agmemory.sdk import AsyncMemoryClient, MemoryClientError
from pg_agmemory.synthesis_policy import SynthesisPolicy

API_URL = "http://127.0.0.1:8000"
TEXT_MODEL = {"name": "synthetic-background-text", "revision": "1"}
EMBEDDING_MODEL = {
    "name": "synthetic-background-embedding",
    "revision": "1",
    "dimensions": 768,
    "distance_metric": "cosine",
    "normalization": "l2-f32-v1",
}
OUTPUT_TOKENS = 512
CONSENT = "production-background-consent"
LITERAL = "SyntheticUser / preferred_editor: Vim"
EVENT = "Synthetic event: src/main.py version 1.2; reviewer approval is still pending."
TAIL = "Synthetic uncompacted tail: await the reviewer; the pending effect remains unknown."
SUMMARY = "Synthetic untrusted context: approval is pending; src/main.py is at version 1.2."


@contextmanager
def json_file(value):
    # The non-root image has a read-only /app. Sealed Linux memfds need no scratch directory.
    descriptor = os.memfd_create(
        "pgag-background-smoke", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING
    )
    try:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        assert len(body) <= 32768
        assert os.write(descriptor, body) == len(body)
        os.lseek(descriptor, 0, os.SEEK_SET)
        fcntl.fcntl(
            descriptor,
            fcntl.F_ADD_SEALS,
            fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL,
        )
        yield f"/proc/self/fd/{descriptor}", descriptor
    finally:
        os.close(descriptor)


def child_environment(**values):
    return {
        "PATH": os.environ["PATH"],
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        **({"SSL_CERT_FILE": os.environ["SSL_CERT_FILE"]} if "SSL_CERT_FILE" in os.environ else {}),
        **values,
    }


def cli(command, *, environment, descriptors=(), data=None, expected_exit=0, timeout=30):
    completed = subprocess.run(
        command,
        input=json.dumps(data) if data is not None else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=environment,
        pass_fds=descriptors,
    )
    assert completed.returncode == expected_exit, "Background smoke child process failed"
    assert len(completed.stdout.encode("utf-8")) <= 262144
    output = json.loads(completed.stdout)
    assert isinstance(output, dict)
    return output


class SyntheticServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, identity, admin_url):
        super().__init__(("127.0.0.1", 0), SyntheticHandler)
        self.identity = identity
        self.admin_url = admin_url
        self.profile_digest = None
        self.policy_epoch = None
        self.compaction_source = None
        self.calls = []
        self.errors = []
        self.lock = threading.Lock()

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address

    def reservation(self, kind, text):
        with psycopg.connect(
            self.admin_url,
            connect_timeout=5,
            options="-c statement_timeout=5000 -c lock_timeout=5000",
            row_factory=dict_row,
        ) as conn:
            rows = conn.execute(
                """SELECT j.id,j.attempt,j.lease_token,c.lease_token AS reserved_token,
                          j.lease_until>clock_timestamp() AS lease_live,c.outcome,
                          c.billing_unknown,c.profile_digest,c.policy_epoch,c.input_bytes,
                          c.max_output_tokens
                   FROM memory_ops.job j JOIN memory_ops.model_call c
                     ON c.tenant_id=j.tenant_id AND c.job_id=j.id
                   WHERE j.tenant_id=%s AND j.scope_id=%s AND j.principal_id=%s
                     AND j.kind=%s AND j.state='running'""",
                (
                    self.identity["tenant_id"],
                    self.identity["scope_id"],
                    self.identity["principal_id"],
                    kind,
                ),
            ).fetchall()
        assert len(rows) == 1, "Exactly one committed reservation must precede inference"
        row = rows[0]
        assert row["attempt"] == 1 and row["lease_live"]
        assert row["lease_token"] == row["reserved_token"]
        assert row["outcome"] == "unknown" and row["billing_unknown"]
        assert row["profile_digest"] == self.profile_digest
        assert row["policy_epoch"] == self.policy_epoch
        assert row["input_bytes"] == len(text.encode("utf-8"))
        assert row["max_output_tokens"] == (None if kind == "embed" else OUTPUT_TOKENS)
        with self.lock:
            assert len(self.calls) < 3
            assert kind == ("extract", "embed", "compact")[len(self.calls)]
            self.calls.append(
                {
                    "kind": kind,
                    "job_id": str(row["id"]),
                    "input_digest": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
            )


class SyntheticHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, payload):
        encoded = json.dumps(payload).encode("utf-8")
        assert len(encoded) <= 32768
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)
        self.close_connection = True

    def do_GET(self):
        self.reply(200 if self.path == "/health" else 404, {"ready": self.path == "/health"})

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            assert 0 < size <= 65536
            assert self.headers.get("Transfer-Encoding") is None
            assert self.headers.get("Content-Type") == "application/json"
            assert self.headers.get("Idempotency-Key") is None
            body = self.rfile.read(size)
            assert len(body) == size
            request = json.loads(body)
            if self.path == "/v1/embeddings":
                assert request == {
                    "model": EMBEDDING_MODEL["name"],
                    "input": LITERAL,
                    "dimensions": 768,
                    "encoding_format": "float",
                }
                self.server.reservation("embed", LITERAL)
                response = {"data": [{"index": 0, "embedding": [1.0] + [0.0] * 767}]}
            else:
                assert self.path == "/v1/chat/completions"
                assert request["model"] == TEXT_MODEL["name"]
                assert request["stream"] is False and request["max_tokens"] == OUTPUT_TOKENS
                assert "tools" not in request and "functions" not in request
                assert len(request["messages"]) == 2
                system, user = request["messages"]
                assert system["role"] == "system" and user["role"] == "user"
                if "response_format" in request:
                    assert request["response_format"] == {
                        "type": "json_schema",
                        "json_schema": extraction_schema(),
                    }
                    assert system["content"] == EXTRACTION_SYSTEM_PROMPT
                    assert user["content"] == LITERAL
                    self.server.reservation("extract", LITERAL)
                    content = json.dumps(
                        {
                            "candidates": [
                                {
                                    "subject": "SyntheticUser",
                                    "predicate": predicate,
                                    "value": "Vim",
                                    "evidence_quote": LITERAL,
                                }
                                for predicate in ("preferred_editor", "editor")
                            ]
                        }
                    )
                else:
                    assert system["content"] == SUMMARY_SYSTEM_PROMPT
                    assert json.loads(user["content"]) == [
                        {
                            "sequence": 1,
                            "source_id": str(self.server.compaction_source),
                            "revision": 1,
                            "content": EVENT,
                        }
                    ]
                    self.server.reservation("compact", user["content"])
                    content = SUMMARY
                response = {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": content},
                        }
                    ]
                }
            self.reply(200, response)
        except (AssertionError, ValueError, KeyError, TypeError, OSError, psycopg.Error):
            with self.server.lock:
                self.server.errors.append("synthetic_inference_contract_failed")
            self.reply(400, {"error": "synthetic_inference_contract_failed"})


class Smoke:
    def __init__(self, identity, subject, config_path, config_fd, server):
        self.identity = identity
        self.scope = UUID(identity["scope_id"])
        self.subject = subject
        self.config_path = config_path
        self.config_fd = config_fd
        self.server = server
        self.prefix = "background-smoke-" + uuid4().hex
        self.worker_env = child_environment(PGAG_DATABASE_URL=os.environ["PGAG_DATABASE_URL"])
        self.admin_env = child_environment(
            PGAG_ADMIN_DATABASE_URL=os.environ["PGAG_ADMIN_DATABASE_URL"]
        )

    def key(self, suffix):
        return self.prefix + "-" + suffix

    def admin(self, operation, *, epoch=None, policy=None):
        command = [
            "pg-agmemory",
            "scope-synthesis",
            operation,
            "--tenant-id",
            self.identity["tenant_id"],
            "--scope-id",
            self.identity["scope_id"],
        ]
        if policy is None:
            return cli(command, environment=self.admin_env)
        with json_file(policy) as (path, descriptor):
            return cli(
                command + ["--expected-access-epoch", str(epoch), "--policy-file", path],
                environment=self.admin_env,
                descriptors=(descriptor,),
            )

    def worker(self, *, digest=False, configured=True):
        command = ["pg-agmemory", "worker", "--subject", self.subject]
        if configured:
            os.lseek(self.config_fd, 0, os.SEEK_SET)
            command += ["--provider-config", self.config_path]
        command += ["--print-profile-digest"] if digest else ["--once"]
        return cli(
            command,
            environment=self.worker_env,
            descriptors=(self.config_fd,) if configured else (),
            timeout=60,
        )

    async def finished(self, sdk, job, kind):
        result = self.worker()
        assert result["job_id"] == str(job)
        assert result["outcome"] == "succeeded", f"Background worker {kind} did not succeed"
        detail = await sdk.get_job(job)
        assert detail.kind == kind and detail.state == "succeeded"
        assert detail.attempt == 1 and detail.retry_of is None and detail.error_code is None
        assert detail.lease_until is None
        assert detail.call["outcome"] == "succeeded" and not detail.call["billing_unknown"]
        assert detail.processing_result == result["result"]
        return result["result"]

    async def observe(self, sdk, text, *, extract=False):
        return await sdk.observe(
            Observe(
                scope_id=self.scope,
                source_namespace="synthetic-background-smoke",
                source_event_id=str(uuid4()),
                occurred_at=datetime.now(UTC),
                content=text,
                consent_reference=CONSENT,
                auto_extract=extract,
            ),
            idempotency_key=self.key("observe-" + uuid4().hex),
        )

    async def missing(self, operation):
        try:
            await operation
        except MemoryClientError as exc:
            assert exc.error.code == "not_found"
        else:
            raise AssertionError("Purged background-processing dependency remained readable")

    async def run(self):
        owned_sources = []
        async with AsyncMemoryClient(API_URL, os.environ["PGAG_SDK_API_TOKEN"]) as sdk:
            assert not (await sdk.recall(
                Recall(scope_ids=[self.scope], purpose="synthetic-background-smoke")
            )).items, "Background smoke requires the previously purged scope"
            try:
                source = await self.observe(sdk, LITERAL, extract=True)
                owned_sources.append(source.memory_id)
                assert source.synthesis_job_id is not None
                assert self.worker(configured=False) == {"outcome": "idle"}
                extracted = await self.finished(sdk, source.synthesis_job_id, "extract")
                assert extracted["counts"] == {"published": 1, "duplicate": 0, "quarantined": 1}
                assert extracted["status"] == "untrusted"
                assert extracted["derivation"]["input_digest"] == self.server.calls[0][
                    "input_digest"
                ]
                assert extracted["derivation"]["model"] == TEXT_MODEL
                assert extracted["derivation"]["profile_digest"] == self.server.profile_digest
                assert extracted["derivation"]["source"] == {
                    "memory_id": str(source.memory_id), "revision": 1
                }
                assert len(extracted["assertions"]) == 1
                proposals = await sdk.get_extraction_candidates(source.synthesis_job_id)
                assert proposals.status == "untrusted"
                assert [item.disposition for item in proposals.candidates] == [
                    "published", "quarantined"
                ]
                for item in proposals.candidates:
                    assert set(item.candidate) == {
                        "subject", "predicate", "value", "evidence_quote", "start", "end"
                    }
                    assert item.candidate["start"] == 0 and item.candidate["end"] == len(LITERAL)
                assert proposals.candidates[0].reason == "allowed_literal_preference"
                assert proposals.candidates[1].reason == "predicate_not_allowed"
                assert proposals.candidates[1].assertion_id is None
                assert proposals.candidates[1].adopted_assertion_id is None
                assertion = UUID(extracted["assertions"][0]["memory_id"])
                explained = await sdk.explain(Explain(memory_id=assertion))
                assert explained.epistemic_status == "inferred"
                assert explained.assertion.subject == "SyntheticUser"
                assert explained.assertion.predicate == "preferred_editor"
                assert explained.assertion.value == "Vim"
                assert explained.evidence[0].quote == LITERAL
                assert explained.confidence["score"] is None
                assert explained.derivation["status"] == "untrusted"
                with psycopg.connect(os.environ["PGAG_ADMIN_DATABASE_URL"]) as conn:
                    assert conn.execute(
                        """SELECT explicit_intent FROM memory.assertion_revision
                           WHERE tenant_id=%s AND assertion_id=%s AND revision=1""",
                        (self.identity["tenant_id"], assertion),
                    ).fetchone() == (False,)

                reference = MemoryReference(memory_id=source.memory_id)
                canonical = await sdk.embedding_input(Explain(memory_id=source.memory_id))
                assert canonical.text == LITERAL
                assert canonical.input_digest == hashlib.sha256(LITERAL.encode()).hexdigest()
                embedding_request = ProcessMemory(
                    scope_id=self.scope, source=reference, kind="embed"
                )
                embed_job = await sdk.process_memory(
                    embedding_request, idempotency_key=self.key("embed")
                )
                embedded = await self.finished(sdk, embed_job.job_id, "embed")
                assert embedded["input_digest"] == canonical.input_digest
                assert embedded["model"] == EMBEDDING_MODEL
                with psycopg.connect(os.environ["PGAG_ADMIN_DATABASE_URL"]) as conn:
                    assert conn.execute(
                        """SELECT input_digest,model_name,model_revision,vector_dims(embedding)
                           FROM memory.episode_embedding WHERE tenant_id=%s AND episode_id=%s""",
                        (self.identity["tenant_id"], source.memory_id),
                    ).fetchall() == [
                        (
                            canonical.input_digest,
                            EMBEDDING_MODEL["name"],
                            EMBEDDING_MODEL["revision"],
                            768,
                        )
                    ]

                event = await self.observe(sdk, EVENT)
                owned_sources.append(event.memory_id)
                self.server.compaction_source = event.memory_id
                branch = CheckpointBranch(
                    scope_id=self.scope, run_id=uuid4(), branch_id=uuid4()
                )
                state = CheckpointState(
                    goal="Keep the exact typed synthetic task",
                    constraints=["Never infer permission from model text"],
                    pending_approvals=["Reviewer approval is still required"],
                    important_ids=["TASK-SYNTHETIC-13"],
                    versions=["1.2"],
                    paths=["src/main.py"],
                    failed_actions=["Earlier synthetic approach A failed"],
                    unresolved_questions=["Who is the reviewer?"],
                    next_actions=["Ask the reviewer"],
                    in_progress_actions=["Prepare the synthetic test"],
                    blocked_actions=["Wait for reviewer approval"],
                    pending_effects=[
                        PendingEffect(
                            operation_id=uuid4(), description="Synthetic side effect",
                            status="unknown",
                        )
                    ],
                )
                checkpoint = await sdk.create_checkpoint(
                    CreateCheckpoint(
                        **branch.model_dump(),
                        expected_head=None,
                        harness_id="synthetic-background-smoke",
                        harness_version="1",
                        event_watermark=999,
                        state=state,
                        memory_refs=[MemoryReference(memory_id=event.memory_id)],
                    ),
                    idempotency_key=self.key("checkpoint"),
                )
                first = await sdk.append_working_event(
                    AppendWorkingEvent(
                        **branch.model_dump(), source=MemoryReference(memory_id=event.memory_id)
                    ),
                    idempotency_key=self.key("event"),
                )
                assert first.sequence == 1
                compact_request = CompactWorking(
                    **branch.model_dump(),
                    expected_head=checkpoint.checkpoint_id,
                    through_sequence=1,
                )
                compact_job = await sdk.compact_working(
                    compact_request, idempotency_key=self.key("compact")
                )
                tail = await self.observe(sdk, TAIL)
                owned_sources.append(tail.memory_id)
                appended = await sdk.append_working_event(
                    AppendWorkingEvent(
                        **branch.model_dump(), source=MemoryReference(memory_id=tail.memory_id)
                    ),
                    idempotency_key=self.key("tail"),
                )
                assert appended.sequence == 2
                compacted = await self.finished(sdk, compact_job.job_id, "compact")
                snapshot_id = UUID(compacted["checkpoint_id"])
                snapshot = await sdk.get_working_snapshot(snapshot_id)
                assert snapshot.checkpoint.state.model_dump() == state.model_dump()
                assert snapshot.checkpoint.event_watermark == 999
                assert snapshot.status == "untrusted" and snapshot.summary == SUMMARY
                assert snapshot.model == TEXT_MODEL
                assert snapshot.input_digest == self.server.calls[2]["input_digest"]
                assert snapshot.coverage_start == snapshot.coverage_end == 1
                assert not snapshot.checkpoint.automatic_reexecution
                assert not snapshot.checkpoint.resume_allowed
                assert [item.source.memory_id for item in snapshot.tail.events] == [
                    tail.memory_id
                ]
                events = await sdk.query_working_events(
                    QueryWorkingEvents(**branch.model_dump())
                )
                assert [item.sequence for item in events.events] == [1, 2]
                restored = await sdk.restore_checkpoint(
                    RestoreCheckpoint(
                        checkpoint_id=snapshot_id,
                        target_branch_id=uuid4(),
                        harness_id="synthetic-background-smoke",
                        harness_version="1",
                    ),
                    idempotency_key=self.key("restore"),
                )
                assert restored.parent_checkpoint == snapshot_id
                assert restored.state.model_dump() == state.model_dump()
                assert not restored.automatic_reexecution and not restored.resume_allowed

                budget = len(snapshot.model_dump_json().encode("utf-8"))
                assert 1 < budget <= 65536
                hook_env = child_environment(
                    PGAG_HOOK_API_URL=API_URL,
                    PGAG_HOOK_API_TOKEN=os.environ["PGAG_SDK_API_TOKEN"],
                    PGAG_HOOK_SCOPE_IDS=json.dumps([str(self.scope)]),
                    PGAG_HOOK_TOKEN_BUDGET="2000",
                    PGAG_HOOK_MAX_ITEMS="1",
                    PGAG_HOOK_TIMEOUT_SECONDS="20",
                    PGAG_HOOK_WORKING_SNAPSHOT_BUDGET_BYTES=str(budget),
                )
                hook_input = {
                    "event": "after_compaction",
                    "query": "",
                    "working_snapshot_id": str(snapshot_id),
                }
                hook = SnapshotHookOutput.model_validate(
                    cli(["pg-agmemory", "recall-hook"], environment=hook_env, data=hook_input)
                )
                assert hook.status == "ok" and hook.error is None
                assert hook.working_snapshot.checkpoint.checkpoint_id == snapshot_id
                assert hook.working_snapshot.checkpoint.state.model_dump() == state.model_dump()
                assert hook.working_snapshot.status == "untrusted"
                assert hook.working_snapshot.summary == SUMMARY
                assert hook.result.items[0].memory_id == tail.memory_id
                assert hook.result.context_pack.byte_count <= 2000
                denied = cli(
                    ["pg-agmemory", "recall-hook"],
                    environment=hook_env | {
                        "PGAG_HOOK_WORKING_SNAPSHOT_BUDGET_BYTES": str(budget - 1)
                    },
                    data=hook_input,
                    expected_exit=1,
                )
                assert denied["status"] == "error" and denied["result"] is None
                assert denied["error"]["code"] == "budget_exhausted"

                assert (await sdk.process_memory(
                    ProcessMemory(scope_id=self.scope, source=reference, kind="extract"),
                    idempotency_key=self.key("extract-replay"),
                )).job_id == source.synthesis_job_id
                assert await sdk.process_memory(
                    embedding_request, idempotency_key=self.key("embed")
                ) == embed_job
                assert await sdk.compact_working(
                    compact_request, idempotency_key=self.key("compact")
                ) == compact_job
                assert self.worker() == {"outcome": "idle"}
                assert not self.server.errors and len(self.server.calls) == 3

                purged = await sdk.forget(
                    Forget(memory_ids=owned_sources, reason="synthetic-background-smoke"),
                    idempotency_key=self.key("purge"),
                )
                owned_sources.clear()
                assert purged.object_count == 10
                await self.missing(sdk.get_working_snapshot(snapshot_id))
                await self.missing(sdk.get_checkpoint(restored.checkpoint_id))
                await self.missing(sdk.get_extraction_candidates(source.synthesis_job_id))
                await self.missing(sdk.embedding_input(Explain(memory_id=source.memory_id)))
                assert not (await sdk.recall(
                    Recall(scope_ids=[self.scope], purpose="synthetic-background-smoke")
                )).items
                with psycopg.connect(os.environ["PGAG_ADMIN_DATABASE_URL"]) as conn:
                    assert conn.execute(
                        """SELECT count(*) FROM memory_ops.model_call WHERE tenant_id=%s
                           AND scope_id=%s AND policy_epoch=%s AND outcome='succeeded'
                           AND NOT billing_unknown""",
                        (
                            self.identity["tenant_id"], self.scope,
                            self.server.policy_epoch,
                        ),
                    ).fetchone() == (3,)
                return {
                    "model_calls": 3,
                    "published": 1,
                    "quarantined": 1,
                    "embedding_dimensions": 768,
                    "typed_state_preserved": True,
                    "hook_snapshot_restored": True,
                    "hook_budget_bytes": budget,
                    "purged_objects": purged.object_count,
                }
            finally:
                if owned_sources:
                    await sdk.forget(
                        Forget(memory_ids=owned_sources, reason="synthetic-smoke-cleanup"),
                        idempotency_key=self.key("cleanup"),
                    )


def main():
    assert len(sys.argv) == 3, "Pass provisioning JSON and the provisioned worker subject"
    assert os.geteuid() != 0, "Run this smoke in the installed non-root runtime"
    identity = json.loads(sys.argv[1])
    for name in ("tenant_id", "principal_id", "scope_id"):
        identity[name] = str(UUID(identity[name]))
    admin_url = os.environ["PGAG_ADMIN_DATABASE_URL"]
    with psycopg.connect(admin_url) as conn:
        assert conn.execute(
            """SELECT external_subject FROM memory.principal
               WHERE tenant_id=%s AND id=%s""",
            (identity["tenant_id"], identity["principal_id"]),
        ).fetchone() == (sys.argv[2],)
    server = SyntheticServer(identity, admin_url)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert thread.is_alive()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("GET", "/health")
            response = connection.getresponse()
            assert response.status == 200 and json.loads(response.read(1024)) == {"ready": True}
        finally:
            connection.close()
        configuration = {
            "backend": "local_http",
            "endpoint": f"http://127.0.0.1:{server.server_port}/v1",
            "text_model": TEXT_MODEL,
            "embedding_model": EMBEDDING_MODEL,
            "max_output_tokens": OUTPUT_TOKENS,
            "timeout_seconds": 15,
        }
        with json_file(configuration) as (path, descriptor):
            smoke = Smoke(identity, sys.argv[2], path, descriptor, server)
            digest = smoke.worker(digest=True)
            server.profile_digest = digest["profile_digest"]
            assert len(server.profile_digest) == 64 and not server.calls
            before = smoke.admin("get")
            desired = SynthesisPolicy(
                enabled=True,
                profile_digest=server.profile_digest,
                consent_references=[CONSENT],
                publish_predicates=["preferred_editor"],
                kinds=["extract", "embed", "compact"],
                max_calls=3,
                max_pending_jobs=3,
                max_input_bytes=8192,
                max_output_tokens=OUTPUT_TOKENS,
            ).model_dump(mode="json")
            try:
                configured = smoke.admin(
                    "set", epoch=before["access_epoch"], policy=desired
                )
                assert configured["policy"] == desired
                server.policy_epoch = configured["policy_access_epoch"]
                result = asyncio.run(smoke.run())
            finally:
                current = smoke.admin("get")
                if current["policy"] != before["policy"]:
                    assert current["policy"] == desired, (
                        "A concurrent policy change prevents safe smoke-policy restoration"
                    )
                    restored = smoke.admin(
                        "set", epoch=current["access_epoch"], policy=before["policy"]
                    )
                    assert restored["policy"] == before["policy"]
                assert smoke.admin("get")["policy"] == before["policy"]
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
        assert not thread.is_alive()
    assert not server.errors and len(server.calls) == 3
    print(json.dumps(
        {
            "status": "ok",
            "smoke": "background_processing",
            "provider": "synthetic_local_http",
            "policy_restored": True,
            **result,
        },
        sort_keys=True,
    ))
    print(
        "Production background processing smoke passed: admin CAS, synthetic HTTP x3, "
        "worker CLI, inferred/quarantined candidates, embedding, exact-state compaction, "
        "budgeted snapshot hook, purge, policy restore"
    )


if __name__ == "__main__":
    main()
