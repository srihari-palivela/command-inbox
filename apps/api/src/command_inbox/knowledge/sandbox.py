"""Run a parser in a separate, resource-limited process.

The child gets the file on stdin and returns JSON blocks on stdout. It runs with a memory cap (1 GiB of
address space), a CPU-time cap (30 s), no open files beyond its pipes, and a wall-clock timeout (60 s); a
hostile or broken file can crash or stall it without touching the worker. Network isolation for the child is
enforced where the worker runs (egress policy), not here.
"""

from __future__ import annotations

import asyncio
import json
import sys

from command_inbox.knowledge.parse import Block

MEMORY_BYTES = 1024 * 1024 * 1024
CPU_SECONDS = 30
WALL_SECONDS = 60


class ParseFailed(Exception):
    pass


def _limits() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


async def parse_isolated(kind: str, data: bytes) -> list[Block]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "command_inbox.knowledge.parse_worker",
        kind,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=_limits,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(data), timeout=WALL_SECONDS)
    except TimeoutError as exc:
        proc.kill()
        raise ParseFailed("Parsing took too long and was stopped.") from exc
    if proc.returncode != 0:
        reason = err.decode("utf-8", "replace").strip().splitlines()[-1:] or ["the parser stopped"]
        raise ParseFailed(f"The document could not be read ({reason[0][:200]}).")
    return [Block(b["section"], b["text"], b.get("page")) for b in json.loads(out)]
