"""Synthetic, offline fixtures only; never fetch or commit Wikipedia payloads."""

import copy
import hashlib
import io
import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from pg_agmemory import evaluation_wikipedia as wiki

HTML = (
    '<div class="mw-parser-output"><table><tr><td>OMIT TABLE</td></tr></table>'
    '<p>Synthetic <b>Unicode</b>: 日本語 &amp; café.<sup class="reference">[1]</sup></p>'
    "<p>A second complete paragraph.</p></div>"
)
TEXT = "Synthetic Unicode: 日本語 & café.\n\nA second complete paragraph."


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("Network must never be used in collector tests")

    monkeypatch.setattr(wiki.OpenerDirector, "open", denied)


def metadata(title="Synthetic article", page_id=17, revision_id=401):
    return {
        "batchcomplete": True,
        "query": {
            "pages": [
                {
                    "pageid": page_id,
                    "ns": 0,
                    "title": title,
                    "lastrevid": revision_id,
                    "revisions": [{"revid": revision_id, "timestamp": "2026-09-18T00:00:00Z"}],
                }
            ],
        },
    }


def parsed(title="Synthetic article", page_id=17, revision_id=401, html=HTML):
    return {"parse": {"pageid": page_id, "title": title, "revid": revision_id, "text": html}}


class FixtureTransport:
    def __init__(self, payloads, *, status=200, content_type="application/json", redirect=False):
        self.payloads = iter(payloads)
        self.urls = []
        self.status = status
        self.content_type = content_type
        self.redirect = redirect

    def get(self, url):
        self.urls.append(url)
        payload = next(self.payloads)
        if isinstance(payload, Exception):
            raise payload
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return wiki.FetchResponse(
            status=self.status,
            url=url + "&redirected=true" if self.redirect else url,
            content_type=self.content_type,
            body=body,
        )


def collect_fixture(tmp_path, payloads=None, **kwargs):
    transport = FixtureTransport(payloads if payloads is not None else [metadata(), parsed()])
    corpus = wiki.collect(
        [wiki.ArticleRequest(language="en", title="Synthetic article")],
        tmp_path / "corpus",
        transport=transport,
        sleep=lambda seconds: None,
        **kwargs,
    )
    return corpus, transport


def test_revision_is_selected_first_and_parse_is_explicitly_bound(tmp_path):
    transport = FixtureTransport([metadata(), parsed()])
    delays = []
    output = tmp_path / "corpus"
    corpus = wiki.collect(
        [wiki.ArticleRequest(language="en", title="Synthetic article")],
        output,
        transport=transport,
        sleep=delays.append,
    )
    assert len(transport.urls) == 2
    first, second = [parse_qs(urlsplit(url).query) for url in transport.urls]
    assert first["action"] == ["query"] and first["rvprop"] == ["ids|timestamp"]
    assert first["prop"] == ["info|revisions"] and first["rvlimit"] == ["1"]
    assert second["action"] == ["parse"] and second["oldid"] == ["401"]
    assert second["section"] == ["0"] and second["prop"] == ["text|revid"]
    assert "page" not in second and "pageid" not in second and "titles" not in second
    assert "redirects" not in first and "redirects" not in second
    assert all(query["maxlag"] == ["5"] for query in (first, second))
    assert delays == [1.0]
    assert corpus.sources[0].text == TEXT
    assert corpus.sources[0].revision_timestamp == "2026-09-18T00:00:00Z"
    assert json.loads((output / "01-parse.json").read_bytes()) == parsed()
    assert json.loads((output / "01-parse.json").read_bytes())["parse"]["text"].encode() == (
        HTML.encode()
    )
    assert all(path.suffix == ".json" for path in output.iterdir())
    assert wiki.WikipediaCorpus.model_validate_json((output / "corpus.json").read_bytes()) == corpus
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert manifest["status"] == "complete" and manifest["corpus_sha256"] == corpus.digest()
    for entry in manifest["requests"]:
        assert (
            hashlib.sha256((output / entry["body_file"]).read_bytes()).hexdigest()
            == (entry["body_sha256"])
        )


