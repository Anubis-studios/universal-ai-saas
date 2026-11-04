"""Real host + platform telemetry for the HUD.

Reads /proc directly (Linux) so the dashboard shows measured numbers rather
than invented ones. Every probe degrades to ``None`` instead of raising, so the
UI can render a dash when a counter is unavailable.
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

_START = time.time()
_LAST_CPU: Optional[tuple[int, int]] = None


def _read(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def cpu_percent() -> Optional[float]:
    """CPU busy percentage since the previous call (0 on the first sample)."""
    global _LAST_CPU
    raw = _read("/proc/stat")
    if not raw:
        return None
    parts = raw.splitlines()[0].split()
    values = [int(v) for v in parts[1:] if v.isdigit()]
    if len(values) < 4:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    if _LAST_CPU is None:
        _LAST_CPU = (total, idle)
        return 0.0
    d_total = total - _LAST_CPU[0]
    d_idle = idle - _LAST_CPU[1]
    _LAST_CPU = (total, idle)
    if d_total <= 0:
        return 0.0
    return round(max(0.0, min(100.0, (d_total - d_idle) * 100.0 / d_total)), 1)


def memory() -> dict[str, Any]:
    raw = _read("/proc/meminfo")
    if not raw:
        return {"total_mb": None, "used_mb": None, "percent": None}
    info: dict[str, int] = {}
    for line in raw.splitlines():
        key, _, rest = line.partition(":")
        num = rest.strip().split(" ")[0]
        if num.isdigit():
            info[key] = int(num)  # kB
    total = info.get("MemTotal")
    avail = info.get("MemAvailable") or info.get("MemFree")
    if not total or avail is None:
        return {"total_mb": None, "used_mb": None, "percent": None}
    used = total - avail
    return {
        "total_mb": round(total / 1024),
        "used_mb": round(used / 1024),
        "percent": round(used * 100.0 / total, 1),
    }


def load_average() -> Optional[list[float]]:
    try:
        return [round(v, 2) for v in os.getloadavg()]
    except (OSError, AttributeError):
        return None


def threads() -> Optional[int]:
    """Threads in this process — the 'thread pool' figure in the HUD."""
    raw = _read("/proc/self/status")
    if not raw:
        return None
    for line in raw.splitlines():
        if line.startswith("Threads:"):
            value = line.split()[1]
            return int(value) if value.isdigit() else None
    return None


def rss_mb() -> Optional[float]:
    raw = _read("/proc/self/status")
    if not raw:
        return None
    for line in raw.splitlines():
        if line.startswith("VmRSS:"):
            value = line.split()[1]
            if value.isdigit():
                return round(int(value) / 1024, 1)
    return None


def snapshot(request) -> dict[str, Any]:
    """Composite telemetry: host counters plus live platform state."""
    state = request.app.state
    db = getattr(state, "db", None)
    skills = getattr(state, "skills", None)
    db_status = db.status() if db else {}
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": {
            "cpu_percent": cpu_percent(),
            "memory": memory(),
            "load": load_average(),
            "threads": threads(),
            "rss_mb": rss_mb(),
            "uptime_s": round(time.time() - _START, 1),
            "pid": os.getpid(),
        },
        "platform": {
            "uptime_s": round(time.time() - _START, 1),
            "records": db_status.get("records", 0),
            "backend": db_status.get("backend"),
            "persistent": db_status.get("persistent"),
            "snapshot": bool(db_status.get("snapshot_exists")),
            "skills": getattr(skills.db, "count", 0) if skills else 0,
            "skill_categories": len(skills.db.categories) if skills else 0,
        },
    }
