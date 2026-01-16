import time
from functools import wraps

import pyotp
from flask import Blueprint, flash, redirect, render_template, request, session, url_for, current_app
from werkzeug.security import check_password_hash, generate_password_hash

from auth_store import get_auth_config, save_auth_config
from i18n import t, set_current_lang
from theme import set_current_theme

auth_bp = Blueprint("auth", __name__)

# -- Decorators --

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        config = get_auth_config()
        current_user = session.get("user")
        if not current_user or current_user != config.get("username"):
            session.clear()
            return redirect(url_for("auth.login", next=request.url))

        timeout_minutes = int(config.get("session_timeout_minutes", 0) or 0)
        last_activity = session.get("last_activity_ts") or session.get("logged_at")
        now_ts = int(time.time())

        if timeout_minutes > 0 and last_activity:
            if now_ts - int(last_activity) > timeout_minutes * 60:
                session.clear()
                flash(t("flash.session_expired"), "info")
                return redirect(url_for("auth.login", next=request.url))

        session["last_activity_ts"] = now_ts
        return view(*args, **kwargs)

    return wrapped


def onboarding_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        config = get_auth_config()
        current_user = session.get("user")
        if not current_user or current_user != config.get("username"):
            session.clear()
            return redirect(url_for("auth.login", next=request.url))
        if not is_onboarding_done():
            return redirect(url_for("auth.setup_account"))
        return view(*args, **kwargs)

    return wrapped

def is_onboarding_done():
    config = get_auth_config()
    return bool(config.get("onboarding_done"))

# -- Helpers --

def _get_remote_addr():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")

def _build_qr_code_data_uri(data: str) -> str:
    # Importing here to avoid circular dependencies or scope issues if moved
    import base64
    import io
    import qrcode
    try:
        qr = qrcode.QRCode(version=1, box_size=6, border=2)
        qr.add_data(data)
        qr.make(fit=True)
        image = qr.make_image(fill_color="#0f1116", back_color="white")

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception:
        return ""

def _publish_current_state():
    # Helper to access mqtt_manager from current_app
    mqtt_manager = current_app.mqtt_manager
    docker_service = current_app.docker_service
    containers_info = docker_service.collect_containers_info_for_updates()
    mqtt_manager.publish_autodiscovery_and_state(containers_info)

# -- Routes --

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    from collections import defaultdict
    # We might need to handle FAILED_LOGINS better, maybe attach to app context?
    # For now, using a global in module scope might work if worker is single, 
    # but ideally this should be in a service. 
    # Let's import it from current_app if we attach it there, or just keep it local here for now.
    # Actually, app.py had FAILED_LOGINS global. Let's make it module-level here.
    
    config = get_auth_config()
    next_url = request.args.get("next")

    remote_addr = _get_remote_addr()
    if is_login_blocked(remote_addr):
        flash(t("flash.login_rate_limited"), "error")
        return render_template("login.html"), 429

    if session.get("user"):
        if not config.get("onboarding_done"):
            return redirect(url_for("auth.setup_account"))
        return redirect(next_url or url_for("ui.index"))

    two_factor = bool(config.get("two_factor_enabled") and config.get("totp_secret"))

    if request.method == "POST":
        username_input = (request.form.get("username") or "").strip()
        password_input = request.form.get("password") or ""
        token_input = (request.form.get("token") or "").strip()

        if username_input == config.get("username") and check_password_hash(
            config.get("password_hash", ""), password_input
        ):
            totp_valid = True
            if two_factor:
                totp = pyotp.TOTP(config.get("totp_secret"))
                totp_valid = bool(token_input) and bool(
                    totp.verify(token_input, valid_window=1)
                )

            if totp_valid:
                session.clear()
                session["user"] = config.get("username")
                session["logged_at"] = int(time.time())
                session["last_activity_ts"] = session["logged_at"]

                if not config.get("onboarding_done"):
                    return redirect(url_for("auth.setup_account"))

                return redirect(next_url or url_for("ui.index"))

        register_failed_login(remote_addr)
        flash(t("flash.invalid_credentials"), "error")

    return render_template(
        "login.html",
        two_factor=two_factor,
        show_onboarding_hint=not bool(config.get("onboarding_done")),
    )


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/set-language", methods=["POST"])
def set_language():
    lang = (request.form.get("lang") or "").strip()
    set_current_lang(lang)
    next_url = request.form.get("next") or request.referrer or url_for("ui.index")
    return redirect(next_url)


@auth_bp.route("/set-theme", methods=["POST"])
def set_theme():
    theme = (request.form.get("theme") or "").strip()
    set_current_theme(theme)
    next_url = request.form.get("next") or request.referrer or url_for("ui.index")
    return redirect(next_url)


