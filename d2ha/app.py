"""D2HA application factory. Gunicorn entry point: ``app:create_app()``."""

import logging
import os
import secrets
from datetime import timedelta
from typing import Optional

from flask import Flask, g, request, url_for
from flask.sessions import SecureCookieSessionInterface
from werkzeug.middleware.proxy_fix import ProxyFix

import i18n
from checker import UpdateChecker
from config import Config
from docker_ops import DockerOps
from jobs import JobRunner
from notify import Notifier, Telegram
from registry import RegistryClient
from releases import ReleaseNotes
from security import LoginLimiter, RateLimiter, csrf_token
from store import Store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
THEMES = ("system", "light", "dark")
log = logging.getLogger("d2ha")


class D2HA:
    """Services shared by the blueprints (``current_app.extensions["d2ha"]``)."""

    def __init__(self, config: Config, ops: DockerOps, registry: RegistryClient,
                 releases: ReleaseNotes, store: Store):
        self.config = config
        self.ops = ops
        self.store = store
        self.notifier = Notifier(Telegram(config.telegram_token, config.telegram_chat_ids), store)
        self.checker = UpdateChecker(ops, registry, releases, store, config.state_path, self.notifier)
        self.jobs = JobRunner()
        self.login_limiter = LoginLimiter()
        self.write_limiter = RateLimiter(limit=30, window=60)
        self.setup_code = "" if store.configured else "-".join(
            secrets.token_hex(2).upper() for _ in range(3)
        )

    def find_container(self, key: str):
        for container in self.ops.list_containers():
            if container["key"] == key:
                return container
        return None


class AutoSecureSessionInterface(SecureCookieSessionInterface):
    """Mark the session cookie Secure whenever the request arrived over HTTPS."""

    def get_cookie_secure(self, app):
        return bool(app.config.get("SESSION_COOKIE_SECURE")) or request.is_secure


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("urllib3", "docker", "werkzeug"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def create_app(config: Optional[Config] = None, ops: Optional[DockerOps] = None,
               registry: Optional[RegistryClient] = None,
               releases: Optional[ReleaseNotes] = None) -> Flask:
    config = config or Config()
    _configure_logging(config.log_level)
    store = Store(config.store_path, config.default_check_hours)
    if store.migrated_from_v03:
        log.info("Imported account and preferences from D2HA 0.3")

    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)
    app.session_interface = AutoSecureSessionInterface()
    app.config.update(
        SECRET_KEY=config.secret_key or store.get("secret_key"),
        SESSION_COOKIE_NAME="d2ha_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    app.json.sort_keys = False

    ctx = D2HA(config, ops or DockerOps(), registry or RegistryClient(),
               releases or ReleaseNotes(config.github_token), store)
    app.extensions["d2ha"] = ctx
    i18n.load(os.path.join(BASE_DIR, "translations"))

    @app.before_request
    def _language_and_theme():
        g.lang = i18n.pick_language()
        theme = request.cookies.get("d2ha_theme", "system")
        g.theme = theme if theme in THEMES else "system"

    @app.after_request
    def _security_headers(response):
        ancestors = " ".join(["'self'", config.frame_ancestors]).strip()
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; manifest-src 'self'; worker-src 'self'; object-src 'none'; "
            f"base-uri 'none'; form-action 'self'; frame-ancestors {ancestors}"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def asset(path: str) -> str:
        return url_for("static", filename=path, v=config.version)

    app.jinja_env.globals.update(
        t=i18n.t,
        csrf_token=csrf_token,
        asset=asset,
        client_texts=i18n.client_texts,
        version=config.version,
        read_only=config.read_only,
    )

    from api import api_bp
    from web import web_bp

    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp)

    if config.start_background:
        ctx.jobs.start()
        ctx.checker.start()
        if ctx.notifier.configured:
            ctx.notifier.start(ctx.ops.events)
        if ctx.setup_code:
            log.warning("No account yet. Open D2HA and use this setup code: %s", ctx.setup_code)
    return app
