/**
 * End-to-end behaviour of the API against a real, seeded Postgres: authentication and CSRF, tenant
 * isolation (RLS), the append-only audit chain, and the approval gateway's safety rules.
 */
import { sql } from 'drizzle-orm';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { buildApp } from '../../src/app.js';
import { closeDb, db, withTenant } from '../../src/db/client.js';
import { clock } from '../../src/platform/clock.js';
import { createWorker } from '../../src/worker-registry.js';
import { signIn, type App, type Session } from './helpers.js';

let app: App;
let staff: Session;
let lead: Session;
let admin: Session;
const worker = createWorker();

beforeAll(async () => {
  app = await buildApp();
  await app.ready();
  staff = await signIn(app, 'p.sharma@bank.example');
  lead = await signIn(app, 'r.menon@bank.example');
  admin = await signIn(app, 'a.kapoor@bank.example');
});

afterAll(async () => {
  clock.reset();
  await app.close();
  await closeDb();
});

describe('authentication and CSRF', () => {
  it('answers unauthenticated calls with a 401 problem document', async () => {
    const r = await app.inject({ method: 'GET', url: '/v1/inbox' });
    expect(r.statusCode).toBe(401);
    expect(r.headers['content-type']).toMatch(/problem\+json/);
    expect(r.json()).toMatchObject({ status: 401, code: 'unauthenticated' });
  });

  it('rejects a state-changing call without the CSRF token', async () => {
    const r = await app.inject({ method: 'POST', url: '/v1/tickets/QRY-48199/watch', headers: { cookie: staff.cookie }, payload: { watching: true } });
    expect(r.statusCode).toBe(403);
    expect(r.json().code).toBe('csrf');
  });

  it('validates bodies and reports the problem', async () => {
    const r = await staff.req('POST', '/v1/tickets/QRY-48199/gate/reject', { reason: 'because' });
    expect(r.statusCode).toBe(400);
  });
});

describe('tenant isolation', () => {
  it('row-level security hides every row when no tenant is set', async () => {
    const r = await db.execute<{ n: number }>(sql`select count(*)::int as n from tickets`);
    expect(r.rows[0]!.n).toBe(0);
  });

  it('scopes reads to the tenant set on the transaction', async () => {
    const apex = await withTenant(staff.me.org.id, (tx) => tx.execute<{ n: number; orgs: number }>(sql`select count(*)::int as n, count(distinct org_id)::int as orgs from tickets`));
    expect(apex.rows[0]!.n).toBeGreaterThan(10);
    expect(apex.rows[0]!.orgs).toBe(1);
  });

  it('a ticket from another workspace is not found, not forbidden', async () => {
    const apexTicket = await staff.ticket('QRY-48199');
    const other = staff.me.memberships.find((m) => m.org.id !== staff.me.org.id)!;
    const moved = await signIn(app, 'p.sharma@bank.example');
    const sw = await moved.req('POST', '/v1/session/org', { orgId: other.org.id });
    expect(sw.statusCode).toBe(200);
    const r = await moved.req('GET', `/v1/tickets/${apexTicket.id}`);
    expect(r.statusCode).toBe(404);
  });
});

describe('audit log', () => {
  it('cannot be rewritten by the application role', async () => {
    await expect(withTenant(staff.me.org.id, (tx) => tx.execute(sql`update audit_events set summary = 'tampered'`))).rejects.toThrow();
    await expect(withTenant(staff.me.org.id, (tx) => tx.execute(sql`delete from audit_events`))).rejects.toThrow();
  });
});

describe('approval gateway — irreversible money movement (maker + checker)', () => {
  it('records the maker, replays an idempotent retry, and waits for a checker', async () => {
    const t = await staff.ticket('QRY-48211');
    expect(t.gate).toMatchObject({ mode: 'action', chain: 'dual', reversible: false, moneyMoves: true, canApprove: true, canUndo: false });
    expect(t.gate.proposedChecker?.name).toBe('R. Menon');

    const key = 'it-48211-maker';
    const first = await staff.req('POST', `/v1/tickets/${t.id}/gate/approve`, { openedEvidence: true }, { 'idempotency-key': key });
    expect(first.statusCode).toBe(200);
    expect(first.json()).toEqual({ outcome: 'awaiting_checker' });
    const replay = await staff.req('POST', `/v1/tickets/${t.id}/gate/approve`, { openedEvidence: true }, { 'idempotency-key': key });
    expect(replay.statusCode).toBe(200);
    expect(replay.json()).toEqual({ outcome: 'awaiting_checker' });

    const approvals = await withTenant(staff.me.org.id, (tx) =>
      tx.execute<{ n: number }>(sql`select count(*)::int as n from approvals where subject_id = ${t.action!.id} and step = 'maker'`),
    );
    expect(approvals.rows[0]!.n).toBe(1);
  });

  it('never lets the maker be their own checker', async () => {
    const t = await staff.ticket('QRY-48211');
    expect(t.gate.state).toBe('awaiting_checker');
    expect(t.gate.canApprove).toBe(false);
    const r = await staff.req('POST', `/v1/tickets/${t.id}/gate/approve`, { openedEvidence: true }, { 'idempotency-key': 'it-48211-self-check' });
    expect(r.statusCode).toBeGreaterThanOrEqual(400);
    expect((await staff.ticket('QRY-48211')).gate.state).toBe('awaiting_checker');
  });

  it('executes after the checker approves, with no undo, and writes the audit trail', async () => {
    const t = await lead.ticket('QRY-48211');
    expect(t.gate.canApprove).toBe(true);
    const r = await lead.req('POST', `/v1/tickets/${t.id}/gate/approve`, { openedEvidence: true }, { 'idempotency-key': 'it-48211-checker' });
    expect(r.statusCode).toBe(200);
    await worker.drain();
    const done = await lead.ticket('QRY-48211');
    expect(done.action?.state).toBe('executed');
    expect(done.action?.externalRef).toBeTruthy();
    expect(done.gate).toMatchObject({ state: 'done', canUndo: false });
    const undo = await lead.req('POST', `/v1/tickets/${t.id}/gate/undo`);
    expect(undo.statusCode).toBe(409);
  });

  it('keeps the audit chain intact after all of that', async () => {
    const r = await admin.req('GET', '/v1/audit/verify');
    expect(r.statusCode).toBe(200);
    expect(r.json()).toMatchObject({ ok: true, brokenAt: null });
  });
});

