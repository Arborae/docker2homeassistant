from typing import Any, Dict

def format_timedelta(delta_seconds: float) -> str:
    """Format seconds into a human-readable time string.
    
    Args:
        delta_seconds: Number of seconds to format.
        
    Returns:
        Human-readable string like "1g 2h 30m" (days, hours, minutes).
    """
    if delta_seconds < 0:
        delta_seconds = 0
    days = int(delta_seconds // 86400)
    hours = int((delta_seconds % 86400) // 3600)
    minutes = int((delta_seconds % 3600) // 60)
    parts = []
    if days:
        parts.append(f"{days}g")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def human_bytes(num: float, suffix: str = "B") -> str:
    """Convert bytes to a human-readable format.
    
    Args:
        num: Number of bytes.
        suffix: Suffix to append (default "B" for bytes).
        
    Returns:
        Human-readable string like "1.5MB" or "2.3GB".
    """
    for unit in ["", "K", "M", "G", "T"]:
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}P{suffix}"


def slugify_container(name: str, short_id: str) -> str:
    """Create a URL-safe slug from container name and ID.
    
    Args:
        name: Container name.
        short_id: Short Docker container ID.
        
    Returns:
        Slugified string like "container_name_abc123".
    """
    base = "".join(c.lower() if c.isalnum() else "_" for c in name).strip("_")
    if not base:
        base = "container"
    return f"{base}_{short_id}"


def build_stable_id(container_info: Dict[str, Any]) -> str:
    """Create a stable ID for Home Assistant based on stack + container name.

    Avoids Docker IDs so the unique_id stays stable when containers are recreated.
    
    Args:
        container_info: Dictionary containing 'stack' and 'name' keys.
        
    Returns:
        Stable identifier string suitable for Home Assistant entity IDs.
    """

    stack = container_info.get("stack") or "no_stack"
    name = container_info.get("name") or "container"

    base = f"{stack}__{name}"

    slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in base)

    while "__" in slug:
        slug = slug.replace("__", "_")
    slug = slug.strip("_")
    return slug

def read_system_uptime_seconds() -> float:
    """Read system uptime from /proc/uptime.
    
    Returns:
        System uptime in seconds, or -1.0 if unavailable (e.g., on Windows).
    """
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as fp:
            content = fp.read().strip().split()
            if content:
                return float(content[0])
    except Exception:
        pass
    return -1.0