@auth_bp.route("/setup-account", methods=["GET", "POST"])
@login_required
def setup_account():
    config = get_auth_config()
    if config.get("onboarding_done"):
        return redirect(url_for("ui.index"))

    if request.method == "POST":
        new_username = (request.form.get("new_username") or config.get("username", "")).strip()
        new_password = request.form.get("new_password") or ""
        new_password_confirm = request.form.get("new_password_confirm") or ""

        if not new_password:
            flash(t("flash.password_required"), "error")
        elif new_password != new_password_confirm:
            flash(t("flash.passwords_mismatch"), "error")
        elif new_password == "admin":
            flash(t("flash.password_admin_forbidden"), "error")
        elif len(new_password) < 10:
            flash(t("flash.password_length_short"), "error")
        else:
            config["username"] = new_username or config.get("username", "admin")
            config["password_hash"] = generate_password_hash(new_password)
            save_auth_config(config)
            session["user"] = config["username"]
            flash(t("flash.account_updated_onboarding"), "success")
            return redirect(url_for("auth.setup_2fa"))

    return render_template(
        "setup_account.html",
        current_username=config.get("username", "admin"),
    )


@auth_bp.route("/setup-2fa", methods=["GET", "POST"])
@login_required
def setup_2fa():
    config = get_auth_config()

    if config.get("onboarding_done"):
        return redirect(url_for("ui.index"))

    secret = session.get("pending_totp_secret") or pyotp.random_base32()
    session["pending_totp_secret"] = secret
    provisioning_uri = pyotp.TOTP(secret).provisioning_uri(
        name=config.get("username", "admin"), issuer_name="D2HA"
    )
    qr_code_data_uri = _build_qr_code_data_uri(provisioning_uri)

    if request.method == "POST":
        choice = request.form.get("choice")
        if choice == "skip":
            config["two_factor_enabled"] = False
            config["totp_secret"] = None
            save_auth_config(config)
            session.pop("pending_totp_secret", None)
            flash(t("flash.setup2fa_skipped"), "info")
            return redirect(url_for("auth.setup_modes"))

        if choice == "enable":
            token = (request.form.get("token") or "").strip()
            totp = pyotp.TOTP(secret)
            if totp.verify(token, valid_window=1):
                config["two_factor_enabled"] = True
                config["totp_secret"] = secret
                save_auth_config(config)
                session.pop("pending_totp_secret", None)
                flash(t("flash.setup2fa_enabled"), "success")
                return redirect(url_for("auth.setup_modes"))

            flash(t("flash.setup2fa_invalid_code"), "error")

    return render_template(
        "setup_2fa.html",
        secret=secret,
        provisioning_uri=provisioning_uri,
        qr_code_data_uri=qr_code_data_uri,
        username=config.get("username", "admin"),
    )


@auth_bp.route("/setup-modes", methods=["GET", "POST"])
@login_required
def setup_modes():
    config = get_auth_config()

    if config.get("onboarding_done"):
        return redirect(url_for("ui.index"))

    safe_mode_enabled = bool(config.get("safe_mode_enabled", True))
    performance_mode_enabled = bool(config.get("performance_mode_enabled", False))
    debug_mode_enabled = bool(config.get("debug_mode_enabled", False))

    if request.method == "POST":
        safe_choice = request.form.get("safe_mode_enabled")
        perf_choice = request.form.get("performance_mode_enabled")
        debug_choice = request.form.get("debug_mode_enabled")

        safe_mode_enabled = bool(safe_choice)
        performance_mode_enabled = bool(perf_choice)
        debug_mode_enabled = bool(debug_choice)

        config["safe_mode_enabled"] = safe_mode_enabled
        config["performance_mode_enabled"] = performance_mode_enabled
        config["debug_mode_enabled"] = debug_mode_enabled
        save_auth_config(config)

        # We probably should trigger logging re-config here if debug changed, 
        # but simpler to just reload or let next request handle it (if stored in config).
        # In app.py we call configure_logging based on this.

        return redirect(url_for("auth.setup_autodiscovery"))

    return render_template(
        "setup_modes.html",
        safe_mode_enabled=safe_mode_enabled,
        performance_mode_enabled=performance_mode_enabled,
        debug_mode_enabled=debug_mode_enabled,
    )

def apply_autodiscovery_default_choice(enable_all: bool) -> None:
    docker_service = current_app.docker_service
    mqtt_manager = current_app.mqtt_manager
    autodiscovery_preferences = current_app.autodiscovery_preferences
    
    containers_info = docker_service.collect_containers_info_for_updates()
    containers_info = [c for c in containers_info if not mqtt_manager.is_self_container(c)]
    stable_ids = []
    from services.preferences import AutodiscoveryPreferences
    actions_pref = {
        action: enable_all for action in AutodiscoveryPreferences.AVAILABLE_ACTIONS
    }
    for container in containers_info:
        stable_id = container.get("stable_id")
        if not stable_id:
            continue
        autodiscovery_preferences.set_preferences(
            stable_id, enable_all, actions_pref
        )
        stable_ids.append(stable_id)

    if stable_ids:
        autodiscovery_preferences.prune(stable_ids)
    _publish_current_state()


