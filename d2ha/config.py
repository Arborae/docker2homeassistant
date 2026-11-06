"""Runtime configuration, read once from environment variables."""

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Config:
    data_dir: str = field(default_factory=lambda: os.environ.get("D2HA_DATA_DIR", "/app/data"))
    version: str = field(default_factory=lambda: os.environ.get("D2HA_VERSION", "dev").strip() or "dev")
    secret_key: str = field(default_factory=lambda: os.environ.get("D2HA_SECRET_KEY", "").strip())
    github_token: str = field(default_factory=lambda: os.environ.get("D2HA_GITHUB_TOKEN", "").strip())
    log_level: str = field(default_factory=lambda: os.environ.get("D2HA_LOG_LEVEL", "INFO").upper())
    # Read-only mode: no update/prune actions, so the Docker API can be proxied without POST.
    read_only: bool = field(default_factory=lambda: _bool("D2HA_READ_ONLY", False))
    # Extra origins allowed to embed D2HA in an iframe (e.g. a Home Assistant dashboard).
    frame_ancestors: str = field(default_factory=lambda: os.environ.get("D2HA_FRAME_ANCESTORS", "").strip())
    default_check_hours: int = field(default_factory=lambda: _int("D2HA_CHECK_INTERVAL_HOURS", 6))
    # Telegram notifications: bot token and one or more chat ids (comma separated).
    telegram_token: str = field(default_factory=lambda: os.environ.get("D2HA_TELEGRAM_TOKEN", "").strip())
    telegram_chat_ids: tuple = field(default_factory=lambda: tuple(
        c.strip() for c in os.environ.get("D2HA_TELEGRAM_CHAT_ID", "").split(",") if c.strip()))
    # Background threads (checker, jobs) are off in tests.
    start_background: bool = True

    @property
    def store_path(self) -> str:
        return os.path.join(self.data_dir, "d2ha.json")

    @property
    def state_path(self) -> str:
        return os.path.join(self.data_dir, "state.json")
