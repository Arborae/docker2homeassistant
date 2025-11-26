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

    env_version = os.environ.get("D2HA_VERSION", "").strip()

    # 1) Prova a usare D2HA_VERSION se ha un formato sensato
    if env_version:
        # Caso 1: Stable Release già pronta
        if env_version.startswith("Stable Release "):
            return env_version

        # Caso 2: nightly formattata tipo "Nightly Release #<sha>"
        if env_version.lower().startswith("nightly release #"):
            after_hash = env_version.split("#", 1)[1].strip() if "#" in env_version else ""
            if _looks_like_sha(after_hash):
                return f"Nightly Release #{after_hash[:7]}"
            # se non ci sono cifre valide, consideriamo D2HA_VERSION non valida
            env_version = ""

        # Caso 3: raw SHA passato dal workflow (es. github.sha)
        if env_version and _looks_like_sha(env_version):
            return f"Nightly Release #{env_version[:7]}"

        # Caso 4: altri valori personalizzati diversi da "dev"
        # (es. "Custom Build xyz") -> usali così come sono
        if env_version.lower() != "dev":
            return env_version

        # Se è "dev" o qualcosa di vuoto/rotto, passiamo alla logica git

    # 2) Nessuna D2HA_VERSION valida -> controlla se il commit corrente è taggato
    tag = _run_git_command(["git", "describe", "--tags", "--exact-match"])
    if tag:
        # Avoid duplicating the leading "v" if the tag already includes it
        prefix = "" if tag.lower().startswith("v") else "v"
        return f"Stable Release {prefix}{tag}"

    # 3) Altrimenti usa la short SHA come nightly
    short_sha = _run_git_command(["git", "rev-parse", "--short", "HEAD"])
    if short_sha:
        return f"Nightly Release #{short_sha}"

    # 4) Fallback finale
    return "dev"