@auth_bp.route("/setup-autodiscovery", methods=["GET", "POST"])
@login_required
def setup_autodiscovery():
    config = get_auth_config()

    if config.get("onboarding_done"):
        return redirect(url_for("ui.index"))

    mqtt_default_entities_enabled = bool(
        config.get("mqtt_default_entities_enabled", True)
    )

    if request.method == "POST":
        choice = request.form.get("autodiscovery_choice")
        if choice not in ("enable_all", "disable_all"):
            flash(t("flash.autodiscovery_invalid_choice"), "error")
        else:
            enable_all = choice == "enable_all"
            config["mqtt_default_entities_enabled"] = enable_all
            save_auth_config(config)

            autodiscovery_applied = True
            try:
                apply_autodiscovery_default_choice(enable_all)
            except Exception:
                autodiscovery_applied = False
                current_app.logger.exception(
                    "Failed to apply autodiscovery defaults during onboarding"
                )
                flash(t("flash.autodiscovery_apply_failed"), "error")

            config["onboarding_done"] = True
            save_auth_config(config)

            if autodiscovery_applied:
                flash(t("flash.autodiscovery_complete"), "success")
            else:
                flash(t("flash.autodiscovery_partial"), "info")
            return redirect(url_for("ui.index"))

    return render_template(
        "setup_autodiscovery.html",
        mqtt_default_entities_enabled=mqtt_default_entities_enabled,
    )


