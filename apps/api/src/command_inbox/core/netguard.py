"""Outbound URLs a workspace configures (a SIEM endpoint) are checked before use, and again at send time
(against DNS rebinding): HTTPS only, and never loopback, link-local (cloud metadata), private or
reserved addresses unless the installation allowlists the range (a bank's internal SIEM, for example)."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from command_inbox.config import settings
from command_inbox.core.errors import unprocessable


def _allowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if any(ip in ipaddress.ip_network(c, strict=False) for c in settings.outbound_allow_cidrs):
        return True
    return not (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def check_outbound(url: str) -> None:
    """Raise a 422 problem unless `url` is HTTPS to an address this installation may reach."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise unprocessable("outbound_url", "Use an https:// address.")
    host = parts.hostname
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, parts.port or 443, type=socket.SOCK_STREAM
            )
        except OSError:
            raise unprocessable("outbound_url", f"{host} does not resolve.") from None
        addresses = [ipaddress.ip_address(i[4][0]) for i in infos]
    blocked = [str(a) for a in addresses if not _allowed(a)]
    if blocked:
        raise unprocessable(
            "outbound_url",
            f"{host} resolves to an address this installation does not call out to ({blocked[0]}).",
            "Ask the operator to allowlist the range if the endpoint is on your internal network.",
        )