def test_metadata_history_continuation_is_validated_retained_and_never_followed(tmp_path):
    value = metadata()
    del value["batchcomplete"]
    value["continue"] = {"rvcontinue": "20260824161056|400", "continue": "||info"}
    corpus, transport = collect_fixture(tmp_path, [value, parsed()])
    assert corpus.sources[0].revision_id == 401
    assert len(transport.urls) == 2
    queries = [parse_qs(urlsplit(url).query) for url in transport.urls]
    assert queries[0]["rvlimit"] == ["1"]
    assert queries[1]["action"] == ["parse"] and queries[1]["oldid"] == ["401"]
    assert all("continue" not in query and "rvcontinue" not in query for query in queries)
    output = tmp_path / "corpus"
    assert json.loads((output / "01-metadata.json").read_bytes()) == value
    assert json.loads((output / "manifest.json").read_bytes())["status"] == "complete"


@pytest.mark.parametrize(
    "continuation",
    [
        None,
        [],
        {},
        {"continue": "||info"},
        {"rvcontinue": "20260824161056|400"},
        {"rvcontinue": "20260824161056|400", "continue": "||"},
        {"rvcontinue": "20260824161056|400", "continue": "||info", "other": "cursor"},
        {"rvcontinue": 400, "continue": "||info"},
        {"rvcontinue": True, "continue": "||info"},
        {"rvcontinue": "20260824|400", "continue": "||info"},
        {"rvcontinue": "20260231161056|400", "continue": "||info"},
        {"rvcontinue": "20260824161056|0", "continue": "||info"},
        {"rvcontinue": "20260824161056|-1", "continue": "||info"},
        {"rvcontinue": "20260824161056|400\n", "continue": "||info"},
        {"rvcontinue": "20260824161056|" + "1" * 21, "continue": "||info"},
    ],
)
def test_unknown_or_malformed_metadata_continuation_fails_without_following(tmp_path, continuation):
    value = metadata()
    value["continue"] = continuation
    transport = FixtureTransport([value])
    output = tmp_path / "invalid"
    with pytest.raises(wiki.CollectionError, match="continuation"):
        wiki.collect(
            [wiki.ArticleRequest(language="en", title="Synthetic article")],
            output,
            transport=transport,
        )
    assert len(transport.urls) == 1
    assert not (output / "corpus.json").exists()
    assert json.loads((output / "manifest.json").read_bytes())["status"] == "failed"


def test_even_well_formed_revision_continuation_is_rejected_for_parse(tmp_path):
    value = parsed()
    value["continue"] = {"rvcontinue": "20260824161056|400", "continue": "||info"}
    transport = FixtureTransport([metadata(), value])
    output = tmp_path / "invalid"
    with pytest.raises(wiki.CollectionError, match="continuation"):
        wiki.collect(
            [wiki.ArticleRequest(language="en", title="Synthetic article")],
            output,
            transport=transport,
            sleep=lambda _: None,
        )
    assert len(transport.urls) == 2
    assert not (output / "corpus.json").exists()
    assert json.loads((output / "01-parse.json").read_bytes()) == value


@pytest.mark.parametrize("field", ["error", "errors", "warnings"])
def test_valid_metadata_continuation_does_not_override_api_errors(tmp_path, field):
    value = metadata()
    value["continue"] = {"rvcontinue": "20260824161056|400", "continue": "||info"}
    value[field] = {"synthetic": "failure"}
    with pytest.raises(wiki.CollectionError, match="error or warning"):
        collect_fixture(tmp_path, [value])


@pytest.mark.parametrize("latest", [None, True, "401", 0, 400, 402])
@pytest.mark.parametrize("with_continuation", [False, True])
def test_metadata_must_report_exactly_the_latest_revision(tmp_path, latest, with_continuation):
    value = metadata()
    if latest is None:
        del value["query"]["pages"][0]["lastrevid"]
    else:
        value["query"]["pages"][0]["lastrevid"] = latest
    if with_continuation:
        value["continue"] = {"rvcontinue": "20260824161056|400", "continue": "||info"}
    with pytest.raises(wiki.CollectionError, match="latest revision"):
        collect_fixture(tmp_path, [value])
    assert not (tmp_path / "corpus" / "corpus.json").exists()


