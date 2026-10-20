"""API keys for other apps (REST API v1). Only a SHA-256 hash of each key is stored; the key itself is
shown once, when it's created in the web app."""
import hashlib
import secrets
import sqlite3
import time
import uuid

from config import DB_PATH

PREFIX = "hap_"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class KeyStore:
    def __init__(self, path=DB_PATH):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY, name TEXT, hint TEXT, hash TEXT UNIQUE,
                created REAL, last_used REAL, revoked INTEGER NOT NULL DEFAULT 0)"""
        )
        self.db.commit()

    def create(self, name: str) -> tuple[dict, str]:
        key = PREFIX + secrets.token_urlsafe(32)
        row = dict(id=uuid.uuid4().hex[:12], name=name, hint=f"{key[:8]}…{key[-4:]}", hash=_hash(key),
                   created=time.time(), last_used=None, revoked=0)
        self.db.execute(f"INSERT INTO api_keys ({','.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
        self.db.commit()
        return self._public(row), key

    def verify(self, key: str) -> dict | None:
        """The key's record if it's valid and not revoked (and note that it was used)."""
        if not key.startswith(PREFIX):
            return None
        row = self.db.execute("SELECT * FROM api_keys WHERE hash = ? AND revoked = 0", (_hash(key),)).fetchone()
        if not row:
            return None
        now = time.time()
        if not row["last_used"] or now - row["last_used"] > 60:  # don't write on every request
            self.db.execute("UPDATE api_keys SET last_used = ? WHERE id = ?", (now, row["id"]))
            self.db.commit()
        return self._public(dict(row))

    def list(self) -> list[dict]:
        rows = self.db.execute("SELECT * FROM api_keys WHERE revoked = 0 ORDER BY created DESC").fetchall()
        return [self._public(dict(r)) for r in rows]

    def revoke(self, key_id: str) -> bool:
        cur = self.db.execute("UPDATE api_keys SET revoked = 1 WHERE id = ? AND revoked = 0", (key_id,))
        self.db.commit()
        return cur.rowcount > 0

    @staticmethod
    def _public(row: dict) -> dict:
        return {k: row[k] for k in ("id", "name", "hint", "created", "last_used")}


def from_headers(headers) -> str | None:
    """An API key from `Authorization: Bearer hap_…` or `X-API-Key: hap_…`."""
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return headers.get("x-api-key") or None
