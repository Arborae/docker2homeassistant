import json
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "d2ha")


def load(lang):
    with open(os.path.join(ROOT, "translations", f"{lang}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_italian_and_english_have_the_same_keys():
    assert set(load("en")) == set(load("it"))


def test_placeholders_match():
    en, it = load("en"), load("it")
    for key in en:
        assert set(re.findall(r"\{(\w+)\}", en[key])) == set(re.findall(r"\{(\w+)\}", it[key])), key


def test_every_key_used_in_the_code_exists():
    used = set()
    for folder, pattern in (("templates", r"\bt\('([a-z0-9_.]+)'"), ("static", r'\bT\("([a-z0-9_.]+)"')):
        for name in os.listdir(os.path.join(ROOT, folder)):
            path = os.path.join(ROOT, folder, name)
            if os.path.isfile(path) and name.endswith((".html", ".js")):
                used |= set(re.findall(pattern, open(path, encoding="utf-8").read()))
    for name in os.listdir(ROOT):
        if name.endswith(".py"):
            used |= set(re.findall(r'"((?:error|flash)\.[a-z0-9_]+)"', open(os.path.join(ROOT, name), encoding="utf-8").read()))
    missing = sorted(used - set(load("en")))
    assert not missing, missing
