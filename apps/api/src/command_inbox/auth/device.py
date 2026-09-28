"""Human labels for a session's device and network, shown under Settings → Where you are signed in."""

from __future__ import annotations

import re


def device_label(ua: str) -> str:
    browser = (
        "Edge"
        if "Edg/" in ua
        else "Chrome"
        if "Chrome/" in ua
        else "Firefox"
        if "Firefox/" in ua
        else "Safari"
        if "Safari/" in ua
        else "Browser"
    )
    os_name = (
        "iPhone"
        if re.search(r"iPhone|iPad", ua)
        else "Android"
        if "Android" in ua
        else "Windows"
        if "Windows" in ua
        else "macOS"
        if "Mac OS X" in ua
        else "Linux"
        if "Linux" in ua
        else "Unknown OS"
    )
    return f"{browser} · {os_name}"


def location_label(ip: str | None) -> str:
    if not ip or ip == "::1" or ip.startswith(("127.", "10.", "192.168.", "::ffff:127.", "172.")):
        return "Local network"
    return "Unknown location"
