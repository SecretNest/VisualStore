from visualstore.auth import LoginRateLimiter, SessionSigner, password_matches

KEY = b"k" * 32


def test_token_round_trip():
    signer = SessionSigner(KEY, ttl_seconds=100)
    token = signer.issue(now=1000.0)
    assert signer.verify(token, now=1050.0)


def test_expired_token_is_rejected():
    signer = SessionSigner(KEY, ttl_seconds=100)
    token = signer.issue(now=1000.0)
    assert not signer.verify(token, now=1100.0)
    assert not signer.verify(token, now=5000.0)


def test_tampered_and_garbage_tokens_are_rejected_not_raised():
    """Review Focus 5: anything a browser might send must yield False, never an exception."""
    signer = SessionSigner(KEY, ttl_seconds=100)
    token = signer.issue(now=1000.0)
    payload, sig = token.split(".")
    assert not signer.verify(f"{payload}x.{sig}", now=1001.0)
    assert not signer.verify(f"{payload}.{sig[:-2]}zz", now=1001.0)
    for garbage in ["", ".", "a.b.c", "not-a-token", "\x00", payload, "..", "9999999999."]:
        assert not signer.verify(garbage, now=1001.0)


def test_token_from_another_key_is_rejected():
    token = SessionSigner(b"a" * 32, ttl_seconds=100).issue(now=1000.0)
    assert not SessionSigner(b"b" * 32, ttl_seconds=100).verify(token, now=1001.0)


def test_password_matches_is_exact():
    assert password_matches("secret", "secret")
    assert not password_matches("secret", "Secret")
    assert not password_matches("secret", "secret ")
    assert not password_matches("secret", "")


def test_rate_limiter_blocks_after_max_failures_and_recovers():
    limiter = LoginRateLimiter(max_failures=3, window_seconds=60)
    for i in range(3):
        assert limiter.allowed("1.2.3.4", now=100.0 + i)
        limiter.record_failure("1.2.3.4", now=100.0 + i)
    assert not limiter.allowed("1.2.3.4", now=110.0)
    assert limiter.allowed("5.6.7.8", now=110.0)  # other clients unaffected
    assert limiter.allowed("1.2.3.4", now=161.0)  # window passed


def test_reset_clears_failures():
    limiter = LoginRateLimiter(max_failures=1, window_seconds=60)
    limiter.record_failure("c", now=0.0)
    assert not limiter.allowed("c", now=1.0)
    limiter.reset("c")
    assert limiter.allowed("c", now=1.0)


def test_rate_limiter_prunes_stale_keys_so_memory_stays_bounded():
    limiter = LoginRateLimiter(max_failures=5, window_seconds=60)
    for i in range(1500):
        limiter.record_failure(f"10.0.{i // 256}.{i % 256}", now=0.0)
    assert len(limiter._failures) == 1500
    limiter.record_failure("fresh", now=61.0)  # everything from t=0 is stale now
    assert set(limiter._failures) == {"fresh"}


def test_tokens_issued_before_the_valid_from_stamp_are_rejected():
    """Logout invalidates every session issued so far (audit minor 15)."""
    signer = SessionSigner(b"k" * 32)
    old = signer.issue(now=1000.0)
    assert signer.verify(old, now=1001.0)
    assert not signer.verify(old, now=1001.0, valid_from=1000.5)
    new = signer.issue(now=1002.0)
    assert signer.verify(new, now=1003.0, valid_from=1000.5)
