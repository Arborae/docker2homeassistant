import logging
import os
import subprocess
from pathlib import Path


_LOGGER = logging.getLogger(__name__)


def _run_git_command(args):
    repo_root = Path(__file__).resolve().parent.parent
    try:
        output = subprocess.check_output(
            args,
            stderr=subprocess.DEVNULL,
            cwd=repo_root,
        )
    except Exception as exc:  # pragma: no cover
        _LOGGER.debug("Git command %s failed when computing version: %s", args, exc)
        return None
    value = output.decode().strip()
    return value or None


def _looks_like_sha(value: str) -> bool:
    if len(value) < 7:
        return False
    lower = value.lower()
    return all(char in "0123456789abcdef" for char in lower)


def get_d2ha_version() -> str:
    """Return a human-readable D2HA version string.

    Priority:
    1. D2HA_VERSION env var (Docker images built by CI).
    2. Git tag on current commit -> Stable Release vX.Y.Z (dev installs).
    3. Git short SHA -> Nightly Release #xxxxxxx (dev installs).
    4. Fallback: dev.
    """

    env_version = os.environ.get("D2HA_VERSION")
    if env_version:
        if env_version.startswith("Stable Release "):
            return env_version
        if _looks_like_sha(env_version):
            return f"Nightly Release #{env_version[:7]}"
        return env_version

    tag = _run_git_command(["git", "describe", "--tags", "--exact-match"])
    if tag:
        return f"Stable Release v{tag}"

    short_sha = _run_git_command(["git", "rev-parse", "--short", "HEAD"])
    if short_sha:
        return f"Nightly Release #{short_sha}"

    return "dev"
