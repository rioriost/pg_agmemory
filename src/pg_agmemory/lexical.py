from __future__ import annotations

import logging
import re
import threading
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import psycopg

if TYPE_CHECKING:
    from janome.tokenizer import Tokenizer

JAPANESE_PROFILE = "ja-janome-0.5.0-v1"
SEARCH_PROFILES = ["simple-v1", JAPANESE_PROFILE]
JAPANESE_RUN = re.compile(
    r"[\u3005-\u3007\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uf900-\ufaff\U00020000-\U0002fa1f]+"
)
_lock = threading.Lock()


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


def rebuild(conn: psycopg.Connection[tuple[Any, ...]]) -> dict[str, str | int]:
    """Replace projections in the caller's offline maintenance transaction."""
    conn.execute("SET LOCAL row_security = off")
    conn.execute("DELETE FROM memory.episode_lexical")
    conn.execute("DELETE FROM memory.assertion_lexical")
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
                   VALUES (%s,%s,%s,%s,to_tsvector('simple',%s))""",
                (tenant, object_id, scope, JAPANESE_PROFILE, segment(content)),
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
                   VALUES (%s,%s,%s,%s,%s,to_tsvector('simple',%s))""",
                (tenant, object_id, revision, scope, JAPANESE_PROFILE, segment(content)),
            )
            revisions += 1
    return {
        "profile": JAPANESE_PROFILE,
        "episodes": episodes,
        "assertion_revisions": revisions,
    }
