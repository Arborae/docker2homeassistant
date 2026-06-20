import time
import threading
from flask import request, abort


def _client_ip() -> str:
    """Best-effort real client IP.

    Behind Cloudflare / a reverse proxy ``request.remote_addr`` is the proxy
    address, so every client would otherwise share the same rate-limit bucket.
    Prefer the forwarded client IP when present.
    """
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


class SimpleRateLimiter:
    def __init__(self, limit=30, window=60):
        self.limit = limit
        self.window = window
        self.requests = {}
        self._lock = threading.Lock()

    def is_allowed(self, ip, endpoint):
        now = time.time()
        key = f"{ip}:{endpoint}"

        with self._lock:
            # Keep only timestamps inside the window
            timestamps = [t for t in self.requests.get(key, []) if now - t < self.window]

            if len(timestamps) >= self.limit:
                self.requests[key] = timestamps
                return False

            timestamps.append(now)
            self.requests[key] = timestamps
            return True

limiter = SimpleRateLimiter()

def init_rate_limiter(app):
    @app.before_request
    def check_rate_limit():
        if request.method == "GET" or not request.path.startswith("/api/"):
            return None

        # Limit write/action endpoints
        if not limiter.is_allowed(_client_ip(), request.path):
            abort(429, description="Troppe richieste, riprova più tardi.")
