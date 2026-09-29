"""Antivirus: ClamAV over the clamd INSTREAM protocol (a sidecar or a shared scanner).

Without CLAMAV_HOST (development only; production refuses to start without it) files are recorded as
`not_scanned` and processed anyway, and the UI says so.
"""

from __future__ import annotations

import asyncio
import struct

from command_inbox.config import settings

CHUNK = 64 * 1024


async def scan(data: bytes) -> tuple[str, str]:
    """Returns (status, detail): clean | infected | error | not_scanned."""
    if not settings.clamav_host:
        return "not_scanned", "No virus scanner is configured for this installation."
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(settings.clamav_host, settings.clamav_port), timeout=10
        )
        writer.write(b"zINSTREAM\0")
        for i in range(0, len(data), CHUNK):
            part = data[i : i + CHUNK]
            writer.write(struct.pack("!L", len(part)) + part)
        writer.write(struct.pack("!L", 0))
        await writer.drain()
        reply = (
            (await asyncio.wait_for(reader.read(4096), timeout=60)).decode("utf-8", "replace").strip("\0 \n")
        )
        writer.close()
    except (OSError, TimeoutError) as err:
        return "error", f"The virus scanner could not be reached ({err})."
    if reply.endswith("OK"):
        return "clean", ""
    if "FOUND" in reply:
        return "infected", reply.split(":", 1)[-1].replace("FOUND", "").strip()
    return "error", reply[:200]
