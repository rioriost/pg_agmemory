from __future__ import annotations

import logging
import re
import threading
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import psycopg

from pg_agmemory.query_planning import ENGLISH_PROFILE as ENGLISH_PROFILE
from pg_agmemory.query_planning import JAPANESE_PROFILE as JAPANESE_PROFILE
from pg_agmemory.query_planning import SEARCH_PROFILES as SEARCH_PROFILES

if TYPE_CHECKING:
    from janome.tokenizer import Tokenizer

JAPANESE_RUN = re.compile(
    r"[\u3005-\u3007\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uf900-\ufaff\U00020000-\U0002fa1f]+"
)
_lock = threading.Lock()
PROJECTED_PROFILES = (JAPANESE_PROFILE, ENGLISH_PROFILE)


class TokenizerUnavailable(RuntimeError):
    pass


class _SafeDictionaryErrors(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = "japanese_dictionary_error"
        record.args = ()
        record.exc_info = record.exc_text = record.stack_info = None
        return True


# Janome's corrupt-dictionary diagnostic otherwise logs the input before sys.exit.
logging.getLogger("janome.dic").addFilter(_SafeDictionaryErrors())


@lru_cache(maxsize=1)
def _tokenizer() -> Tokenizer:
    from janome.tokenizer import Tokenizer

    tokenizer = Tokenizer(wakati=True)
    tokenizer.matcher.max_cached_word_len = 0
    return tokenizer


def segment(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        tokens = list(_tokenizer().tokenize(match.group(), wakati=True))
        if not all(isinstance(token, str) for token in tokens):
            raise RuntimeError("Unexpected Japanese tokenizer output")
        return " " + " ".join(tokens) + " "

    # Only dictionary resources are reused; never cache source text or token streams.
    with _lock:
        try:
            return JAPANESE_RUN.sub(replace, text)
        except SystemExit:
            raise TokenizerUnavailable("Japanese tokenizer unavailable") from None


def text_search_config(profile: str) -> str:
    if profile == ENGLISH_PROFILE:
        return "pg_catalog.english"
    if profile in ("simple-v1", JAPANESE_PROFILE):
        return "pg_catalog.simple"
    raise ValueError("Unsupported lexical profile")


def rebuild(
    conn: psycopg.Connection[tuple[Any, ...]], *, profile: str = JAPANESE_PROFILE,
) -> dict[str, str | int]:
    """Replace only the selected projection in an offline maintenance transaction."""
    if profile not in PROJECTED_PROFILES:
        raise ValueError("Unsupported projected lexical profile")
    configuration = text_search_config(profile)
    conn.execute("SET LOCAL row_security = off")
    conn.execute("DELETE FROM memory.episode_lexical WHERE profile = %s", (profile,))
    conn.execute("DELETE FROM memory.assertion_lexical WHERE profile = %s", (profile,))
    episodes = revisions = 0
    with conn.cursor(name="lexical_episodes") as cursor:
        cursor.execute(
            """SELECT e.tenant_id,e.id,e.scope_id,e.content
               FROM memory.episode e JOIN memory.object o USING (tenant_id,id)
               WHERE NOT EXISTS (SELECT 1 FROM memory_ops.object_tombstone t
                   WHERE t.tenant_id = o.tenant_id AND t.object_id = o.id)"""
        )
        for tenant, object_id, scope, content in cursor:
            conn.execute(
                """INSERT INTO memory.episode_lexical
                   (tenant_id,episode_id,scope_id,profile,search_text)
                   VALUES (%s,%s,%s,%s,to_tsvector(%s::regconfig,%s))""",
                (
                    tenant, object_id, scope, profile, configuration,
                    segment(content) if profile == JAPANESE_PROFILE else content,
                ),
            )
            episodes += 1
    with conn.cursor(name="lexical_assertions") as cursor:
        cursor.execute(
            """SELECT a.tenant_id,a.id,a.scope_id,r.revision,
                      a.subject || ' ' || a.predicate || ' ' || r.value
               FROM memory.assertion a JOIN memory.object o USING (tenant_id,id)
               JOIN memory.assertion_revision r
                 ON r.tenant_id = a.tenant_id AND r.assertion_id = a.id
               WHERE NOT EXISTS (SELECT 1 FROM memory_ops.object_tombstone t
                   WHERE t.tenant_id = o.tenant_id AND t.object_id = o.id)"""
        )
        for tenant, object_id, scope, revision, content in cursor:
            conn.execute(
                """INSERT INTO memory.assertion_lexical
                   (tenant_id,assertion_id,revision,scope_id,profile,search_text)
                   VALUES (%s,%s,%s,%s,%s,to_tsvector(%s::regconfig,%s))""",
                (
                    tenant, object_id, revision, scope, profile, configuration,
                    segment(content) if profile == JAPANESE_PROFILE else content,
                ),
            )
            revisions += 1
    return {
        "profile": profile,
        "episodes": episodes,
        "assertion_revisions": revisions,
    }
