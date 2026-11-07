"""Single-password login primitives (design §9.2): signed sessions, constant-time compare,
per-client failure rate limiting. No database access; the signing key comes from the caller."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


class SessionSigner:
    """Tokens are `<expiry>,<issued>.<hmac>`: no user id (single password), but the issue time
    lets a logout invalidate every token issued so far through a `valid_from` stamp."""

    def __init__(self, key: bytes, ttl_seconds: int = 30 * 24 * 3600) -> None:
        if len(key) < 16:
            raise ValueError("session key must be at least 16 bytes")
        self._key = key
        self.ttl_seconds = ttl_seconds

    def _sign(self, payload: bytes) -> bytes:
        return hmac.new(self._key, payload, hashlib.sha256).digest()

    def issue(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        payload = f"{int(now + self.ttl_seconds)},{now:.3f}".encode("ascii")
        return f"{_b64(payload)}.{_b64(self._sign(payload))}"

    def verify(self, token: str, now: float | None = None, valid_from: float = 0.0) -> bool:
        """True for an untampered, unexpired token issued at or after `valid_from`."""
        now = time.time() if now is None else now
        try:
            payload_text, sig_text = token.split(".")
            payload = _unb64(payload_text)
            signature = _unb64(sig_text)
        except (ValueError, TypeError):
            return False
        if not hmac.compare_digest(signature, self._sign(payload)):
            return False
        try:
            expiry_text, _, issued_text = payload.decode("ascii").partition(",")
            expiry = int(expiry_text)
            issued = float(issued_text) if issued_text else 0.0
        except (ValueError, UnicodeDecodeError):
            return False
        return expiry > now and issued >= valid_from


def password_matches(expected: str, given: str) -> bool:
    return hmac.compare_digest(expected.encode("utf-8"), given.encode("utf-8"))


class LoginRateLimiter:
    """In-memory sliding window per client key. State is lost on restart, which is fine."""

    def __init__(self, max_failures: int = 5, window_seconds: float = 60) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        cutoff = now - self.window_seconds
        recent = [t for t in self._failures.get(key, []) if t > cutoff]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def allowed(self, key: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return len(self._recent(key, now)) < self.max_failures

    PRUNE_ABOVE = 1000  # keys; a scan from many addresses must not grow memory without bound

    def record_failure(self, key: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._failures.setdefault(key, []).append(now)
        if len(self._failures) > self.PRUNE_ABOVE:
            for stale in list(self._failures):
                self._recent(stale, now)  # drops keys whose failures all left the window

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
