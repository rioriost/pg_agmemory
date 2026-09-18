"""Operator-only, revision-bound Wikipedia lead collection for human review.

Run ``python -m pg_agmemory.evaluation_wikipedia --plan plan.json --output NEW_DIR``.
The closed plan schema is a JSON list of ``{"language": "en" | "ja", "title": str}``.
``--default-plan`` explicitly selects the fixed six EN/JA articles; neither mode
samples, ranks, or tunes a seed. Importing this module never performs network I/O.

The output contains corpus.json only after a complete run, plus manifest.json
and the exact API response bytes. Rendered HTML remains encoded inside parse JSON,
never in an executable HTML file; html_sha256 hashes its exact decoded UTF-8 bytes.
Failure leaves a failed manifest and available audit artifacts, not a partial corpus.
These source artifacts are not covered by the project's MIT license. Operators
must review per-page third-party restrictions before redistribution. No images
are fetched. Rendering an old revision can use present-day transcluded templates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from http.client import HTTPException
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Protocol, Self
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    build_opener,
)

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

Language = Literal["en", "ja"]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
MAX_ARTICLES = 20
MAX_PLAN_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_TEXT_BYTES = 12_000
MAX_PARAGRAPHS = 6
MANIFEST_RESERVE = 128 * 1024
TIMEOUT_SECONDS = 20
USER_AGENT = "pg-agmemory-review-bot/0.0.27 (https://github.com/rioriost/pg_agmemory)"
LICENSE_URL: Final = "https://creativecommons.org/licenses/by-sa/4.0/"
MODIFICATIONS = (
    "Rendered action=parse section=0 from the selected oldid; "
    "this is lead text, not a full article.",
    "HTML converted to plain text; entities decoded and whitespace collapsed; paragraphs "
    "separated by two newlines; Unicode is not normalized.",
    "Images, scripts, styles, tables, references, navigation, quotations, and non-paragraph "
    f"material omitted; at most {MAX_PARAGRAPHS} complete leading paragraphs retained "
    f"within {MAX_TEXT_BYTES} UTF-8 bytes; later paragraphs are not substituted to fit.",
    "Rendered/transcluded templates and other dependencies are evaluated at fetch time "
    "and may not reflect their state at the selected historical revision.",
    "Wikipedia material is CC-BY-SA-4.0, not project MIT; per-page third-party restrictions "
    "and attribution requirements require operator review before reuse.",
)


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _validate_title(value: str) -> str:
    if value != value.strip() or any(ord(char) < 32 or char in "#<>[]{}|" for char in value):
        raise ValueError("Invalid article title")
    return value


class ArticleRequest(_StrictModel):
    language: Language
    title: str = Field(min_length=1, max_length=256)

    _title = field_validator("title")(_validate_title)


def validate_plan(plan: list[ArticleRequest]) -> list[ArticleRequest]:
    requests = TypeAdapter(list[ArticleRequest]).validate_python(plan, strict=True)
    if not 1 <= len(requests) <= MAX_ARTICLES:
        raise ValueError(f"Plan must contain 1..{MAX_ARTICLES} articles")
    keys = {(item.language, item.title.replace("_", " ").casefold()) for item in requests}
    if len(keys) != len(requests):
        raise ValueError("Duplicate article requests")
    return requests


def load_plan(path: Path) -> list[ArticleRequest]:
    with path.open("rb") as stream:
        payload = stream.read(MAX_PLAN_BYTES + 1)
    if len(payload) > MAX_PLAN_BYTES:
        raise ValueError("Plan exceeds byte limit")
    requests = TypeAdapter(list[ArticleRequest]).validate_json(payload, strict=True)
    return validate_plan(requests)


def default_plan() -> list[ArticleRequest]:
    articles: tuple[tuple[Language, str], ...] = (
        ("en", "PostgreSQL"),
        ("ja", "PostgreSQL"),
        ("en", "Solar System"),
        ("ja", "太陽系"),
        ("en", "Atomic clock"),
        ("ja", "原子時計"),
    )
    return [ArticleRequest(language=language, title=title) for language, title in articles]


def source_urls(language: Language, title: str, revision_id: int) -> tuple[str, str, str]:
    host = f"https://{language}.wikipedia.org"
    return (
        f"{host}/wiki/{quote(title.replace(' ', '_'), safe='')}",
        f"{host}/w/index.php?{urlencode({'title': title, 'oldid': revision_id})}",
        f"{host}/w/index.php?{urlencode({'title': title, 'action': 'history'})}",
    )


def source_id(language: Language, page_id: int, revision_id: int, text_sha256: str) -> str:
    return f"wiki-{language}-{page_id}-{revision_id}-{text_sha256}"


def attribution(title: str, revision_url: str, history_url: str) -> str:
    return (
        f'"{title}" — Wikipedia contributors. Revision: {revision_url}. '
        f"Contributor history: {history_url}. CC-BY-SA-4.0: {LICENSE_URL}. "
        "Modified: rendered lead converted to plain text and excerpted; see modifications."
    )


class WikipediaSource(_StrictModel):
    source_id: str = Field(min_length=1, max_length=160)
    language: Language
    page_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=256)
    article_url: str = Field(max_length=4096)
    revision_id: int = Field(gt=0)
    revision_timestamp: str = Field(max_length=32)
    revision_url: str = Field(max_length=4096)
    history_url: str = Field(max_length=4096)
    license: Literal["CC-BY-SA-4.0"] = "CC-BY-SA-4.0"
    license_url: Literal["https://creativecommons.org/licenses/by-sa/4.0/"] = LICENSE_URL
    attribution: str = Field(min_length=1, max_length=12_000)
    retrieved_at: str = Field(max_length=32)
    html_sha256: Sha256
    text_sha256: Sha256
    text: str = Field(min_length=1, max_length=MAX_TEXT_BYTES)
    modifications: list[Annotated[str, Field(min_length=1, max_length=1024)]] = Field(
        min_length=len(MODIFICATIONS), max_length=16
    )

    _title = field_validator("title")(_validate_title)

    @field_validator("revision_timestamp", "retrieved_at")
    @classmethod
    def utc_timestamp(cls, value: str) -> str:
        if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
            raise ValueError("Timestamp must be a UTC ISO8601 second")
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        return value

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        if (self.article_url, self.revision_url, self.history_url) != source_urls(
            self.language, self.title, self.revision_id
        ):
            raise ValueError("Source URLs must be canonical HTTPS Wikipedia provenance URLs")
        encoded = self.text.encode("utf-8")
        if len(encoded) > MAX_TEXT_BYTES or not self.text.strip():
            raise ValueError("Source text is empty or exceeds the UTF-8 byte limit")
        if self.text_sha256 != sha256(encoded):
            raise ValueError("Text SHA256 does not match captured text")
        if self.source_id != source_id(
            self.language, self.page_id, self.revision_id, self.text_sha256
        ):
            raise ValueError("Source ID does not match language/page/revision/captured text")
        if self.attribution != attribution(self.title, self.revision_url, self.history_url):
            raise ValueError("Required Wikipedia attribution is missing or altered")
        if not all(notice in self.modifications for notice in MODIFICATIONS):
            raise ValueError("Required modification and licensing notices are missing")
        return self


class WikipediaCorpus(_StrictModel):
    format: Literal["pgag-wikipedia-corpus-v1"] = "pgag-wikipedia-corpus-v1"
    sources: list[WikipediaSource] = Field(min_length=1, max_length=MAX_ARTICLES)

    @model_validator(mode="after")
    def unique_sources(self) -> Self:
        if len({source.source_id for source in self.sources}) != len(self.sources):
            raise ValueError("Duplicate source IDs")
        return self

    def digest(self) -> str:
        """SHA256 of canonical UTF-8 JSON, including provenance and collection timestamps."""
        return sha256(_json_bytes(self.model_dump(mode="json")))


_OMIT_TAGS = {
    "script",
    "style",
    "table",
    "nav",
    "aside",
    "figure",
    "figcaption",
    "img",
    "picture",
    "svg",
    "math",
    "audio",
    "video",
    "iframe",
    "object",
    "template",
    "noscript",
    "form",
    "button",
    "input",
    "select",
    "textarea",
    "blockquote",
    "q",
}
_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_OMIT_CLASSES = {
    "reference",
    "references",
    "reflist",
    "mw-references-wrap",
    "mw-editsection",
    "mw-empty-elt",
    "hatnote",
    "navigation-not-searchable",
    "navbox",
    "vertical-navbox",
    "sidebar",
    "metadata",
    "shortdescription",
    "noprint",
    "nomobile",
    "infobox",
    "thumb",
    "toc",
    "quotebox",
    "mbox",
    "ambox",
    "dablink",
    "rellink",
}


class _LeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        self._stack: list[tuple[str, bool]] = []
        self._parts: list[str] | None = None

    def _finish_paragraph(self) -> None:
        if self._parts is not None:
            paragraph = " ".join("".join(self._parts).split())
            if paragraph:
                self.paragraphs.append(paragraph)
        self._parts = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        style = re.sub(r"\s+", "", attributes.get("style") or "").lower()
        hidden = (
            tag in _OMIT_TAGS
            or bool(classes & _OMIT_CLASSES)
            or attributes.get("role") in {"navigation", "note"}
            or "hidden" in attributes
            or attributes.get("aria-hidden") == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        )
        blocked = hidden or bool(self._stack and self._stack[-1][1])
        if tag == "p" and not blocked:
            self._finish_paragraph()
            self._parts = []
        if tag == "br" and not blocked and self._parts is not None:
            self._parts.append(" ")
        if tag not in _VOID_TAGS:
            self._stack.append((tag, blocked))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                blocked = self._stack[index][1]
                if tag == "p" and not blocked:
                    self._finish_paragraph()
                del self._stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if self._parts is not None and not (self._stack and self._stack[-1][1]):
            self._parts.append(data)


def lead_text(html: str) -> str:
    """Select at most six complete paragraphs and 12,000 UTF-8 bytes; never slice text."""
    if len(html.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise ValueError("Rendered HTML exceeds byte limit")
    parser = _LeadParser()
    parser.feed(html)
    parser.close()
    selected: list[str] = []
    for paragraph in parser.paragraphs[:MAX_PARAGRAPHS]:
        candidate = "\n\n".join([*selected, paragraph])
        if len(candidate.encode("utf-8")) > MAX_TEXT_BYTES:
            break
        selected.append(paragraph)
    if not selected:
        raise ValueError("No complete lead paragraph fits the excerpt caps")
    return "\n\n".join(selected)


@dataclass(frozen=True)
class FetchResponse:
    status: int
    url: str
    content_type: str
    body: bytes


class Transport(Protocol):
    def get(self, url: str) -> FetchResponse: ...


class CollectionError(ValueError):
    """Explicit collection failure; inspect the retained manifest for request metadata."""


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


class OfficialWikipediaClient:
    """Serial GET-only stdlib client: no proxies, redirects, retries, or fallback."""

    def __init__(self) -> None:
        self._opener: OpenerDirector = build_opener(
            ProxyHandler({}), _NoRedirects(), HTTPSHandler(context=ssl.create_default_context())
        )

    def get(self, url: str) -> FetchResponse:
        if not any(
            url.startswith(f"https://{lang}.wikipedia.org/w/api.php?") for lang in ("en", "ja")
        ):
            raise CollectionError("Refusing a non-allowlisted API endpoint")
        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            method="GET",
        )
        started = time.monotonic()
        try:
            try:
                response = self._opener.open(request, timeout=TIMEOUT_SECONDS)
            except HTTPError as error:
                response = error
            with response:
                chunks: list[bytes] = []
                size = 0
                while size <= MAX_RESPONSE_BYTES:
                    if time.monotonic() - started > TIMEOUT_SECONDS:
                        raise CollectionError("API response exceeded wall-clock deadline")
                    chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                return FetchResponse(
                    status=response.code,
                    url=response.geturl(),
                    content_type=response.headers.get("Content-Type", ""),
                    body=b"".join(chunks),
                )
        except (URLError, OSError, HTTPException) as error:
            raise CollectionError(f"API transport failed ({type(error).__name__})") from error


class _Artifacts:
    def __init__(self, directory: Path) -> None:
        directory.mkdir(exist_ok=False)
        self.directory = directory
        self.bytes_written = 0

    def store(self, name: str, payload: bytes) -> None:
        if self.bytes_written + len(payload) + 2 * MANIFEST_RESERVE > MAX_ARTIFACT_BYTES:
            raise CollectionError("Total artifact byte cap exceeded")
        with (self.directory / name).open("xb") as stream:
            stream.write(payload)
        self.bytes_written += len(payload)

    def manifest(self, value: dict[str, Any]) -> None:
        payload = _json_bytes(value)
        if len(payload) > MANIFEST_RESERVE:
            raise CollectionError("Manifest byte cap exceeded")
        pending = self.directory / "manifest.pending"
        with pending.open("wb") as stream:
            stream.write(payload)
        pending.replace(self.directory / "manifest.json")


def _api_url(language: Language, parameters: dict[str, str | int]) -> str:
    return f"https://{language}.wikipedia.org/w/api.php?" + urlencode(
        {"format": "json", "formatversion": 2, "maxlag": 5, **parameters}
    )


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CollectionError(f"API response has invalid {label}")
    return value


def _positive_int(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise CollectionError(f"API response has invalid {label}")
    return value


def _validate_revision_continuation(payload: dict[str, Any]) -> None:
    # rvlimit=1 can advertise older history. Validate the cursor, but never follow it.
    if "continue" not in payload:
        return
    continuation = _object(payload["continue"], "revision-history continuation")
    if set(continuation) != {"rvcontinue", "continue"} or continuation["continue"] != "||info":
        raise CollectionError("API returned unexpected revision-history continuation")
    cursor = continuation["rvcontinue"]
    if not isinstance(cursor, str) or not re.fullmatch(r"[0-9]{14}\|[1-9][0-9]{0,19}", cursor):
        raise CollectionError("API returned malformed revision-history continuation")
    try:
        datetime.strptime(cursor[:14], "%Y%m%d%H%M%S")
    except ValueError as error:
        raise CollectionError("API returned invalid revision-history continuation date") from error


def _revision_metadata(payload: dict[str, Any]) -> tuple[int, str, int, str]:
    _validate_revision_continuation(payload)
    query = _object(payload.get("query"), "query")
    if any(key in query for key in ("redirects", "interwiki")):
        raise CollectionError("Redirect/interwiki resolution is not allowed")
    pages = query.get("pages")
    if not isinstance(pages, list) or len(pages) != 1:
        raise CollectionError("API must return exactly one page")
    page = _object(pages[0], "page")
    if (
        any(key in page for key in ("missing", "invalid", "redirect"))
        or type(page.get("ns")) is not int
        or page["ns"] != 0
    ):
        raise CollectionError("Missing, invalid, redirect, or non-article page")
    page_id = _positive_int(page.get("pageid"), "page ID")
    title = page.get("title")
    if not isinstance(title, str):
        raise CollectionError("API response has invalid title")
    revisions = page.get("revisions")
    if not isinstance(revisions, list) or len(revisions) != 1:
        raise CollectionError("API must return exactly one revision")
    revision = _object(revisions[0], "revision")
    revision_id = _positive_int(revision.get("revid"), "revision ID")
    if _positive_int(page.get("lastrevid"), "latest revision ID") != revision_id:
        raise CollectionError("Selected revision does not match the page's latest revision")
    timestamp = revision.get("timestamp")
    if not isinstance(timestamp, str):
        raise CollectionError("API response has invalid revision timestamp")
    if not 1 <= len(title) <= 256:
        raise CollectionError("API response has invalid title length")
    _validate_title(title)
    WikipediaSource.utc_timestamp(timestamp)
    return page_id, title, revision_id, timestamp


def _rendered_html(payload: dict[str, Any], page_id: int, title: str, revision_id: int) -> str:
    parsed = _object(payload.get("parse"), "parse")
    if (
        _positive_int(parsed.get("pageid"), "parsed page ID") != page_id
        or _positive_int(parsed.get("revid"), "parsed revision ID") != revision_id
        or parsed.get("title") != title
    ):
        raise CollectionError("Rendered page does not match the selected page and revision")
    html = parsed.get("text")
    if not isinstance(html, str) or not html:
        raise CollectionError("API response has missing rendered text")
    return html


def collect(
    plan: list[ArticleRequest],
    output: Path,
    *,
    transport: Transport,
    delay_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> WikipediaCorpus:
    """Collect an explicit plan into a new directory; inject a transport for offline tests."""
    requests = validate_plan(plan)
    if not math.isfinite(delay_seconds) or not 1 <= delay_seconds <= 60:
        raise ValueError("Serial delay must be between 1 and 60 seconds")
    artifacts = _Artifacts(output)
    manifest: dict[str, Any] = {
        "format": "pgag-wikipedia-collection-v1",
        "status": "running",
        "plan": [item.model_dump(mode="json") for item in requests],
        "plan_sha256": sha256(_json_bytes([item.model_dump(mode="json") for item in requests])),
        "user_agent": USER_AGENT,
        "limits": {
            "max_articles": MAX_ARTICLES,
            "response_bytes": MAX_RESPONSE_BYTES,
            "artifact_bytes": MAX_ARTIFACT_BYTES,
            "text_bytes": MAX_TEXT_BYTES,
            "paragraphs": MAX_PARAGRAPHS,
            "delay_seconds": delay_seconds,
            "maxlag": 5,
        },
        "licensing_notice": MODIFICATIONS[-1],
        "requests": [],
        "completed_source_ids": [],
    }
    sources: list[WikipediaSource] = []

    def fetch(
        index: int, stage: str, language: Language, parameters: dict[str, str | int]
    ) -> dict[str, Any]:
        url = _api_url(language, parameters)
        if manifest["requests"]:
            sleep(delay_seconds)
        entry: dict[str, Any] = {"article_index": index, "stage": stage, "url": url}
        manifest["requests"].append(entry)
        artifacts.manifest(manifest)
        response = transport.get(url)
        body = response.body[:MAX_RESPONSE_BYTES]
        filename = f"{index:02d}-{stage}.json"
        entry.update(
            status=response.status,
            response_url=response.url,
            content_type=response.content_type,
            body_file=filename,
            body_sha256=sha256(body),
            captured_bytes=len(body),
            body_complete=len(response.body) <= MAX_RESPONSE_BYTES,
            body_stored=False,
        )
        artifacts.manifest(manifest)
        artifacts.store(filename, body)
        entry["body_stored"] = True
        artifacts.manifest(manifest)
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise CollectionError("API payload exceeds response byte cap")
        if response.url != url:
            raise CollectionError("API response URL changed; redirects are forbidden")
        if response.status != 200:
            raise CollectionError(f"API returned HTTP {response.status}")
        if response.content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise CollectionError("API response is not application/json")
        try:
            payload = _object(json.loads(body.decode("utf-8")), "root")
        except (UnicodeError, json.JSONDecodeError) as error:
            raise CollectionError("API response is not valid UTF-8 JSON") from error
        if any(key in payload for key in ("error", "errors", "warnings")):
            raise CollectionError("API returned an error or warning")
        if "continue" in payload and stage != "metadata":
            raise CollectionError("API returned an error, warning, or unexpected continuation")
        return payload

    try:
        artifacts.manifest(manifest)
        for index, request in enumerate(requests, 1):
            metadata = fetch(
                index,
                "metadata",
                request.language,
                {
                    "action": "query",
                    "prop": "info|revisions",
                    "titles": request.title,
                    "rvprop": "ids|timestamp",
                    "rvlimit": 1,
                },
            )
            page_id, title, revision_id, timestamp = _revision_metadata(metadata)
            rendered = fetch(
                index,
                "parse",
                request.language,
                {
                    "action": "parse",
                    "oldid": revision_id,
                    "section": 0,
                    "prop": "text|revid",
                    "disableeditsection": 1,
                },
            )
            html = _rendered_html(rendered, page_id, title, revision_id)
            html_bytes = html.encode("utf-8")
            text = lead_text(html)
            text_digest = sha256(text.encode("utf-8"))
            article_url, revision_url, history_url = source_urls(
                request.language, title, revision_id
            )
            source = WikipediaSource(
                source_id=source_id(request.language, page_id, revision_id, text_digest),
                language=request.language,
                page_id=page_id,
                title=title,
                article_url=article_url,
                revision_id=revision_id,
                revision_timestamp=timestamp,
                revision_url=revision_url,
                history_url=history_url,
                attribution=attribution(title, revision_url, history_url),
                retrieved_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                html_sha256=sha256(html_bytes),
                text_sha256=text_digest,
                text=text,
                modifications=list(MODIFICATIONS),
            )
            sources.append(source)
            manifest["completed_source_ids"].append(source.source_id)
            artifacts.manifest(manifest)
        corpus = WikipediaCorpus(sources=sources)
        artifacts.store("corpus.pending", _json_bytes(corpus.model_dump(mode="json")))
        manifest.update(status="complete", corpus_sha256=corpus.digest())
        artifacts.manifest(manifest)
        (output / "corpus.pending").replace(output / "corpus.json")
        return corpus
    except (CollectionError, OSError, ValueError, TypeError, RecursionError) as error:
        manifest.update(status="failed", error_type=type(error).__name__)
        # Validation errors can include source text; retain a category, never echo a payload.
        message = (
            str(error) if isinstance(error, CollectionError) else "Invalid source or local I/O"
        )
        manifest["error"] = message
        try:
            artifacts.manifest(manifest)
        except OSError:
            pass
        raise CollectionError(f"{message}; audit directory: {output}") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--plan", type=Path, help="Explicit closed-schema article request list")
    selection.add_argument("--default-plan", action="store_true", help="Fixed six EN/JA articles")
    parser.add_argument("--output", required=True, type=Path, help="New artifact directory")
    parser.add_argument(
        "--delay-seconds", default=1.0, type=float, help="Serial delay, 1..60 seconds"
    )
    args = parser.parse_args()
    try:
        plan = default_plan() if args.default_plan else load_plan(args.plan)
        corpus = collect(
            plan,
            args.output,
            transport=OfficialWikipediaClient(),
            delay_seconds=args.delay_seconds,
        )
    except (ValueError, OSError) as error:
        message = (
            str(error) if isinstance(error, CollectionError) else "Invalid plan/output/options"
        )
        parser.exit(2, f"{message}\n")
    print(f"Collected {len(corpus.sources)} sources; corpus SHA256 {corpus.digest()}")


if __name__ == "__main__":
    main()
