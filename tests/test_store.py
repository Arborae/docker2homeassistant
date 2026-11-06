import json
import os
import stat

from store import Store


def test_new_store_has_defaults_and_private_file(tmp_path):
    store = Store(str(tmp_path / "d2ha.json"), default_check_hours=12)
    assert not store.configured
    assert store.get("settings", "check_interval_hours") == 12
    assert len(store.get("secret_key")) == 64
    mode = stat.S_IMODE(os.stat(tmp_path / "d2ha.json").st_mode)
    assert mode == 0o600


def test_imports_d2ha_03_account_and_preferences(tmp_path):
    (tmp_path / "auth_config.json").write_text(json.dumps({
        "username": "alice", "password_hash": "scrypt:32768:8:1$abc$def", "onboarding_done": True,
        "two_factor_enabled": True, "totp_secret": "JBSWY3DPEHPK3PXP", "session_timeout_minutes": 60,
    }))
    (tmp_path / "autodiscovery_preferences.json").write_text(json.dumps({
        "containers": {"site_web": {"state": True}, "site_db": {"state": False}}, "global": {},
    }))
    store = Store(str(tmp_path / "d2ha.json"))
    assert store.migrated_from_v03
    auth = store.get("auth")
    assert auth["username"] == "alice"
    assert auth["password_hash"] == "scrypt:32768:8:1$abc$def"
    assert auth["totp_enabled"] and auth["totp_secret"] == "JBSWY3DPEHPK3PXP"
    assert store.get("settings", "session_timeout_minutes") == 60
    assert store.container_in_ha("site_web") and not store.container_in_ha("site_db")
    assert store.container_in_ha("never_seen")  # new containers are visible by default


def test_unfinished_03_onboarding_is_not_imported(tmp_path):
    (tmp_path / "auth_config.json").write_text(json.dumps({"username": "admin", "password_hash": "x", "onboarding_done": False}))
    assert not Store(str(tmp_path / "d2ha.json")).configured


def test_changes_survive_a_restart(tmp_path):
    path = str(tmp_path / "d2ha.json")
    Store(path).set_container_in_ha("site_web", False)
    assert not Store(path).container_in_ha("site_web")