def test_unicode_hashes_attribution_and_standard_license_are_preserved(tmp_path):
    corpus, _ = collect_fixture(tmp_path)
    source = corpus.sources[0]
    assert source.html_sha256 == hashlib.sha256(HTML.encode()).hexdigest()
    assert source.text_sha256 == hashlib.sha256(TEXT.encode()).hexdigest()
    assert source.source_id == f"wiki-en-17-401-{source.text_sha256}"
    assert source.license == "CC-BY-SA-4.0" and source.license_url == wiki.LICENSE_URL
    assert source.title in source.attribution and "Wikipedia contributors" in source.attribution
    assert source.revision_url in source.attribution and source.history_url in source.attribution
    assert source.license_url in source.attribution and "Modified:" in source.attribution
    assert "not project MIT" in " ".join(source.modifications)
    assert "third-party restrictions" in " ".join(source.modifications)
    assert "transcluded templates" in " ".join(source.modifications)
    assert "fetch time" in " ".join(source.modifications)
    assert "license" not in json.loads((tmp_path / "corpus" / "manifest.json").read_bytes())
    assert (
        corpus.digest()
        == wiki.WikipediaCorpus.model_validate_json(corpus.model_dump_json()).digest()
    )


def test_japanese_urls_and_hashes_do_not_normalize_unicode(tmp_path):
    transport = FixtureTransport(
        [
            metadata(title="合成記事"),
            parsed(title="合成記事", html="<p>日本語 e\u0301 é</p>"),
        ]
    )
    corpus = wiki.collect(
        [wiki.ArticleRequest(language="ja", title="合成記事")],
        tmp_path / "ja",
        transport=transport,
        sleep=lambda _: None,
    )
    source = corpus.sources[0]
    assert source.text == "日本語 e\u0301 é"
    assert source.source_id.startswith("wiki-ja-17-401-")
    assert source.text_sha256 == wiki.sha256("日本語 e\u0301 é".encode())
    assert all(url.startswith("https://ja.wikipedia.org/") for url in transport.urls)
    assert urlsplit(source.article_url).hostname == "ja.wikipedia.org"
    assert parse_qs(urlsplit(source.revision_url).query) == {
        "title": ["合成記事"],
        "oldid": ["401"],
    }


@pytest.mark.parametrize(
    "damage",
    [
        {"page_id": "17"},
        {"page_id": True},
        {"revision_id": 0},
        {"language": "fr"},
        {"extra": "not permitted"},
        {"license": "MIT"},
        {"text_sha256": "0" * 64},
        {"source_id": "invented"},
        {"attribution": "project MIT"},
        {"modifications": ["No changes"]},
        {"revision_timestamp": "2026-02-31T00:00:00Z"},
        {"retrieved_at": "2026-09-18T00:00:00+00:00"},
        {"html_sha256": "UPPERCASE"},
        {"text": "altered"},
    ],
)
def test_source_schema_rejects_coercions_and_broken_provenance(tmp_path, damage):
    corpus, _ = collect_fixture(tmp_path)
    with pytest.raises(ValidationError):
        wiki.WikipediaSource.model_validate({**corpus.sources[0].model_dump(), **damage})


@pytest.mark.parametrize(
    "field,url",
    [
        ("article_url", "http://en.wikipedia.org/wiki/Synthetic_article"),
        ("article_url", "https://en.wikipedia.org.evil.test/wiki/Synthetic_article"),
        ("article_url", "https://en.wikipedia.org:443/wiki/Synthetic_article"),
        ("article_url", "https://user@en.wikipedia.org/wiki/Synthetic_article"),
        ("revision_url", "https://en.wikipedia.org/w/index.php?oldid=999"),
        ("revision_url", "https://ja.wikipedia.org/w/index.php?title=Synthetic+article&oldid=401"),
        ("history_url", "https://en.wikipedia.org/w/index.php?title=Other&action=history"),
        ("license_url", "https://example.test/license"),
    ],
)
def test_source_urls_are_exact_https_language_title_revision_urls(tmp_path, field, url):
    corpus, _ = collect_fixture(tmp_path)
    with pytest.raises(ValidationError):
        wiki.WikipediaSource.model_validate({**corpus.sources[0].model_dump(), field: url})