@auth_bp.route("/settings/security", methods=["GET", "POST"])
@login_required
def security_settings():
    config = get_auth_config()

    provisioning_uri = None
    qr_code_data_uri = None
    pending_2fa_setup = False

    if not is_onboarding_done():
        return redirect(url_for("auth.setup_account"))

    def _require_current_password(value: str) -> bool:
        if not check_password_hash(config.get("password_hash", ""), value):
            flash(t("flash.security_current_password_incorrect"), "error")
            return False
        return True

    def _require_totp(value: str) -> bool:
        if not value:
            flash(t("flash.security_current_totp_missing"), "error")
            return False
        if not config.get("totp_secret"):
            flash(t("flash.security_totp_config_invalid"), "error")
            return False
        totp = pyotp.TOTP(config.get("totp_secret"))
        if not totp.verify(value, valid_window=1):
            flash(t("flash.security_totp_invalid"), "error")
            return False
        return True

    if request.method == "POST":
        action = request.form.get("action")
        current_password = request.form.get("current_password") or ""
        current_totp_code = (request.form.get("current_totp_code") or "").strip()

        if action == "change_credentials":
            if not _require_current_password(current_password):
                pass
            elif bool(config.get("two_factor_enabled")) and not _require_totp(
                current_totp_code
            ):
                pass
            else:
                new_username = (request.form.get("new_username") or "").strip()
                new_password = request.form.get("new_password") or ""
                new_password_confirm = request.form.get("new_password_confirm") or ""

                username_change = new_username if new_username else None
                password_hash = None
                has_errors = False
                changes_made = False

                if username_change:
                    changes_made = True

                if new_password:
                    if new_password != new_password_confirm:
                        flash(t("flash.security_new_passwords_mismatch"), "error")
                        has_errors = True
                    elif new_password == "admin":
                        flash(t("flash.security_new_password_admin_forbidden"), "error")
                        has_errors = True
                    elif len(new_password) < 10:
                        flash(t("flash.security_new_password_short"), "error")
                        has_errors = True
                    else:
                        password_hash = generate_password_hash(new_password)
                        changes_made = True
                        flash(t("flash.security_password_updated"), "success")

                if not changes_made:
                    flash(t("flash.security_no_changes"), "info")
                elif has_errors:
                    flash(t("flash.security_fix_errors"), "error")
                else:
                    if username_change:
                        config["username"] = username_change
                        session["user"] = username_change
                    if password_hash:
                        config["password_hash"] = password_hash
                    save_auth_config(config)
                    flash(t("flash.security_credentials_updated"), "success")

        elif action == "update_session_timeout":
            if not _require_current_password(current_password):
                pass
            elif bool(config.get("two_factor_enabled")) and not _require_totp(
                current_totp_code
            ):
                pass
            else:
                session_timeout_raw = (request.form.get("session_timeout_minutes") or "").strip()
                session_timeout_minutes = None

                if not session_timeout_raw:
                    flash(t("flash.security_timeout_invalid"), "error")
                else:
                    try:
                        parsed_timeout = int(session_timeout_raw)
                        if parsed_timeout < 1 or parsed_timeout > 1440:
                            flash(t("flash.security_timeout_invalid"), "error")
                        else:
                            session_timeout_minutes = parsed_timeout
                    except ValueError:
                        flash(t("flash.security_timeout_invalid"), "error")

                if session_timeout_minutes is not None:
                    if session_timeout_minutes == config.get("session_timeout_minutes"):
                        flash(t("flash.security_no_changes"), "info")
                    else:
                        config["session_timeout_minutes"] = session_timeout_minutes
                        save_auth_config(config)
                        flash(t("flash.security_session_timeout_updated"), "success")
        
        elif action == "enable_2fa":
            if config.get("two_factor_enabled"):
                flash(t("flash.security_2fa_already_enabled"), "info")
            elif not _require_current_password(current_password):
                pass
            else:
                secret = config.get("totp_secret") or pyotp.random_base32()
                config["totp_secret"] = secret
                save_auth_config(config)
                provisioning_uri = pyotp.TOTP(secret).provisioning_uri(
                    name=config.get("username", "admin"), issuer_name="D2HA"
                )
                qr_code_data_uri = _build_qr_code_data_uri(provisioning_uri)
                pending_2fa_setup = True
                flash(t("flash.security_scan_qr_to_enable"), "info")

        elif action == "confirm_enable_2fa":
            if config.get("two_factor_enabled"):
                flash(t("flash.security_2fa_already_enabled"), "info")
            elif not config.get("totp_secret"):
                flash(t("flash.security_totp_secret_missing"), "error")
            else:
                verify_totp_code = (request.form.get("verify_totp_code") or "").strip()
                totp = pyotp.TOTP(config.get("totp_secret"))
                if not totp.verify(verify_totp_code, valid_window=1):
                    flash(t("flash.security_2fa_enabled_invalid_code"), "error")
                    provisioning_uri = totp.provisioning_uri(
                        name=config.get("username", "admin"), issuer_name="D2HA"
                    )
                    qr_code_data_uri = _build_qr_code_data_uri(provisioning_uri)
                    pending_2fa_setup = True
                else:
                    config["two_factor_enabled"] = True
                    save_auth_config(config)
                    flash(t("flash.security_2fa_enabled"), "success")

        elif action == "disable_2fa":
            if not config.get("two_factor_enabled"):
                flash(t("flash.security_2fa_already_disabled"), "info")
            elif not _require_current_password(current_password):
                pass
            elif not _require_totp(current_totp_code):
                pass
            else:
                config["two_factor_enabled"] = False
                config["totp_secret"] = None
                save_auth_config(config)
                flash(t("flash.security_2fa_disabled_warning"), "warning")

        config = get_auth_config()

    if not provisioning_uri and config.get("totp_secret") and not config.get(
        "two_factor_enabled"
    ):
        pending_2fa_setup = True
        provisioning_uri = pyotp.TOTP(config.get("totp_secret")).provisioning_uri(
            name=config.get("username", "admin"), issuer_name="D2HA"
        )
        qr_code_data_uri = _build_qr_code_data_uri(provisioning_uri)

    return render_template(
        "security_settings.html",
        two_factor_enabled=bool(
            config.get("two_factor_enabled") and config.get("totp_secret")
        ),
        has_totp_secret=bool(config.get("totp_secret")),
        pending_2fa_setup=pending_2fa_setup,
        provisioning_uri=provisioning_uri,
        qr_code_data_uri=qr_code_data_uri,
        active_page="security",
        current_username=config.get("username", "admin"),
        totp_secret=config.get("totp_secret"),
        session_timeout_minutes=int(config.get("session_timeout_minutes", 30) or 30),
    )


# -- Rate Limiting Logics --
from collections import defaultdict
FAILED_LOGINS = defaultdict(list)
MAX_FAILED = 5
BLOCK_WINDOW = 15 * 60
CLEANUP_WINDOW = 60 * 60

def is_login_blocked(remote_addr: str) -> bool:
    now = time.time()
    attempts = FAILED_LOGINS[remote_addr]

    # cleanup old attempts
    FAILED_LOGINS[remote_addr] = [t for t in attempts if now - t < CLEANUP_WINDOW]
    attempts = FAILED_LOGINS[remote_addr]

    recent_attempts = [t for t in attempts if now - t < BLOCK_WINDOW]
    return len(recent_attempts) >= MAX_FAILED

def register_failed_login(remote_addr: str) -> None:
    FAILED_LOGINS[remote_addr].append(time.time())
