from typing import Any
from uuid import UUID

from pg_agmemory.jobs import Jobs
from pg_agmemory.models import Capture, CaptureBatch, EnqueueJob
from pg_agmemory.service import MemoryService


class Captures:
    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory

    async def create(self, data: Capture, key: str) -> dict[str, Any]:
        await self.memory.validate_capture(data.episode)
        key_hash, payload_hash, previous = await self.memory.replay(
            "capture", key, data.model_dump_json()
        )
        if previous is not None:
            return previous
        # Separate internal retries from public observe/job idempotency keys.
        episode = await self.memory.observe(data.episode, "capture-observe-v1:" + key_hash)
        intent = data.memory.remember(data.episode.scope_id, UUID(episode["memory_id"]))
        job = await Jobs(self.memory).enqueue(
            EnqueueJob(kind="structured_remember", memory=intent), "capture-job-v1:" + key_hash
        )
        result = {
            "memory_id": episode["memory_id"],
            "revision": 1,
            "synthesis_job_id": job["job_id"],
        }
        await self.memory.save_result("capture", key_hash, payload_hash, result)
        await self.memory.audit("capture", UUID(episode["memory_id"]))
        return result

    async def create_batch(self, data: CaptureBatch, key: str) -> dict[str, Any]:
        await self.memory.validate_capture(data.episode)
        key_hash, payload_hash, previous = await self.memory.replay(
            "capture_batch", key, data.model_dump_json()
        )
        if previous is not None:
            return previous
        episode = await self.memory.observe(data.episode, "capture-batch-observe-v1:" + key_hash)
        jobs = Jobs(self.memory)
        job_ids = []
        for index, memory in enumerate(data.memories):
            intent = memory.remember(data.episode.scope_id, UUID(episode["memory_id"]))
            job = await jobs.enqueue(
                EnqueueJob(kind="structured_remember", memory=intent),
                f"capture-batch-job-v1:{key_hash}:{index}",
            )
            job_ids.append(job["job_id"])
        result = {
            "memory_id": episode["memory_id"],
            "revision": 1,
            "synthesis_job_ids": job_ids,
        }
        await self.memory.save_result("capture_batch", key_hash, payload_hash, result)
        await self.memory.audit("capture_batch", UUID(episode["memory_id"]))
        return result
