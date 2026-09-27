# Frontend conventions

The web app (`apps/web`) is a React 19 + TypeScript SPA built with Vite, React Router 7 and TanStack Query.
It is a production port of the Claude Design prototype in `docs/design-source/Command Inbox.dc.html`;
the prototype is the visual source of truth, and `docs/ux/frontend-review.md` lists the refinements we
apply on top of it.

## Layout of the code

```
apps/web/src/
  app/            shell (header, nav), routes, sign-in, ⌘K palette, notification center, UI context
  ui/             primitives: Button, Pill, Chip, Card, Eyebrow, Tabs, Segmented, Toggle, Avatar, Dot,
                  Meter, Spark, Kbd, Field, Input, TextArea, Modal, Drawer, Popover, MenuItem, Toaster,
                  EmptyState, Skeleton, ErrorState, Page, PageHeader, Loadable
  lib/            api client, query hooks (queries.ts), live updates (SSE), presentation maps,
                  formatting, toast, preferences
  features/<area> one folder per product area; screens are the default export of `<Name>Screen.tsx`
  styles/         tokens.css (all colours, radii, fonts), base.css (reset, keyframes)
```

## Rules

1. **Server state only through `lib/queries.ts`.** Read with the existing hooks (`useInbox`, `useTickets`,
   `useTicket`, `usePerformance`, …). Write with `useAction(fn, { invalidate, success })` and the `api`
   client. Errors from the server are toasted automatically; don't add ad-hoc error toasts.
2. **The server is the authority on permissions.** Hide or disable controls the user cannot use
   (`me.capabilities`, `ticket.permissions`, `gate.canApprove`/`blockedReason`), and say why in a
   `title` or inline note — but never rely on the UI for enforcement.
3. **No colours in feature code except through tokens** (`var(--accent)`, `var(--ok)`, …) and the maps in
   `lib/presentation.ts`. Every status shows a word as well as a colour.
4. **Styling:** CSS Modules per feature (`Thing.module.css`) using the tokens. Match the prototype's metrics
   (font sizes, paddings, radii). Numbers, IDs and codes use the mono font (`className="mono"`).
5. **Motion:** reuse the global keyframes (`riseIn`, `fadeIn`, `slideLeft`, `growX`, `growY`, `breathe`,
   `popIn`) with small staggered delays, as the prototype does. Reduced motion is handled globally.
6. **Accessibility:** every clickable is a `button` or link; inputs have labels; dialogs use `Modal` or
   `Drawer` (focus trap, Escape, focus restore); tabs use `Tabs`; toggles use `Toggle` (`role="switch"`).
7. **States:** wrap query results in `Loadable` (skeleton + error with retry) and render an `EmptyState`
   for empty lists — the prototype had none of these.
8. **URLs:** anything worth sharing lives in the URL (selected ticket, board filters, view mode).
9. **Copy:** keep the prototype's plain-language wording ("What it can do", "Who owns what", "Auto — the
   AI does it"). Don't invent jargon.

## Running it

```
pnpm --filter @ci/server dev    # API on :4000 (worker embedded in dev)
pnpm --filter @ci/web dev       # SPA on :5173, proxies /v1 to the API
```

Demo sign-in: `p.sharma@bank.example` (staff), `r.menon@bank.example` (team lead, checker),
`a.kapoor@bank.example` (admin, Risk & Compliance).
