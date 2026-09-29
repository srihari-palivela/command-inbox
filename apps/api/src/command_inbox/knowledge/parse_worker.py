"""Child process entry point for `sandbox.parse_isolated`: `python -m command_inbox.knowledge.parse_worker <kind>`."""

from __future__ import annotations

import json
import sys

from command_inbox.knowledge.parse import PARSERS, parse


def main() -> int:
    kind = sys.argv[1] if len(sys.argv) > 1 else ""
    if kind not in PARSERS:
        print(f"unsupported kind {kind!r}", file=sys.stderr)
        return 2
    blocks = parse(kind, sys.stdin.buffer.read())
    json.dump([{"section": b.section, "text": b.text, "page": b.page} for b in blocks], sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
