"""Password login with a signed session cookie.

Every request needs the cookie, including local ones: traffic through the tunnel also arrives from 127.0.0.1,
so "local" can't be trusted. The password and the signing secret come from the environment (.env, see
scripts/start.sh), never from the code.
"""
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque

from fastapi import Request, WebSocket

COOKIE = "happica_session"
SESSION_DAYS = 30
OPEN_PATHS = {"/api/login", "/api/session"}  # reachable without a session
MAX_ATTEMPTS, WINDOW = 8, 15 * 60  # failed logins per client per 15 minutes
# Client IPs behind proxies come from headers a client can fake, so also cap failures from everyone together:
# a guesser rotating fake IPs still gets at most this many tries per window.
MAX_ATTEMPTS_ALL = 30

PASSWORD = os.getenv("HAPPICA_PASSWORD", "")
SECRET = os.getenv("HAPPICA_SECRET", "").encode()
if not PASSWORD or len(SECRET) < 16:
    raise RuntimeError("Set HAPPICA_PASSWORD and HAPPICA_SECRET (scripts/start.sh creates them in .env)")

_failures: dict[str, deque] = defaultdict(deque)


def _sign(payload: str) -> str:
    return hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()


def new_session() -> str:
    expires = int(time.time()) + SESSION_DAYS * 86400
    payload = f"{expires}.{secrets.token_hex(8)}"
    return f"{payload}.{_sign(payload)}"


def valid(token: str | None) -> bool:
    if not token or token.count(".") != 2:
        return False
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return False
    try:
        return int(payload.split(".")[0]) > time.time()
    except ValueError:
        return False


def client_id(conn: Request | WebSocket) -> str:
    """Who's asking, for rate limiting: the real visitor behind Cloudflare / Railway when present."""
    h = conn.headers
    return h.get("cf-connecting-ip") or h.get("x-forwarded-for", "").split(",")[0].strip() or (conn.client.host if conn.client else "?")


def _recent(key: str) -> int:
    q = _failures[key]
    while q and q[0] < time.time() - WINDOW:
        q.popleft()
    return len(q)


def allowed_attempt(who: str) -> bool:
    return _recent(who) < MAX_ATTEMPTS and _recent("*") < MAX_ATTEMPTS_ALL


def check_password(who: str, password: str) -> bool:
    ok = hmac.compare_digest(password.encode(), PASSWORD.encode())
    if not ok:
        _failures[who].append(time.time())
        _failures["*"].append(time.time())
    return ok


def is_secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
