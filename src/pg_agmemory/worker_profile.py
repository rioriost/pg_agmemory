import hashlib
from typing import Any

from pg_agmemory.providers import (
    EXTRACTION_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    InferenceInput,
    ProviderFailure,
    ProviderSettings,
    make_provider,
    parse_settings,
)
from pg_agmemory.synthesis_policy import profile_digest


class WorkerProfile:
    def __init__(self, settings: ProviderSettings) -> None:
        self.settings = ProviderSettings.model_validate(settings.model_dump())
        if self.settings.backend != "local_http":
            raise ProviderFailure("unsupported_worker_profile")
        if self.settings.text_model is not None and self.settings.max_output_tokens is None:
            raise ProviderFailure("unsupported_worker_profile")
        self.prompt_digests = {
            "extract": hashlib.sha256(EXTRACTION_SYSTEM_PROMPT.encode()).hexdigest(),
            "compact": hashlib.sha256(SUMMARY_SYSTEM_PROMPT.encode()).hexdigest(),
        }
        self.digest = profile_digest(
            self.settings.model_dump(mode="json"), prompt_digests=self.prompt_digests
        )
        self.provider = make_provider(self.settings)

    @classmethod
    def parse(cls, raw: bytes) -> "WorkerProfile":
        return cls(parse_settings(raw))

    def validate_kind(self, kind: str, max_output_tokens: int) -> None:
        if self.digest != profile_digest(
            self.settings.model_dump(mode="json"), prompt_digests=self.prompt_digests
        ):
            raise ProviderFailure("unsupported_worker_profile")
        if kind == "embed":
            valid = self.settings.embedding_model is not None
        else:
            valid = (
                kind in ("extract", "compact") and self.settings.text_model is not None
                and self.settings.max_output_tokens is not None
                and self.settings.max_output_tokens <= max_output_tokens
            )
        if not valid:
            raise ProviderFailure("unsupported_worker_profile")

    async def call(self, kind: str, text: str) -> Any:
        data = InferenceInput(text=text)
        if kind == "extract":
            return await self.provider.extract(data)
        if kind == "embed":
            return await self.provider.embed(data)
        if kind == "compact":
            return await self.provider.summarize(data)
        raise ProviderFailure("unsupported_worker_profile")
