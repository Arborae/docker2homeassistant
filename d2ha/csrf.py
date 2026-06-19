"""Lightweight CSRF protection middleware for Flask.

Generates a per-session CSRF token and validates it on all state-changing
requests (POST, PUT, DELETE, PATCH).  JSON API requests that carry the
correct ``Content-Type: application/json`` header are exempt because
browsers enforce the Same-Origin Policy on such requests – a cross-origin
page cannot send ``application/json`` without a CORS preflight.

Usage in ``app.py``::

    from csrf import init_csrf
    init_csrf(app)

In Jinja templates, include the token in every HTML form::

    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
"""

import secrets
from typing import Optional

from flask import Flask, Request, abort, request, session

_CSRF_TOKEN_KEY = "_csrf_token"
_CSRF_FORM_FIELD = "csrf_token"
_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})

# Paths that are exempt from CSRF checks (e.g. health endpoints).
_EXEMPT_PATHS: frozenset = frozenset({"/api/health"})


def _generate_csrf_token() -> str:
    """Return a fresh 64-character hex token."""
    return secrets.token_hex(32)


def _get_csrf_token() -> str:
    """Return the current CSRF token, creating one if needed.

    The token is stored in the Flask session so it persists across
    requests for the same user.
    """
    token = session.get(_CSRF_TOKEN_KEY)
    if not token:
        token = _generate_csrf_token()
        session[_CSRF_TOKEN_KEY] = token
    return token


def _is_json_api_request(req: Request) -> bool:
    """Return ``True`` if the request carries a JSON content type.

    Browsers enforce the Same-Origin Policy on ``application/json``
    requests; they cannot be sent cross-origin without a successful
    CORS preflight, making CSRF attacks via JSON impractical.
    """
    content_type = (req.content_type or "").lower()
    return "application/json" in content_type


def _validate_csrf(req: Request) -> Optional[str]:
    """Validate the CSRF token on the request.

    Returns ``None`` on success, or an error message string on failure.
    """
    if req.method not in _STATE_CHANGING_METHODS:
        return None  # Safe methods don't need CSRF

    if req.path in _EXEMPT_PATHS:
        return None

    # JSON API requests are exempt (browser SOP protects them)
    if _is_json_api_request(req):
        return None

    expected = session.get(_CSRF_TOKEN_KEY)
    if not expected:
        return "CSRF token mancante nella sessione"

    # Check form data first, then fall back to a custom header
    submitted = req.form.get(_CSRF_FORM_FIELD) or req.headers.get("X-CSRF-Token")
    if not submitted:
        return "CSRF token non fornito"

    if not secrets.compare_digest(submitted, expected):
        return "CSRF token non valido"

    return None


def init_csrf(app: Flask) -> None:
    """Register the CSRF middleware on *app*.

    * Adds a ``before_request`` hook that validates CSRF tokens.
    * Makes ``csrf_token()`` available as a Jinja global.
    """

    @app.before_request
    def _csrf_protect():
        error = _validate_csrf(request)
        if error:
            abort(403, description=error)

    # Expose csrf_token() in Jinja templates
    app.jinja_env.globals["csrf_token"] = _get_csrf_token
