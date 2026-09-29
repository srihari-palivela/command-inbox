# Frontend review — Command Inbox prototype

**Reviewed:** `docs/design-source/Command Inbox.dc.html` rendered at 1600×1000 in Chromium, all 15 screens and
the main overlays, as Staff, Team lead and Admin. This review builds on the design team's own UX review
(`docs/design-source/UX Review.dc.html`), which already fixed five findings. Everything in that document
still holds; below are the additional issues found while turning the prototype into a product, and what the
production frontend does about each.

**Verdict.** The information architecture is strong and should be kept: the three lanes, the approval
gateway at the bottom of the ticket, evidence-backed reasoning, the risk matrix, and plain-language
navigation. Nearly every defect found is the kind a prototype carries because its state was local and
simulated — correctness of the approval path, missing URLs, and interaction details — not visual design.

## 1. Correctness defects (fixed — these would have been real incidents)

| # | Where | Defect | Fix in production |
|---|---|---|---|
| C1 | Approval gateway, lane A | After approving an irreversible action ("Cannot be undone"), the done state offers **"Undo 29s"**. Undo is offered for every non-manual lane | Undo is offered only for reversible actions, and implemented as a delayed commit so nothing reaches core banking before the window closes. Irreversible actions show "Executed — cannot be undone" |
| C2 | Approval gateway, maker–checker | One click on "Approve & execute" completes both approvals — "checked by R. Menon" is simulated | Maker approval moves the action to **Waiting for checker**; it appears in the checker's queue; the checker must be a different person with *approve* clearance. The gateway shows who is holding it and for how long, and escalates after 2 h |
| C3 | Reject → "This should never be automated" | Staff reject silently **adds a hard-stop rule** | The rejection files a *proposed* rule that an Admin approves on Rules & policies. The permission matrix says only Admin edits rules, and the product now obeys its own matrix |
| C4 | Override lane | Any role can move a ticket *up* to Auto | Moving toward less autonomy (Auto → Draft → You) is open to everyone. Moving toward more needs Team lead or above, and the policy engine can still refuse (e.g. never Auto in the locked cell) |
| C5 | AI trace, Priority Ranker span | Shows "P3 · Normal" for an "Urgent · Corporate" ticket because two priority formats co-exist | One priority model (P1–P4) plus segment. Every surface derives from it |
| C6 | Call overlay | Esc closes other overlays but leaves the call wrap-up open underneath the palette | One overlay manager: a single overlay is open at a time, Esc closes the top one, focus returns to the trigger |
| C7 | Inbox counts | "6 for you" is a constant; the Tickets board shows a different universe | Counts come from the same query as the list; the badge in the nav and the header number cannot disagree |

## 2. Interaction and layout issues (fixed)

| # | Where | Issue | Fix |
|---|---|---|---|
| U1 | Gateway primary button | "Approve & execute" wraps onto three lines at 1600 px | Fixed-width, single-line primary button with keyboard hint; secondary buttons collapse into a menu below 1280 px |
| U2 | Ticket fields grid | Pills truncate ("Waiting on a", "Cannot be un") in the 3-column grid | Pills never truncate: long values wrap to a second line, and the grid uses `minmax()` columns |
| U3 | Everything | No URLs — a ticket, a filtered board or a screen cannot be linked, bookmarked or opened in a new tab; browser Back does nothing | Router with deep links: `/inbox/:ticket`, `/tickets?status=…&q=…`, `/agents/:id`, etc. |
| U4 | Layout | `main` has `min-width: 1120px`; below ~1350 px wide the whole app scrolls horizontally | Responsive breakpoints: the activity rail collapses at < 1440 px, the queue narrows at < 1280 px, and setup screens reflow to one column |
| U5 | Reasoning stream | Re-streams from scratch every time a ticket is re-selected, and cannot be skipped | Streams once per triage run; click or `Esc` shows it in full; respects the "Show the AI thinking" setting and `prefers-reduced-motion` |
| U6 | Toasts | Every action ends in a toast, including destructive ones, with no way to undo from the toast | Toasts carry an action where one exists ("Undo", "Open ticket"); success is otherwise shown in place |
| U7 | Empty/loading/error | None exist (static data) | Skeletons for first load, explicit empty states ("Nothing waiting on you"), inline error with retry |
| U8 | Keyboard | J/K/A only on the Inbox; no shortcut help; `A` approves without focus on the gate | `?` opens a shortcut sheet; `A` scrolls to and focuses the gate first, then requires a second `A` (or Enter) for irreversible actions |
| U9 | Filters | Faceted filters and NL chips are lost on navigation | Filters live in the URL, so they survive navigation and can be shared |
| U10 | Role switch in the chrome | Useful for a demo, dangerous in a product: it implies anyone can become Admin | The role comes from the signed-in membership. In demo mode, the segmented control signs in as the seeded person for that role, and the server enforces that person's real permissions |

## 3. Accessibility

- Clickable `div`s and `span`s → `button`/`a` with visible focus rings (the prototype has no focus styles).
- Dialogs (`role="dialog"`, `aria-modal`, labelled, focus-trapped), menus (`role="menu"`), tabs (`role="tablist"`).
- Colour is never the only signal: the lane, deadline, and confidence pills all carry a word as well as a colour,
  as the design team's finding 03 already established.
- Live regions announce gate results ("Action waiting for checker") and toasts.
- Motion: `riseIn`/`slideLeft`/stream animations are disabled under `prefers-reduced-motion`.

## 4. Watchlist items from the design review — implemented

| Watchlist item | Implementation |
|---|---|
| Gate volume / rubber-stamping | Every approval records whether the approver opened the evidence (reasoning, fields or draft) before approving; **approve-without-open rate** is a Performance KPI and alerts above 15% |
| Batch approvals | Related pending approvals (same template, same department) can be selected together on the Tickets list and approved in one step; each still writes its own audit entry and idempotency key |
| Draft edits as eval data | Draft edits are stored as original/current diffs and fed into the Reply Drafter's feedback store |
| Overconfidence drift | The agent drawer shows **calibration**: observed accuracy by confidence band, next to the eval score |
| Global search | ⌘K searches tickets, customers (CIF, name), knowledge documents and policies from the same box |

## 5. Visual refinements (keeping the design language)

- Design tokens are extracted as CSS variables, so the palette stays exactly as designed and dark mode becomes possible later.
- Status, lane, priority and deadline pills share one `Pill` component with fixed metrics (height 20, radius 5, 11 px type).
- Numbers use IBM Plex Mono with `font-variant-numeric: tabular-nums`, so counts don't jitter as they update live.
- The nav badge "Performance · 4" becomes the live number of active alerts, and "What it knows · 12" the live number of open gaps.
- The "Agent live" pill reflects real worker health: live, degraded (LLM unavailable, falling back to people), or paused.