def test_corpus_rejects_duplicates_empty_and_too_many_sources(tmp_path):
    corpus, _ = collect_fixture(tmp_path)
    for sources in ([], corpus.sources * 2, corpus.sources * 21):
        with pytest.raises(ValidationError):
            wiki.WikipediaCorpus(sources=sources)
    with pytest.raises(ValidationError):
        wiki.WikipediaCorpus.model_validate({**corpus.model_dump(), "unexpected": True})


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"error": {"code": "maxlag", "info": "synthetic busy"}},
        {"warnings": {"query": {"*": "synthetic warning"}}},
        {"query": {"pages": []}},
        {"query": {"pages": [{"missing": True, "ns": 0, "title": "Missing"}]}},
        {"query": {"pages": [{"invalid": True, "ns": 0, "title": "Invalid"}]}},
        {"query": {"redirects": [], "pages": []}},
        {"query": {"interwiki": [], "pages": []}},
        {"continue": {"continue": "synthetic"}},
        [],
        b"<html>not JSON</html>",
        b"\xff",
    ],
)
def test_missing_errors_and_invalid_responses_fail_without_retry(tmp_path, payload):
    transport = FixtureTransport([payload])
    output = tmp_path / "failed"
    with pytest.raises(wiki.CollectionError):
        wiki.collect(
            [wiki.ArticleRequest(language="en", title="Synthetic article")],
            output,
            transport=transport,
        )
    assert len(transport.urls) == 1 and not (output / "corpus.json").exists()
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert manifest["status"] == "failed"
    assert manifest["requests"][0]["url"] == transport.urls[0]
    assert (output / "01-metadata.json").exists()


@pytest.mark.parametrize(
    "damage",
    [
        {"redirect": True},
        {"ns": 1},
        {"ns": False},
        {"pageid": True},
        {"revisions": []},
        {"revisions": [{"revid": "401", "timestamp": "2026-09-18T00:00:00Z"}]},
    ],
)
def test_redirect_nonarticle_and_malformed_revision_are_rejected(tmp_path, damage):
    payload = metadata()
    payload["query"]["pages"][0].update(damage)
    with pytest.raises(wiki.CollectionError):
        collect_fixture(tmp_path, [payload])


@pytest.mark.parametrize(
    "damage",
    [
        {"revid": 402},
        {"pageid": 18},
        {"title": "Unrelated article"},
        {"revid": None},
        {"text": None},
        {"text": {"*": HTML}},
    ],
)
def test_parse_must_confirm_exact_revision_and_page(tmp_path, damage):
    payload = parsed()
    payload["parse"].update(damage)
    with pytest.raises(wiki.CollectionError):
        collect_fixture(tmp_path, [metadata(), payload])
    assert not (tmp_path / "corpus" / "corpus.json").exists()
    assert (tmp_path / "corpus" / "01-metadata.json").exists()
    assert (tmp_path / "corpus" / "01-parse.json").exists()


@pytest.mark.parametrize(
    "options",
    [
        {"status": 301},
        {"status": 429},
        {"status": 503},
        {"content_type": "text/html"},
        {"redirect": True},
    ],
)
def test_http_errors_and_redirects_are_retained_and_never_followed(tmp_path, options):
    transport = FixtureTransport([metadata()], **options)
    output = tmp_path / "failed"
    with pytest.raises(wiki.CollectionError):
        wiki.collect(
            [wiki.ArticleRequest(language="en", title="Synthetic article")],
            output,
            transport=transport,
        )
    assert len(transport.urls) == 1
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert manifest["status"] == "failed"
    assert manifest["requests"][0]["status"] == options.get("status", 200)


def test_transport_failure_keeps_planned_endpoint_and_error(tmp_path):
    transport = FixtureTransport([wiki.CollectionError("API transport failed (URLError)")])
    with pytest.raises(wiki.CollectionError, match="URLError"):
        wiki.collect(
            [wiki.ArticleRequest(language="en", title="Synthetic article")],
            tmp_path / "failure",
            transport=transport,
        )
    manifest = json.loads((tmp_path / "failure" / "manifest.json").read_bytes())
    assert manifest["status"] == "failed"
    assert manifest["requests"][0]["stage"] == "metadata"
    assert "status" not in manifest["requests"][0]


