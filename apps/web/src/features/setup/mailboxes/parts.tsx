import type { AdminDTO } from '@ci/contracts';
import { useState } from 'react';
import { clockTime } from '../../../lib/format';
import { useAuditVerify } from '../../../lib/queries';
import { Button, Card, cx, EmptyState, ErrorState } from '../../../ui';
import s from './Mailboxes.module.css';
import { CONNECTOR_TONE, MAILBOX_TONE } from './tones';

export function MailboxesCard({ mailboxes }: { mailboxes: AdminDTO['mailboxes'] }) {
  return (
    <Card title="Monitored mailboxes" actions={<span className={s.hint}>Read and label only · sending always needs a person</span>} flush className={s.rise} style={{ animationDelay: '.05s' }}>
      {mailboxes.length === 0 && <EmptyState title="No mailboxes connected" text="Connect a shared mailbox from Boards to start triage." />}
      {mailboxes.map((m, i) => {
        const t = MAILBOX_TONE[m.state];
        return (
          <div key={m.id} className={s.row} style={{ animationDelay: `${i * 0.05}s` }}>
            <span className={s.dot} style={{ background: t.dot }} aria-hidden />
            <div className={s.main}>
              <div className={s.title}>{m.address}</div>
              <div className={s.sub}>
                {m.department} · {m.permissions.join(' · ')}
              </div>
            </div>
            <div className={s.vol}>
              <div className={s.volN}>{m.volume24h.toLocaleString('en-IN')}</div>
              <div className={s.volL}>last 24h</div>
            </div>
            <span className={s.state} style={{ color: t.fg, background: t.bg, borderColor: t.line }}>
              {t.label}
            </span>
          </div>
        );
      })}
      <div className={s.foot}>Attachments are parsed for text and never stored outside the bank tenancy. PII is masked before it reaches the classifier.</div>
    </Card>
  );
}

export function ConnectorsCard({ connectors }: { connectors: AdminDTO['connectors'] }) {
  return (
    <Card title="System connectors" actions={<span className={s.hint}>scoped per action, not per system</span>} flush className={s.rise} style={{ animationDelay: '.1s' }}>
      {connectors.length === 0 && <EmptyState title="No systems connected" text="The AI cannot touch any core system until one is connected here." />}
      {connectors.map((c) => {
        const t = CONNECTOR_TONE[c.state];
        return (
          <div key={c.id} className={cx(s.row, s.connRow)}>
            <span className={s.abbr} style={{ color: t.iconFg, background: t.iconBg }} aria-hidden>
              {c.abbr}
            </span>
            <div className={s.main}>
              <div className={s.title}>{c.name}</div>
              <div className={s.sub}>{c.scope}</div>
            </div>
            <span className={s.state} style={{ color: t.fg, background: t.bg }}>
              {t.label}
            </span>
          </div>
        );
      })}
    </Card>
  );
}

export function GuardrailsCard({ guardrails }: { guardrails: string[] }) {
  return (
    <Card title="Guardrails in force" className={s.rise} style={{ animationDelay: '.14s' }}>
      <ul className={s.guards} style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {guardrails.map((g) => (
          <li key={g} className={s.guard}>
            <span className={s.check} aria-hidden>
              ✓
            </span>
            <span>{g}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** Recomputes the audit log's hash chain on the server and reports where, if anywhere, it breaks. */
export function AuditCard() {
  const [asked, setAsked] = useState(false);
  const q = useAuditVerify(asked);
  const run = () => (asked ? void q.refetch() : setAsked(true));
  const r = q.data;
  return (
    <Card title="Audit log integrity" className={s.rise} style={{ animationDelay: '.18s' }}>
      <div className={s.audit}>
        <p className={s.auditText}>Every decision is written to a hash-chained log. Verifying recomputes each link, so an edited or deleted event shows up as a break.</p>
        <Button variant="dark" loading={q.isFetching} onClick={run}>
          Verify the chain
        </Button>
      </div>
      {q.error ? (
        <div style={{ marginTop: 12 }}>
          <ErrorState error={q.error} onRetry={() => void q.refetch()} />
        </div>
      ) : r ? (
        <div
          role="status"
          className={s.result}
          style={r.ok ? { color: 'var(--ok)', background: 'var(--ok-bg-2)', borderColor: 'var(--ok-line)' } : { color: 'var(--bad-text)', background: 'var(--bad-bg)', borderColor: 'var(--bad-line)' }}
        >
          {r.ok ? (
            <span>
              ✓ <span className="mono">{r.events.toLocaleString('en-IN')}</span> events · chain intact
            </span>
          ) : (
            <span>
              × Broken at event <span className="mono">#{r.brokenAt}</span> of <span className="mono">{r.events.toLocaleString('en-IN')}</span> — raise it with Risk now
            </span>
          )}
          <span className={s.resultMeta}>checked {clockTime(new Date(q.dataUpdatedAt).toISOString())}</span>
        </div>
      ) : null}
    </Card>
  );
}
