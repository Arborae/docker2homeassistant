from flask import session

SUPPORTED_LANGS = ["it", "en"]
DEFAULT_LANG = "it"

TRANSLATIONS = {
    "it": {
        "nav.containers": "Container",
        "nav.images": "Immagini",
        "nav.updates": "Aggiornamenti",
        "nav.autodiscovery": "Autodiscovery",
        "nav.settings": "Impostazioni",
        "nav.security": "Sicurezza",
        "nav.logout": "Esci",
        "nav.events": "Eventi",
        "nav.home": "Dashboard",
        "login.title": "D2HA – Accesso",
        "login.button": "Accedi",
        "login.heading": "Benvenuto in D2HA",
        "login.username": "Nome utente",
        "login.password": "Password",
        "login.token": "Codice 2FA",
        "login.reminder": "Completa la configurazione iniziale se richiesto.",
        "wizard.step1.title": "Configurazione account amministratore",
        "wizard.step2.title": "Autenticazione a due fattori (2FA)",
        "wizard.step3.title": "Modalità del sistema",
        "wizard.step4.title": "Autodiscovery MQTT e Home Assistant",
        "setup_account.title": "Aggiorna le credenziali di accesso",
        "setup_account.submit": "Continua",
        "setup_2fa.enable": "Abilita 2FA",
        "setup_2fa.skip": "Salta per ora",
        "setup_modes.title": "Modalità del sistema",
        "setup_modes.safe": "Modalità sicura",
        "setup_modes.performance": "Modalità prestazioni",
        "setup_autodiscovery.title": "Autodiscovery MQTT e Home Assistant",
        "setup_autodiscovery.enable_all": "Abilita tutte le entità",
        "setup_autodiscovery.disable_all": "Disattiva autodiscovery",
        "safe_mode.label": "Modalità sicura",
        "performance_mode.label": "Modalità prestazioni",
        "theme.label": "Tema",
        "theme.dark": "Scuro",
        "theme.light": "Chiaro",
        "language.label": "Lingua",
        "language.italian": "Italiano",
        "language.english": "English",
        "settings.title": "Impostazioni",
        "settings.security_hint": "Gestisci autenticazione e modalità sicura.",
        "settings.performance_hint": "Riduci carico e polling automatico.",
        "settings.safe_hint": "Richiedi conferma o blocca le modifiche critiche.",
        "settings.backend_status": "Stato backend",
        "settings.notifications": "Notifiche",
        "settings.close": "Chiudi",
        "settings.refresh": "Aggiorna ora",
        "settings.os": "OS installato",
        "settings.docker": "Versione Docker",
        "settings.uptime": "Uptime",
        "theme.switch_label": "Tema",
        "theme.dark.label": "Scuro",
        "theme.light.label": "Chiaro",
        "footer.logout": "Esci",
    },
    "en": {
        "nav.containers": "Containers",
        "nav.images": "Images",
        "nav.updates": "Updates",
        "nav.autodiscovery": "Autodiscovery",
        "nav.settings": "Settings",
        "nav.security": "Security",
        "nav.logout": "Logout",
        "nav.events": "Events",
        "nav.home": "Dashboard",
        "login.title": "D2HA – Login",
        "login.button": "Sign in",
        "login.heading": "Welcome to D2HA",
        "login.username": "Username",
        "login.password": "Password",
        "login.token": "2FA code",
        "login.reminder": "Complete the initial setup if prompted.",
        "wizard.step1.title": "Admin account setup",
        "wizard.step2.title": "Two-factor authentication (2FA)",
        "wizard.step3.title": "System modes",
        "wizard.step4.title": "MQTT Autodiscovery & Home Assistant",
        "setup_account.title": "Update sign-in credentials",
        "setup_account.submit": "Continue",
        "setup_2fa.enable": "Enable 2FA",
        "setup_2fa.skip": "Skip for now",
        "setup_modes.title": "System modes",
        "setup_modes.safe": "Safe mode",
        "setup_modes.performance": "Performance mode",
        "setup_autodiscovery.title": "MQTT Autodiscovery & Home Assistant",
        "setup_autodiscovery.enable_all": "Enable all entities",
        "setup_autodiscovery.disable_all": "Disable autodiscovery",
        "safe_mode.label": "Safe mode",
        "performance_mode.label": "Performance mode",
        "theme.label": "Theme",
        "theme.dark": "Dark",
        "theme.light": "Light",
        "language.label": "Language",
        "language.italian": "Italiano",
        "language.english": "English",
        "settings.title": "Settings",
        "settings.security_hint": "Manage authentication and safe mode.",
        "settings.performance_hint": "Reduce load and automatic polling.",
        "settings.safe_hint": "Require confirmation or block critical changes.",
        "settings.backend_status": "Backend status",
        "settings.notifications": "Notifications",
        "settings.close": "Close",
        "settings.refresh": "Refresh now",
        "settings.os": "Installed OS",
        "settings.docker": "Docker version",
        "settings.uptime": "Uptime",
        "theme.switch_label": "Theme",
        "theme.dark.label": "Dark",
        "theme.light.label": "Light",
        "footer.logout": "Logout",
    },
}


def get_current_lang() -> str:
    lang = session.get("lang") or DEFAULT_LANG
    if lang not in SUPPORTED_LANGS:
        lang = DEFAULT_LANG
    return lang


def set_current_lang(lang: str) -> None:
    if lang in SUPPORTED_LANGS:
        session["lang"] = lang


def t(key: str) -> str:
    lang = get_current_lang()
    return TRANSLATIONS.get(lang, {}).get(
        key,
        TRANSLATIONS.get(DEFAULT_LANG, {}).get(key, key),
    )
