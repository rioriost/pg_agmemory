from typing import Any
from uuid import UUID

from pg_agmemory.jobs import Jobs
from pg_agmemory.models import Capture, EnqueueJob
from pg_agmemory.service import MemoryService


class Captures:
    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory

    async def create(self, data: Capture, key: str) -> dict[str, Any]:
        await self.memory.scope(data.episode.scope_id, "write")
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