def test_html_conversion_omits_nontext_and_navigation():
    html = """
    <h1>OMIT HEADING</h1><style>OMIT STYLE</style><script>OMIT SCRIPT</script>
    <table><tr><td><p>OMIT TABLE</p></td></tr></table>
    <nav><p>OMIT NAV</p></nav><div class="hatnote"><p>OMIT HATNOTE</p></div>
    <div class="references"><p>OMIT REFERENCES</p></div>
    <blockquote><p>OMIT QUOTATION</p></blockquote>
    <p> Alpha <b>bold</b> &amp; &nbsp; 日本語<br>next
    <img src="https://example.test/image" alt="OMIT IMAGE">
    <span hidden>OMIT HIDDEN</span><span aria-hidden="true">OMIT ARIA</span>
    <span style="display: none">OMIT CSS</span>
    <sup class="reference">OMIT CITATION</sup><q>OMIT QUOTE</q> done. </p>
    <figure><p>OMIT CAPTION</p></figure><p>Second paragraph.</p>
    <p>Incomplete paragraph
    """
    assert wiki.lead_text(html) == "Alpha bold & 日本語 next done.\n\nSecond paragraph."


def test_paragraph_and_utf8_caps_never_cut_or_skip_an_oversized_first_paragraph():
    assert wiki.lead_text("".join(f"<p>Paragraph {i}.</p>" for i in range(8))) == (
        "\n\n".join(f"Paragraph {i}." for i in range(6))
    )
    assert wiki.lead_text("<p>Complete.</p><p>" + "日" * 4000 + "</p>") == "Complete."
    with pytest.raises(ValueError, match="No complete"):
        wiki.lead_text("<p>" + "日" * 4001 + "</p><p>Do not skip ahead.</p>")
    for html in ("<p>Incomplete", "<p> </p>", "<table><p>Only a table</p></table>"):
        with pytest.raises(ValueError, match="No complete"):
            wiki.lead_text(html)
    with pytest.raises(ValueError, match="HTML exceeds"):
        wiki.lead_text("x" * (wiki.MAX_RESPONSE_BYTES + 1))


def test_response_cap_retains_only_bounded_body(tmp_path):
    transport = FixtureTransport([b"x" * (wiki.MAX_RESPONSE_BYTES + 1)])
    with pytest.raises(wiki.CollectionError, match="response byte cap"):
        wiki.collect(
            [wiki.ArticleRequest(language="en", title="Synthetic article")],
            tmp_path / "capped",
            transport=transport,
        )
    assert (tmp_path / "capped" / "01-metadata.json").stat().st_size == wiki.MAX_RESPONSE_BYTES
    manifest = json.loads((tmp_path / "capped" / "manifest.json").read_bytes())
    assert manifest["requests"][0]["body_complete"] is False
    assert manifest["status"] == "failed"


def test_total_artifact_cap_fails_explicitly_with_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(wiki, "MAX_ARTIFACT_BYTES", 2 * wiki.MANIFEST_RESERVE + 1)
    with pytest.raises(wiki.CollectionError, match="Total artifact"):
        collect_fixture(tmp_path)
    output = tmp_path / "corpus"
    assert json.loads((output / "manifest.json").read_bytes())["status"] == "failed"
    assert not (output / "corpus.json").exists()
    assert sum(path.stat().st_size for path in output.iterdir()) <= wiki.MAX_ARTIFACT_BYTES


@pytest.mark.parametrize("delay", [0, 0.99, -1, 61, float("nan"), float("inf")])
def test_serial_delay_cannot_be_disabled(tmp_path, delay):
    with pytest.raises(ValueError, match="Serial delay"):
        collect_fixture(tmp_path, delay_seconds=delay)
    assert not (tmp_path / "corpus").exists()


def test_no_overwrite_and_invalid_plans_never_fetch(tmp_path):
    transport = FixtureTransport([])
    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "marker"
    marker.write_text("Do not overwrite")
    valid = [wiki.ArticleRequest(language="en", title="Synthetic article")]
    with pytest.raises(FileExistsError):
        wiki.collect(valid, existing, transport=transport)
    assert marker.read_text() == "Do not overwrite"
    for requests in ([], valid * 2, valid * 21):
        with pytest.raises(ValueError):
            wiki.collect(requests, tmp_path / "invalid", transport=transport)
    assert transport.urls == []


