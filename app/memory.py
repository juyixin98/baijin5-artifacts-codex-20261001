"""Peak-memory probing.

Peak RSS (VmHWM) is read from /proc on Linux with a getrusage fallback.
VmHWM is process-wide and monotonic, so for *comparative* measurements
(tiled vs direct) each mode should run in its own subprocess — see
``python -m app.memprobe`` and ``tests/test_memory.py``.
"""
from __future__ import annotations

import resource


def peak_rss_bytes() -> int:
    """Peak resident set size of the current process, in bytes."""
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024  # kB -> bytes
    except OSError:
        pass
    # Fallback: ru_maxrss is kB on Linux, bytes on macOS.
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys_platform_is_darwin():
        return int(rss)
    return int(rss) * 1024


def sys_platform_is_darwin() -> bool:
    import sys

    return sys.platform == "darwin"
