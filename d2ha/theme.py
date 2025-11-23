from flask import session

SUPPORTED_THEMES = ["dark", "light"]
DEFAULT_THEME = "dark"


def get_current_theme() -> str:
    theme = session.get("theme") or DEFAULT_THEME
    if theme not in SUPPORTED_THEMES:
        theme = DEFAULT_THEME
    return theme


def set_current_theme(theme: str) -> None:
    if theme in SUPPORTED_THEMES:
        session["theme"] = theme