def test_plan_schema_is_closed_bounded_and_default_is_fixed(tmp_path):
    path = tmp_path / "plan.json"
    default = wiki.default_plan()
    assert [(item.language, item.title) for item in default] == [
        ("en", "PostgreSQL"),
        ("ja", "PostgreSQL"),
        ("en", "Solar System"),
        ("ja", "太陽系"),
        ("en", "Atomic clock"),
        ("ja", "原子時計"),
    ]
    path.write_bytes(json.dumps([item.model_dump() for item in default]).encode())
    assert wiki.load_plan(path) == default
    default.pop()
    assert len(wiki.default_plan()) == 6
    for invalid in (
        {},
        [],
        [{"language": "en", "title": "Valid", "url": "https://example.test"}],
        [{"language": "de", "title": "Wrong language"}],
        [{"language": "en", "title": 42}],
        [{"language": "en", "title": "Title#fragment"}],
        [{"language": "en", "title": " " + "Blank"}],
        [{"language": "en", "title": "A_B"}, {"language": "en", "title": "a b"}],
    ):
        path.write_bytes(json.dumps(invalid).encode())
        with pytest.raises(ValueError):
            wiki.load_plan(path)
    path.write_bytes(b" " * (wiki.MAX_PLAN_BYTES + 1))
    with pytest.raises(ValueError, match="byte limit"):
        wiki.load_plan(path)


def test_later_failure_preserves_audit_but_does_not_publish_partial_corpus(tmp_path):
    transport = FixtureTransport([metadata(), parsed(), {"error": {"code": "maxlag"}}])
    delays = []
    with pytest.raises(wiki.CollectionError):
        wiki.collect(
            [
                wiki.ArticleRequest(language="en", title="Synthetic article"),
                wiki.ArticleRequest(language="ja", title="合成記事"),
            ],
            tmp_path / "partial",
            transport=transport,
            sleep=delays.append,
        )
    manifest = json.loads((tmp_path / "partial" / "manifest.json").read_bytes())
    assert manifest["status"] == "failed" and len(manifest["completed_source_ids"]) == 1
    assert len(transport.urls) == 3 and delays == [1.0, 1.0]
    assert not (tmp_path / "partial" / "corpus.json").exists()


class FakeHTTPResponse(io.BytesIO):
    code = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, body, url):
        super().__init__(body)
        self.url = url

    def geturl(self):
        return self.url


def test_official_transport_disables_proxies_and_redirects_and_identifies_itself(monkeypatch):
    captured = {}

    class FakeOpener:
        def open(self, request, *, timeout):
            captured.update(request=request, timeout=timeout)
            return FakeHTTPResponse(b"{}", request.full_url)

    def build(*handlers):
        captured["handlers"] = handlers
        return FakeOpener()

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
    monkeypatch.setattr(wiki, "build_opener", build)
    client = wiki.OfficialWikipediaClient()
    url = "https://en.wikipedia.org/w/api.php?action=query&maxlag=5"
    assert client.get(url).body == b"{}"
    proxy, redirects, https = captured["handlers"]
    assert proxy.proxies == {}
    assert redirects.redirect_request(None, None, 302, "", None, "https://evil.test") is None
    assert isinstance(https, wiki.HTTPSHandler)
    request = captured["request"]
    assert request.get_method() == "GET"
    assert request.get_header("User-agent") == wiki.USER_AGENT
    assert "Mozilla" not in wiki.USER_AGENT
    assert captured["timeout"] == wiki.TIMEOUT_SECONDS
    for bad in (
        "http://en.wikipedia.org/w/api.php?",
        "https://en.wikipedia.org.evil.test/w/api.php?",
        "https://en.wikipedia.org:443/w/api.php?",
        "https://fr.wikipedia.org/w/api.php?",
        "https://en.wikipedia.org/wiki/Article",
    ):
        with pytest.raises(wiki.CollectionError, match="allowlisted"):
            client.get(bad)


def test_official_transport_retains_http_errors_and_does_not_retry(monkeypatch):
    calls = []
    url = "https://en.wikipedia.org/w/api.php?action=query"

    class FakeOpener:
        def open(self, request, *, timeout):
            calls.append(request.full_url)
            raise HTTPError(
                url,
                429,
                "Synthetic",
                {"Content-Type": "application/json"},
                io.BytesIO(b'{"error":"rate limited"}'),
            )

    monkeypatch.setattr(wiki, "build_opener", lambda *args: FakeOpener())
    response = wiki.OfficialWikipediaClient().get(url)
    assert response.status == 429 and response.body == b'{"error":"rate limited"}'
    assert calls == [url]


