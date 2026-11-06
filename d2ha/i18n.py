"""Italian/English translations loaded from translations/*.json."""

import json
import os
from typing import Dict

from flask import g, has_request_context, request

LANGUAGES = ("en", "it")
DEFAULT_LANGUAGE = "en"
_TEXTS: Dict[str, Dict[str, str]] = {}


def load(directory: str) -> None:
    for lang in LANGUAGES:
        with open(os.path.join(directory, f"{lang}.json"), encoding="utf-8") as fh:
            _TEXTS[lang] = json.load(fh)


def pick_language() -> str:
    chosen = request.cookies.get("d2ha_lang", "")
    if chosen in LANGUAGES:
        return chosen
    return request.accept_languages.best_match(LANGUAGES) or DEFAULT_LANGUAGE


def current() -> str:
    if has_request_context() and getattr(g, "lang", None):
        return g.lang
    return DEFAULT_LANGUAGE


def translate(lang: str, key: str, **params) -> str:
    """A text in a given language (for messages sent outside a request)."""
    text = _TEXTS.get(lang, {}).get(key) or _TEXTS.get(DEFAULT_LANGUAGE, {}).get(key) or key
    return text.format(**params) if params else text


def t(key: str, **params) -> str:
    return translate(current(), key, **params)


CLIENT_PREFIXES = ("js.", "status.", "phase.", "error.", "api.")


def client_texts() -> Dict[str, str]:
    """The strings the browser script needs."""
    lang = current()
    base = {k: v for k, v in _TEXTS.get(DEFAULT_LANGUAGE, {}).items() if k.startswith(CLIENT_PREFIXES)}
    base.update({k: v for k, v in _TEXTS.get(lang, {}).items() if k.startswith(CLIENT_PREFIXES)})
    return base


def all_keys(lang: str) -> set:
    return set(_TEXTS.get(lang, {}))
