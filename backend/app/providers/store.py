"""Content-addressed store of raw upstream responses (§12), in Postgres.

Every provider read goes through here first. What a stored answer may be reused for is its scope:
  immutable        a confirmed tx, a spent+confirmed outspend, an Etherscan query with a past endblock
  snapshot:<blk>   a volatile read (address history page, stats) made for a trace pinned at <blk>
A trace pinned to one snapshot therefore replays byte-identically from the store, and `offline`
mode (eval run --offline) serves only from here. Bodies are canonical JSON (sorted keys) hashed
with sha256, so identical responses share one blob.
"""
import hashlib
import json

from app.db import connect


def canonical(body) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


class RawStore:
    def __init__(self, conn=None):
        self.conn = conn or connect(autocommit=True)

    def get(self, request: str, scope: str):
        row = self.conn.execute(
            "SELECT b.body FROM raw_response r JOIN raw_blob b USING (content_hash) WHERE r.request_key = %s",
            (f"{scope}|{request}",)).fetchone()
        return row[0] if row else None

    def put(self, request: str, scope: str, provider: str, body) -> str:
        text = canonical(body)
        h = hashlib.sha256(text.encode()).hexdigest()
        with self.conn.transaction():
            self.conn.execute("INSERT INTO raw_blob (content_hash, body) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                              (h, text))
            self.conn.execute(
                "INSERT INTO raw_response (request_key, request, scope, content_hash, provider) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (request_key) DO NOTHING",
                (f"{scope}|{request}", request, scope, h, provider))
        return h

    def latest(self, request: str):
        """Most recent stored answer to `request` under ANY scope -> (body, scope, fetched_at) | None.
        Only for degraded mode (provider down / 429): the caller must mark its result partial."""
        return self.conn.execute(
            "SELECT b.body, r.scope, r.fetched_at FROM raw_response r JOIN raw_blob b USING (content_hash) "
            "WHERE r.request = %s ORDER BY r.fetched_at DESC LIMIT 1", (request,)).fetchone()


def open_store():
    """RawStore if Postgres is reachable, else None (the provider then runs uncached, and says so)."""
    try:
        return RawStore()
    except Exception as e:  # noqa: BLE001 — a missing DB must not stop a host-side probe
        print(f"   ! raw store unavailable ({type(e).__name__}); running without the §12 cache")
        return None