def test_official_transport_network_error_is_explicit(monkeypatch):
    class FakeOpener:
        def open(self, request, *, timeout):
            raise URLError("synthetic failure")

    monkeypatch.setattr(wiki, "build_opener", lambda *args: FakeOpener())
    with pytest.raises(wiki.CollectionError, match="URLError"):
        wiki.OfficialWikipediaClient().get("https://en.wikipedia.org/w/api.php?action=query")


def test_official_transport_bounds_reads_and_wall_clock(monkeypatch):
    body = b"x" * (wiki.MAX_RESPONSE_BYTES + 100)

    class FakeOpener:
        def open(self, request, *, timeout):
            return FakeHTTPResponse(body, request.full_url)

    monkeypatch.setattr(wiki, "build_opener", lambda *args: FakeOpener())
    client = wiki.OfficialWikipediaClient()
    url = "https://en.wikipedia.org/w/api.php?action=query"
    assert len(client.get(url).body) == wiki.MAX_RESPONSE_BYTES + 1
    times = iter([0, wiki.TIMEOUT_SECONDS + 1])
    monkeypatch.setattr(wiki.time, "monotonic", lambda: next(times))
    with pytest.raises(wiki.CollectionError, match="wall-clock"):
        client.get(url)


@pytest.mark.parametrize(
    "title,timestamp",
    [
        ("", "2026-09-18T00:00:00Z"),
        ("Wrong#fragment", "2026-09-18T00:00:00Z"),
        ("Synthetic", "2026-99-18T00:00:00Z"),
    ],
)
def test_bad_metadata_is_rejected_before_requesting_parse(tmp_path, title, timestamp):
    value = metadata(title=title)
    value["query"]["pages"][0]["revisions"][0]["timestamp"] = timestamp
    transport = FixtureTransport([value])
    with pytest.raises(wiki.CollectionError):
        wiki.collect(
            [wiki.ArticleRequest(language="en", title="Synthetic article")],
            tmp_path / "invalid",
            transport=transport,
        )
    assert len(transport.urls) == 1


def test_cli_requires_explicit_plan_output_and_never_echoes_payload(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["evaluation_wikipedia"])
    with pytest.raises(SystemExit) as exit_info:
        wiki.main()
    assert exit_info.value.code == 2
    path = tmp_path / "plan.json"
    path.write_text('[{"language":"en","title":42,"secret":"DO_NOT_ECHO"}]')
    monkeypatch.setattr(
        "sys.argv", ["evaluation_wikipedia", "--plan", str(path), "--output", str(tmp_path / "out")]
    )
    with pytest.raises(SystemExit) as exit_info:
        wiki.main()
    assert exit_info.value.code == 2
    assert "DO_NOT_ECHO" not in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_cli_success_reports_only_count_and_digest(tmp_path, monkeypatch, capsys):
    original_collect = wiki.collect
    transport = FixtureTransport([metadata(), parsed()])

    def offline_collect(plan, output, **kwargs):
        return original_collect(
            plan,
            output,
            transport=transport,
            sleep=lambda _: None,
            delay_seconds=kwargs["delay_seconds"],
        )

    path = tmp_path / "plan.json"
    path.write_text('[{"language":"en","title":"Synthetic article"}]')
    output = tmp_path / "out"
    monkeypatch.setattr(wiki, "collect", offline_collect)
    monkeypatch.setattr(wiki, "OfficialWikipediaClient", lambda: transport)
    monkeypatch.setattr(
        "sys.argv", ["evaluation_wikipedia", "--plan", str(path), "--output", str(output)]
    )
    wiki.main()
    printed = capsys.readouterr().out
    assert printed.startswith("Collected 1 sources; corpus SHA256 ")
    assert "Synthetic" not in printed and "日本語" not in printed
    assert (output / "corpus.json").exists()


def test_captured_text_changes_change_source_identity_and_corpus_digest(tmp_path):
    corpus, _ = collect_fixture(tmp_path)
    value = copy.deepcopy(corpus.sources[0].model_dump())
    value["text"] += " Changed."
    value["text_sha256"] = wiki.sha256(value["text"].encode())
    value["source_id"] = wiki.source_id("en", 17, 401, value["text_sha256"])
    changed = wiki.WikipediaSource.model_validate(value)
    assert changed.source_id != corpus.sources[0].source_id
    assert wiki.WikipediaCorpus(sources=[changed]).digest() != corpus.digest()
