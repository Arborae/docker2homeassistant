import pyotp

from security import (LoginLimiter, api_token_valid, hash_password, new_api_token, password_problem,
                      safe_next, verify_password, verify_totp)


def test_password_rules():
    assert password_problem("short", "short") == "error.password_short"
    assert password_problem("long enough pass", "different one!!") == "error.password_mismatch"
    assert password_problem("long enough pass", "long enough pass") is None
    assert verify_password(hash_password("long enough pass"), "long enough pass")
    assert not verify_password(hash_password("long enough pass"), "wrong")
    assert not verify_password("", "anything")


def test_totp_code_cannot_be_reused():
    secret = pyotp.random_base32()
    code = pyotp.TOTP(secret).now()
    assert verify_totp(secret, code)
    assert not verify_totp(secret, code)
    assert not verify_totp(secret, "12x456")


def test_login_limiter_per_address_and_overall():
    limiter = LoginLimiter(per_ip=3, overall=5)
    for _ in range(3):
        limiter.failed("1.1.1.1")
    assert limiter.blocked("1.1.1.1")
    assert not limiter.blocked("2.2.2.2")
    limiter.failed("2.2.2.2")
    limiter.failed("3.3.3.3")
    assert limiter.blocked("4.4.4.4")  # too many failures overall


def test_safe_next_only_allows_local_paths():
    assert safe_next("/settings", "/") == "/settings"
    for bad in ("https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)", None, ""):
        assert safe_next(bad, "/") == "/"


def test_api_token_is_stored_hashed():
    token, stored = new_api_token()
    assert token.startswith("d2ha_") and token not in stored
    assert api_token_valid(token, stored)
    assert not api_token_valid(token + "x", stored)
    assert not api_token_valid(token, "")
