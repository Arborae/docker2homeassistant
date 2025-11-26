import logging
import os
import subprocess
from pathlib import Path


_LOGGER = logging.getLogger(__name__)


def get_d2ha_version() -> str:
    """Return the current application version.

    Priority order:
    1. D2HA_VERSION environment variable.
    2. Detect a tagged release via git for Stable builds.
    3. Detect a commit SHA via git for Nightly builds.
    4. Fallback to "dev" if detection fails.
    """

    env_version = os.environ.get("D2HA_VERSION")
    if env_version:
        return env_version

    repo_root = Path(__file__).resolve().parent.parent

    try:
        tag = (
            subprocess.check_output(
                ["git", "describe", "--tags", "--exact-match"],
                stderr=subprocess.DEVNULL,
                cwd=repo_root,
            )
            .decode()
            .strip()
        )
        if tag:
            return f"Stable Release {tag}"
    except Exception as exc:  # pragma: no cover - best effort logging
        _LOGGER.debug("Unable to detect tag for version: %s", exc)

    try:
        short_sha = (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL,
                cwd=repo_root,
            )
            .decode()
            .strip()
        )
        if short_sha:
            return f"Nightly Release #{short_sha}"
    except Exception as exc:  # pragma: no cover - best effort logging
        _LOGGER.debug("Unable to detect commit for version: %s", exc)

    return "dev"
