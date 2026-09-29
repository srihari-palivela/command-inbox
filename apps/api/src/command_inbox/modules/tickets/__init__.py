"""Tickets: the workspace read models and commands.

Reusable by other modules (gateway, calls, intake):
- `load_ticket(tx, org_id, id_or_number)` → the ticket row, or a 404 problem
- `find_ticket(tx, org_id, id_or_number)` → the ticket row or None
- `build_ticket_detail(tx, ctx, id_or_number)` → `TicketDetailDTO`
- `modules.tickets.ops`: `lock_ticket`, `update_ticket`, `system_note`, `add_comment`, `set_subtask`,
  `record_ticket_event`, `next_number`
"""

from __future__ import annotations

from command_inbox.modules.tickets.detail import build_ticket_detail, get_ticket_detail
from command_inbox.modules.tickets.queries import find_ticket, load_ticket

__all__ = ["build_ticket_detail", "find_ticket", "get_ticket_detail", "load_ticket"]
