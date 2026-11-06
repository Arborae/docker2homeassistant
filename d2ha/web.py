"""HTML pages: first-run setup, login, containers and settings."""

import hmac
import re

from flask import (Blueprint, current_app, flash, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)

from security import (client_ip, hash_password, login_session, logout_session,
                      new_api_token, new_totp_secret, page_login_required, password_problem,
                      qr_data_uri, safe_next, session_user, totp_uri, verify_password, verify_totp)

web_bp = Blueprint("web", __name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
CHECK_INTERVALS = (1, 3, 6, 12, 24)
SESSION_TIMEOUTS = (15, 30, 60, 240, 1440)


def ctx():
    return current_app.extensions["d2ha"]


# -- public --------------------------------------------------------------------------

@web_bp.route("/healthz")
def healthz():
    return jsonify(status="ok")


@web_bp.route("/sw.js")
def service_worker():
    response = send_from_directory(current_app.static_folder, "sw.js", mimetype="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@web_bp.route("/setup", methods=["GET", "POST"])
def setup():
    if ctx().store.configured:
        return redirect(url_for("web.login"))
    error = ""
    if request.method == "POST":
        ip = client_ip()
        code = request.form.get("code", "").strip().upper()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if ctx().login_limiter.blocked(ip):
            error = "error.too_many_attempts"
        elif not ctx().setup_code or not hmac.compare_digest(code, ctx().setup_code):
            ctx().login_limiter.failed(ip)
            error = "error.setup_code"
        elif not USERNAME_RE.match(username):
            error = "error.username"
        else:
            error = password_problem(password, request.form.get("confirm", "")) or ""
        if not error:
            def mutate(data):
                data["auth"].update(username=username, password_hash=hash_password(password),
                                    totp_secret="", totp_enabled=False)
                data["auth"]["generation"] += 1

            ctx().store.update(mutate)
            ctx().setup_code = ""
            login_session(username, ctx().store.get("auth", "generation"))
            flash("flash.welcome", "success")
            return redirect(url_for("web.settings"))
    return render_template("setup.html", error=error), (400 if error else 200)


@web_bp.route("/login", methods=["GET", "POST"])
def login():
    store = ctx().store
    if not store.configured:
        return redirect(url_for("web.setup"))
    target = safe_next(request.values.get("next"), url_for("web.containers"))
    if request.method == "GET" and session_user():
        return redirect(target)
    auth = store.get("auth")
    error = ""
    if request.method == "POST":
        ip = client_ip()
        if ctx().login_limiter.blocked(ip):
            error = "error.too_many_attempts"
        else:
            username_ok = hmac.compare_digest(request.form.get("username", "").strip(), auth["username"])
            password_ok = verify_password(auth["password_hash"], request.form.get("password", ""))
            code_ok = not auth["totp_enabled"] or verify_totp(auth["totp_secret"], request.form.get("code", ""))
            if username_ok and password_ok and code_ok:
                ctx().login_limiter.succeeded(ip)
                login_session(auth["username"], auth["generation"])
                return redirect(target)
            ctx().login_limiter.failed(ip)
            error = "error.login"
    return render_template("login.html", error=error, totp=auth["totp_enabled"], next=target), (
        401 if error else 200
    )


@web_bp.route("/logout", methods=["POST"])
@page_login_required
def logout():
    logout_session()
    return redirect(url_for("web.login"))


# -- app pages --------------------------------------------------------------------------

@web_bp.route("/")
@page_login_required
def containers():
    return render_template("containers.html", page="containers")


@web_bp.route("/settings", methods=["GET"])
@page_login_required
def settings(new_token: str = ""):
    store = ctx().store
    auth = store.get("auth")
    pending = session.get("totp_pending", "")
    containers = [c for c in ctx().ops.list_containers() if not c["is_self"]]
    return render_template(
        "settings.html",
        page="settings",
        username=auth["username"],
        totp_enabled=auth["totp_enabled"],
        totp_pending=pending,
        totp_qr=qr_data_uri(totp_uri(pending, auth["username"])) if pending else "",
        has_token=bool(store.get("api_token_hash")),
        new_token=new_token,
        check_hours=store.get("settings", "check_interval_hours"),
        session_minutes=store.get("settings", "session_timeout_minutes"),
        check_intervals=CHECK_INTERVALS,
        session_timeouts=SESSION_TIMEOUTS,
        containers=containers,
        in_ha={c["key"]: store.container_in_ha(c["key"]) for c in containers},
        docker_version=ctx().ops.engine_info().get("version", ""),
    )


def _bump_generation_and_relogin(mutate) -> None:
    def wrapped(data):
        mutate(data)
        data["auth"]["generation"] += 1

    ctx().store.update(wrapped)
    auth = ctx().store.get("auth")
    login_session(auth["username"], auth["generation"])


@web_bp.route("/settings/password", methods=["POST"])
@page_login_required
def change_password():
    auth = ctx().store.get("auth")
    new = request.form.get("password", "")
    if not verify_password(auth["password_hash"], request.form.get("current", "")):
        flash("error.current_password", "error")
    elif problem := password_problem(new, request.form.get("confirm", "")):
        flash(problem, "error")
    else:
        _bump_generation_and_relogin(lambda d: d["auth"].update(password_hash=hash_password(new)))
        flash("flash.password_changed", "success")
    return redirect(url_for("web.settings") + "#account")


@web_bp.route("/settings/2fa/start", methods=["POST"])
@page_login_required
def totp_start():
    session["totp_pending"] = new_totp_secret()
    return redirect(url_for("web.settings") + "#account")


@web_bp.route("/settings/2fa/cancel", methods=["POST"])
@page_login_required
def totp_cancel():
    session.pop("totp_pending", None)
    return redirect(url_for("web.settings") + "#account")


@web_bp.route("/settings/2fa/confirm", methods=["POST"])
@page_login_required
def totp_confirm():
    secret = session.get("totp_pending", "")
    if not secret or not verify_totp(secret, request.form.get("code", "")):
        flash("error.totp_code", "error")
        return redirect(url_for("web.settings") + "#account")
    _bump_generation_and_relogin(lambda d: d["auth"].update(totp_secret=secret, totp_enabled=True))
    flash("flash.totp_enabled", "success")
    return redirect(url_for("web.settings") + "#account")


@web_bp.route("/settings/2fa/disable", methods=["POST"])
@page_login_required
def totp_disable():
    auth = ctx().store.get("auth")
    if not verify_password(auth["password_hash"], request.form.get("current", "")) or not verify_totp(
        auth["totp_secret"], request.form.get("code", "")
    ):
        flash("error.totp_disable", "error")
    else:
        _bump_generation_and_relogin(lambda d: d["auth"].update(totp_secret="", totp_enabled=False))
        flash("flash.totp_disabled", "success")
    return redirect(url_for("web.settings") + "#account")


@web_bp.route("/settings/token", methods=["POST"])
@page_login_required
def create_token():
    token, token_hash = new_api_token()
    ctx().store.update(lambda d: d.update(api_token_hash=token_hash))
    # Rendered directly (no redirect): the token is shown once and never stored in clear.
    return settings(new_token=token)


@web_bp.route("/settings/token/revoke", methods=["POST"])
@page_login_required
def revoke_token():
    ctx().store.update(lambda d: d.update(api_token_hash=""))
    flash("flash.token_revoked", "success")
    return redirect(url_for("web.settings") + "#homeassistant")


@web_bp.route("/settings/general", methods=["POST"])
@page_login_required
def general_settings():
    try:
        hours = int(request.form.get("check_hours", 6))
        minutes = int(request.form.get("session_minutes", 30))
    except ValueError:
        hours, minutes = 6, 30
    if hours not in CHECK_INTERVALS or minutes not in SESSION_TIMEOUTS:
        flash("error.generic", "error")
    else:
        ctx().store.update(lambda d: d["settings"].update(check_interval_hours=hours,
                                                          session_timeout_minutes=minutes))
        flash("flash.saved", "success")
    return redirect(url_for("web.settings") + "#updates")
