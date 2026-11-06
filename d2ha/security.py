"""Authentication building blocks: passwords, TOTP, tokens, CSRF, sessions, rate limits."""

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from functools import wraps
from typing import Deque, Dict, Optional, Tuple

import pyotp
import qrcode
import qrcode.image.svg
from flask import abort, current_app, g, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

MIN_PASSWORD_LENGTH = 10


# -- passwords -----------------------------------------------------------------

def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    if not stored_hash or not password:
        return False
    try:
        return check_password_hash(stored_hash, password)
    except (ValueError, TypeError):
        return False


def password_problem(password: str, confirm: str) -> Optional[str]:
    """Return an i18n key describing why the password is not acceptable, or None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return "error.password_short"
    if password != confirm:
        return "error.password_mismatch"
    if password.lower() in ("admin", "password", "changeme"):
        return "error.password_weak"
    return None


# -- TOTP ------------------------------------------------------------------------

_totp_lock = threading.Lock()
_last_totp_step: Dict[str, int] = {}


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username or "admin", issuer_name="D2HA")


def verify_totp(secret: str, code: str) -> bool:
    """Accept a code once: a code already used (same 30 s step or older) is refused."""
    code = (code or "").replace(" ", "")
    if not secret or not code.isdigit():
        return False
    totp = pyotp.TOTP(secret)
    now = time.time()
    for offset in (0, -1, 1):
        step = int(now // 30) + offset
        if hmac.compare_digest(totp.at(step * 30), code):
            with _totp_lock:
                if _last_totp_step.get(secret, -1) >= step:
                    return False
                _last_totp_step[secret] = step
            return True
    return False


def qr_data_uri(text: str) -> str:
    image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    encoded = base64.b64encode(image.to_string()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


# -- API token -------------------------------------------------------------------

def new_api_token() -> Tuple[str, str]:
    token = "d2ha_" + secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def api_token_valid(token: str, stored_hash: str) -> bool:
    if not token or not stored_hash:
        return False
    digest = hashlib.sha256(token.encode()).hexdigest()
    return hmac.compare_digest(digest, stored_hash)


def bearer_token() -> str:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


# -- login rate limiting ------------------------------------------------------------

class LoginLimiter:
    """Blocks an address after repeated failures, and everyone after a burst of them."""

    def __init__(self, per_ip: int = 5, overall: int = 30, window: int = 15 * 60):
        self.per_ip = per_ip
        self.overall = overall
        self.window = window
        self._lock = threading.Lock()
        self._by_ip: Dict[str, Deque[float]] = {}
        self._all: Deque[float] = deque()

    def _trim(self, items: Deque[float], now: float) -> None:
        while items and now - items[0] > self.window:
            items.popleft()

    def blocked(self, ip: str) -> bool:
        now = time.time()
        with self._lock:
            self._trim(self._all, now)
            attempts = self._by_ip.get(ip)
            if attempts is not None:
                self._trim(attempts, now)
                if not attempts:
                    del self._by_ip[ip]
            return len(self._all) >= self.overall or len(self._by_ip.get(ip, ())) >= self.per_ip

    def failed(self, ip: str) -> None:
        now = time.time()
        with self._lock:
            self._by_ip.setdefault(ip, deque()).append(now)
            self._all.append(now)

    def succeeded(self, ip: str) -> None:
        with self._lock:
            self._by_ip.pop(ip, None)


class RateLimiter:
    """At most ``limit`` calls per ``window`` seconds for each key."""

    def __init__(self, limit: int = 30, window: int = 60):
        self.limit = limit
        self.window = window
        self._lock = threading.Lock()
        self._calls: Dict[str, Deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            calls = self._calls.setdefault(key, deque())
            while calls and now - calls[0] > self.window:
                calls.popleft()
            if len(calls) >= self.limit:
                return False
            calls.append(now)
            if len(self._calls) > 1000:  # forget idle clients
                for idle in [k for k, v in self._calls.items() if not v]:
                    del self._calls[idle]
            return True


def client_ip() -> str:
    # Cloudflare sets CF-Connecting-IP; without it we use the TCP peer. The port is
    # meant to be reachable only through the tunnel (or localhost), so it can't be spoofed.
    return (request.headers.get("CF-Connecting-IP") or request.remote_addr or "unknown").strip()


# -- sessions ------------------------------------------------------------------------

def login_session(username: str, generation: int) -> None:
    session.clear()
    session.permanent = True
    session["user"] = username
    session["gen"] = generation
    session["seen"] = int(time.time())
    session["csrf"] = secrets.token_urlsafe(24)


def logout_session() -> None:
    session.clear()


def session_user() -> Optional[str]:
    """The logged-in username, enforcing password generation and idle timeout."""
    store = current_app.extensions["d2ha"].store
    user = session.get("user")
    if not user:
        return None
    auth = store.get("auth")
    if user != auth["username"] or session.get("gen") != auth["generation"]:
        session.clear()
        return None
    timeout = int(store.get("settings", "session_timeout_minutes", default=30)) * 60
    now = int(time.time())
    if timeout > 0 and now - int(session.get("seen", 0)) > timeout:
        session.clear()
        return None
    if now - int(session.get("seen", 0)) > 60:
        session["seen"] = now
    return user


def csrf_token() -> str:
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(24)
        session["csrf"] = token
    return token


def csrf_ok() -> bool:
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    expected = session.get("csrf", "")
    return bool(sent and expected) and hmac.compare_digest(sent, expected)


# -- decorators ------------------------------------------------------------------------

def page_login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        store = current_app.extensions["d2ha"].store
        if not store.configured:
            return redirect(url_for("web.setup"))
        if not session_user():
            return redirect(url_for("web.login", next=request.full_path.rstrip("?")))
        if request.method not in ("GET", "HEAD") and not csrf_ok():
            abort(400)
        return view(*args, **kwargs)

    return wrapper


def api_auth(allow_token: bool = True):
    """Session (with CSRF on writes) or, for Home Assistant, the API bearer token."""

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            store = current_app.extensions["d2ha"].store
            token = bearer_token()
            if token:
                if allow_token and api_token_valid(token, store.get("api_token_hash")):
                    g.via_token = True
                    return view(*args, **kwargs)
                return jsonify(error="unauthorized"), 401
            if not session_user():
                return jsonify(error="unauthorized"), 401
            if request.method not in ("GET", "HEAD") and not csrf_ok():
                return jsonify(error="csrf"), 403
            g.via_token = False
            return view(*args, **kwargs)

        return wrapper

    return decorator


def safe_next(target: Optional[str], fallback: str) -> str:
    """Only same-site relative paths are allowed as redirect targets."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return fallback
    return target
