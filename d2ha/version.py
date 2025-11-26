import logging
import os
import subprocess
from pathlib import Path


_LOGGER = logging.getLogger(__name__)


def get_d2ha_version() -> str:
    """Return the current application version.

    Priority order:
    1. D2HA_VERSION environment variable.
    2. ``git describe --tags --always --dirty`` output.
    3. Fallback to "dev" if detection fails.
    """

    env_version = os.environ.get("D2HA_VERSION")
    if env_version:
        return env_version

    try:
        repo_root = Path(__file__).resolve().parent.parent
        output = subprocess.check_output(
            ["git", "describe", "--tags", "--always", "--dirty"],
            stderr=subprocess.STDOUT,
            cwd=repo_root,
        )
        git_version = output.decode().strip()
        if git_version:
            return git_version
    except Exception as exc:  # pragma: no cover - best effort logging
        _LOGGER.debug("Falling back to dev version: %s", exc)

    return "dev"