describe('approval gateway — replies are recallable inside the window only', () => {
  it('recalls a queued reply, then sends once the window passes', async () => {
    const t = await staff.ticket('QRY-48207');
    expect(t.gate).toMatchObject({ mode: 'draft', state: 'open' });
    const approve = await staff.req('POST', `/v1/tickets/${t.id}/gate/approve`, { openedEvidence: true }, { 'idempotency-key': 'it-48207-send-1' });
    expect(approve.json()).toEqual({ outcome: 'sending' });
    const queued = await staff.ticket('QRY-48207');
    expect(queued.gate).toMatchObject({ state: 'scheduled', canUndo: true });

    expect((await staff.req('POST', `/v1/tickets/${t.id}/gate/undo`)).statusCode).toBe(200);
    expect((await staff.ticket('QRY-48207')).gate.state).toBe('open');
    await worker.drain();
    expect((await staff.ticket('QRY-48207')).draft?.state).toBe('draft');

    await staff.req('POST', `/v1/tickets/${t.id}/gate/approve`, { openedEvidence: true }, { 'idempotency-key': 'it-48207-send-2' });
    clock.advance(61_000);
    try {
      expect((await staff.req('POST', `/v1/tickets/${t.id}/gate/undo`)).statusCode).toBe(409);
      await worker.drain();
    } finally {
      clock.reset();
    }
    const sent = await staff.ticket('QRY-48207');
    expect(sent.draft?.state).toBe('sent');
    expect(sent.gate.state).toBe('done');
  });
});

describe('roles and permissions', () => {
  it('staff cannot widen autonomy or change clearances', async () => {
    expect((await staff.req('PUT', '/v1/actions/dial', { cell: '0-0', level: 2 })).statusCode).toBe(403);
    const dept = (await lead.req('GET', '/v1/people')).json().departments[0].id as string;
    expect((await staff.req('PUT', '/v1/clearances', { userId: staff.me.user.id, departmentId: dept, level: 3 })).statusCode).toBe(403);
  });

  it('the irreversible + money cell cannot be dialled up, even by an admin', async () => {
    const r = await admin.req('PUT', '/v1/actions/dial', { cell: '1-1', level: 1 });
    expect(r.statusCode).toBeGreaterThanOrEqual(400);
  });

  it('a lead cannot write a clearance for someone outside the workspace', async () => {
    const dept = (await lead.req('GET', '/v1/people')).json().departments[0].id as string;
    const r = await lead.req('PUT', '/v1/clearances', { userId: '00000000-0000-4000-8000-000000000000', departmentId: dept, level: 1 });
    expect(r.statusCode).toBe(404);
  });

  it('staff can read the clearance matrix but not edit it', async () => {
    const r = await staff.req('GET', '/v1/people');
    expect(r.statusCode).toBe(200);
    expect(r.json().editable).toBe(false);
  });

  it('batch approvals count as approving without opening the evidence', async () => {
    const before = await withTenant(staff.me.org.id, (tx) => tx.execute<{ n: number }>(sql`select count(*)::int as n from approvals where not opened_evidence`));
    const t = await staff.ticket('QRY-48188');
    const r = await staff.req('POST', '/v1/gate/batch-approve', { ticketIds: [t.id] });
    expect(r.statusCode).toBe(200);
    expect(r.json().results[0]).toMatchObject({ ok: true });
    const after = await withTenant(staff.me.org.id, (tx) => tx.execute<{ n: number }>(sql`select count(*)::int as n from approvals where not opened_evidence`));
    expect(after.rows[0]!.n).toBe(before.rows[0]!.n + 1);
  });
});
