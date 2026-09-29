/** "Ticket fields": the case record — status, fields, sub-tasks, activity, customer history, clocks, people. */
import type { CommentKind, TicketDetailDTO, TicketStatus } from '@ci/contracts';
import { STATUS_LABEL } from '@ci/contracts';
import { useState } from 'react';
import { clockTime, formatMinutes, minutesLeftLabel, shortDate } from '../../lib/format';
import {
  confWord,
  LANE_TONE,
  PRIORITY_TONE,
  SLA_TONE,
  STATUS_TONE,
  statusGroup,
} from '../../lib/presentation';
import { usePeople } from '../../lib/queries';
import {
  Avatar,
  Button,
  Card,
  EmptyState,
  Eyebrow,
  Field,
  Input,
  Meter,
  Modal,
  Pill,
  Popover,
  Segmented,
  cx,
} from '../../ui';
import type { TicketActions } from './actions';
import s from './Inbox.module.css';

type LogFilter = 'all' | 'comments' | 'history';

const KIND_TONE: Record<CommentKind, { label: string; fg: string; bg: string }> = {
  note: { label: 'Internal note', fg: 'var(--warn)', bg: 'var(--warn-bg)' },
  public: { label: 'Reply', fg: 'var(--ok)', bg: 'var(--ok-bg)' },
  call: { label: 'Call', fg: 'var(--ok)', bg: 'var(--ok-bg)' },
  system: { label: 'System', fg: 'var(--accent)', bg: 'var(--accent-bg)' },
};

export function TicketFields({
  t,
  actions,
  onOpenLink,
}: {
  t: TicketDetailDTO;
  actions: TicketActions;
  onOpenLink: (ref: string) => void;
}) {
  const [merging, setMerging] = useState(false);
  const group = statusGroup(t.status);
  const tone = STATUS_TONE[group];
  const pr = PRIORITY_TONE[t.priority];
  const lane = LANE_TONE[t.lane];
  const sla = SLA_TONE[t.sla.tone];
  const repeat = t.customer?.history.filter((h) => h.sameTopic).length ?? 0;

  const details: { label: string; value: React.ReactNode; mono?: boolean }[] = [
    {
      label: 'Status',
      value: (
        <Pill fg={tone.fg} bg={tone.bg}>
          {STATUS_LABEL[t.status]}
        </Pill>
      ),
    },
    {
      label: 'Handled by',
      value: (
        <Pill fg={lane.fg} bg={lane.bg}>
          {t.lane === 'auto' ? 'AI acts' : t.lane === 'draft' ? 'AI drafts' : 'A person'}
        </Pill>
      ),
    },
    {
      label: 'Priority',
      value: (
        <Pill fg={pr.fg} bg={pr.bg} title={pr.note}>
          {pr.label}
        </Pill>
      ),
    },
    { label: 'Assignee', value: t.assignee?.name ?? 'Unassigned' },
    { label: 'Reporter', value: t.fromName },
    { label: 'Team', value: t.department },
    { label: 'Category', value: t.category },
    { label: 'Sub-category', value: t.subcategory },
    { label: 'Product', value: t.product },
    { label: 'Query type', value: t.bucket },
    { label: 'Ticket ID', value: t.number, mono: true },
    { label: 'Created', value: clockTime(t.receivedAt), mono: true },
    {
      label: 'Due in',
      value: (
        <span style={{ color: sla.fg }}>
          {t.sla.tone === 'closed'
            ? 'closed'
            : `${minutesLeftLabel(t.sla.minutesLeft)}${t.sla.minutesLeft !== null && t.sla.minutesLeft >= 0 ? ' left' : ''}`}
        </span>
      ),
      mono: true,
    },
    { label: 'First reply', value: t.firstReplyAt ? clockTime(t.firstReplyAt) : 'not yet', mono: true },
    { label: 'Confidence', value: `${confWord(t.confidence)} · ${t.confidence.toFixed(2)}`, mono: true },
    { label: 'Channel', value: 'Email' },
    { label: 'Mailbox', value: t.mailbox, mono: true },
    { label: 'Account', value: t.customer?.account ?? '—', mono: true },
    {
      label: 'Customer ID',
      value: !t.customer ? '—' : (t.customer.cif ?? 'Unmatched sender'),
      mono: !!t.customer?.cif,
    },
    {
      label: 'Customer since',
      value:
        t.customer?.sinceYear != null
          ? `${t.customer.sinceYear} · ${new Date().getFullYear() - t.customer.sinceYear} years`
          : '—',
    },
    { label: 'Segment', value: t.segment || '—' },
    { label: 'Reopen count', value: String(t.reopenCount), mono: true },
    { label: 'Resolution', value: t.resolvedAt ? `Resolved ${clockTime(t.resolvedAt)}` : 'Unresolved' },
    { label: 'Regulatory flag', value: t.regulatoryFlag ?? 'None' },
  ];
  if (t.action) {
    details.push(
      {
        label: 'Reversibility',
        value: (
          <Pill
            fg={t.action.reversible ? 'var(--ok)' : 'var(--bad-text)'}
            bg={t.action.reversible ? 'var(--ok-bg)' : 'var(--bad-bg)'}
          >
            {t.action.reversible ? 'Can be undone' : 'Cannot be undone'}
          </Pill>
        ),
      },
      {
        label: 'Criticality',
        value: (
          <Pill fg="var(--warn)" bg="var(--warn-bg)">
            {t.action.moneyMoves ? 'Money moves' : 'No money moves'}
          </Pill>
        ),
      },
    );
  }

  return (
    <div className={s.fieldsLayout}>
      <div className={s.col}>
        <div style={{ display: 'grid', gap: 8 }}>
          <div className={s.statusBar} role="group" aria-label="Move the ticket">
            <span
              className={s.statusNow}
              style={{ color: tone.fg, background: tone.bg, borderColor: tone.dot }}
            >
              {STATUS_LABEL[t.status]}
            </span>
            {t.allowedTransitions.map((to: TicketStatus) => (
              <Button
                key={to}
                size="sm"
                onClick={() => actions.transition.mutate(to)}
                disabled={!t.permissions.canWork || actions.transition.isPending}
              >
                → {STATUS_LABEL[to]}
              </Button>
            ))}
          </div>
          <div className={s.statusBar} style={{ justifyContent: 'flex-end' }}>
            <Button size="sm" aria-pressed={t.watching} onClick={() => actions.watch.mutate(!t.watching)}>
              {t.watching ? 'Watching' : 'Watch'}
            </Button>
            <Button
              size="sm"
              variant="danger"
              onClick={() => actions.escalate.mutate(undefined)}
              disabled={!t.permissions.canWork}
            >
              Escalate
            </Button>
            <Button
              size="sm"
              onClick={() => actions.split.mutate(undefined)}
              disabled={!t.permissions.canWork}
              title={
                t.splitProposed
                  ? 'The AI found two separate questions in this email.'
                  : 'Split into two tickets'
              }
            >
              Split{t.splitProposed ? ' (suggested)' : ''}
            </Button>
            <Button size="sm" onClick={() => setMerging(true)} disabled={!t.permissions.canWork}>
              Merge
            </Button>
          </div>
        </div>

        <Card title="Details" meta={`${details.length} fields · synced to the case record`} flush>
          <div className={s.grid3}>
            {details.map((d) => (
              <div key={d.label} className={s.gcell}>
                <div className={s.label} style={{ marginBottom: 0 }}>
                  {d.label}
                </div>
                <div
                  className={cx(s.gval, d.mono && 'mono')}
                  title={typeof d.value === 'string' ? d.value : undefined}
                >
                  {d.value}
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card
          title="Sub-tasks"
          meta={`${t.subtasks.filter((x) => x.done).length} of ${t.subtasks.length} done`}
          flush
        >
          {t.subtasks.length === 0 ? (
            <EmptyState title="No sub-tasks" />
          ) : (
            t.subtasks.map((st) => (
              <label key={st.key} className={s.subtask}>
                <input
                  type="checkbox"
                  checked={st.done}
                  disabled={!t.permissions.canWork || st.owner === 'AI'}
                  onChange={(e) => actions.subtask.mutate({ key: st.key, done: e.target.checked })}
                />
                <span className={cx(st.done && s.done)} style={{ flex: 1 }}>
                  {st.label}
                </span>
                <span style={{ fontSize: 11, color: 'var(--muted)' }}>{st.owner}</span>
              </label>
            ))
          )}
        </Card>

        <ActivityLog t={t} actions={actions} />
      </div>

      <div className={s.col}>
        {repeat > 0 && (
          <div className={s.seen} role="note">
            <b style={{ display: 'block', marginBottom: 4 }}>● Seen before</b>
            They have raised this same thing {repeat} time{repeat > 1 ? 's' : ''} before and it has not stuck.
            Treat as a repeat failure, not a fresh query.
          </div>
        )}
        {t.customer && (
          <Card title={<Eyebrow>This customer</Eyebrow>} meta={`${t.customer.history.length} past`} flush>
            <div style={{ display: 'flex', gap: 26, padding: '12px 15px' }}>
              {[
                [String(t.customer.history.length + 1), 'tickets ever'],
                [String(repeat), 'on this topic'],
                t.customer.sinceYear != null
                  ? [String(t.customer.sinceYear), 'customer since']
                  : ['—', 'unmatched sender'],
              ].map(([n, l]) => (
                <div key={l}>
                  <div className="mono" style={{ fontSize: 16, fontWeight: 600 }}>
                    {n}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--muted)' }}>{l}</div>
                </div>
              ))}
            </div>
            {t.customer.history.map((h) => (
              <div key={h.number} className={s.past}>
                <div
                  style={{
                    display: 'flex',
                    gap: 7,
                    alignItems: 'center',
                    fontSize: 11,
                    color: 'var(--muted)',
                  }}
                >
                  <span className="mono">{h.number}</span>
                  {h.sameTopic && (
                    <Pill fg="var(--warn)" bg="var(--warn-bg)">
                      same topic
                    </Pill>
                  )}
                  <span style={{ marginLeft: 'auto' }}>{shortDate(h.at)}</span>
                </div>
                <div style={{ margin: '3px 0' }}>{h.subject}</div>
                <div
                  style={{
                    fontSize: 11.5,
                    color: h.tone === 'ok' ? 'var(--ok)' : h.tone === 'warn' ? 'var(--warn)' : 'var(--bad)',
                  }}
                >
                  {h.outcome}
                </div>
              </div>
            ))}
          </Card>
        )}

        <Card title={<Eyebrow>Deadlines &amp; time</Eyebrow>}>
          <div className={s.clock}>
            <div
              style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, marginBottom: 6 }}
            >
              <span>Time to first reply</span>
              <span className="mono" style={{ color: t.firstReplyAt ? 'var(--ok)' : 'var(--warn)' }}>
                {t.firstReplyAt ? 'met' : 'open'}
              </span>
            </div>
            <Meter
              pct={t.firstReplyAt ? 100 : 40}
              color={t.firstReplyAt ? 'var(--ok-dot)' : 'var(--warn-dot)'}
              label="Time to first reply"
            />
          </div>
          <div className={s.clock}>
            <div
              style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, marginBottom: 6 }}
            >
              <span>Time to resolve</span>
              <span className="mono" style={{ color: sla.fg }}>
                {t.sla.tone === 'closed'
                  ? 'met'
                  : `${minutesLeftLabel(t.sla.minutesLeft)} ${t.sla.minutesLeft !== null && t.sla.minutesLeft >= 0 ? 'left' : ''}`}
              </span>
            </div>
            <Meter
              pct={
                t.sla.minutesLeft === null
                  ? 100
                  : 100 - (Math.max(0, t.sla.minutesLeft) / Math.max(1, t.sla.budgetMinutes)) * 100
              }
              color={
                t.sla.tone === 'on_track' || t.sla.tone === 'closed'
                  ? 'var(--ok-dot)'
                  : t.sla.tone === 'due_soon'
                    ? 'var(--warn-dot)'
                    : 'var(--bad)'
              }
              label="Share of the resolution budget used"
            />
            <div className={s.small}>
              Budget {formatMinutes(t.sla.budgetMinutes)} for {pr.label.split(' · ')[1]?.toLowerCase()}{' '}
              priority
            </div>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, paddingTop: 6 }}>
            <span>Time logged</span>
            <span className="mono">{formatMinutes(t.loggedMinutes)}</span>
          </div>
        </Card>

        <PeopleCard t={t} actions={actions} />

        {t.attachments.length > 0 && (
          <Card title={<Eyebrow>Attachments</Eyebrow>}>
            {t.attachments.map((a) => (
              <div key={a.id} className={s.chipLink}>
                <span
                  className="mono"
                  style={{
                    fontSize: 9.5,
                    background: 'var(--surface-3)',
                    borderRadius: 4,
                    padding: '2px 5px',
                  }}
                >
                  {a.ext.toUpperCase()}
                </span>
                <span
                  style={{
                    flex: 1,
                    minWidth: 0,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {a.name}
                </span>
                <span style={{ fontSize: 11, color: 'var(--muted)' }}>{a.size}</span>
              </div>
            ))}
          </Card>
        )}

        <Card title={<Eyebrow>Labels</Eyebrow>}>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <Pill fg="var(--text-2)" bg="var(--surface-3)">
              {t.department}
            </Pill>
            <Pill fg={lane.fg} bg={lane.bg}>
              {t.lane === 'auto' ? 'automatable' : t.lane === 'draft' ? 'draftable' : 'human only'}
            </Pill>
            {t.regulatoryFlag && (
              <Pill fg="var(--bad-text)" bg="var(--bad-bg)">
                regulatory
              </Pill>
            )}
          </div>
        </Card>

        {t.links.length > 0 && (
          <Card title={<Eyebrow>Linked</Eyebrow>}>
            {t.links.map((l, i) => {
              const clickable = !!l.ref && /^QRY-\d+$/.test(l.ref);
              const inner = (
                <>
                  <span
                    className="mono"
                    style={{
                      fontSize: 9.5,
                      color: 'var(--accent)',
                      background: 'var(--accent-bg-2)',
                      border: '1px solid var(--accent-line)',
                      borderRadius: 4,
                      padding: '2px 5px',
                    }}
                  >
                    {l.kind}
                  </span>
                  <span
                    style={{
                      flex: 1,
                      minWidth: 0,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {l.label}
                  </span>
                </>
              );
              return clickable ? (
                <button
                  key={i}
                  type="button"
                  className={s.chipLink}
                  style={{ width: '100%', background: 'var(--surface)', textAlign: 'left' }}
                  onClick={() => onOpenLink(l.ref!)}
                >
                  {inner}
                </button>
              ) : (
                <div key={i} className={s.chipLink}>
                  {inner}
                </div>
              );
            })}
          </Card>
        )}
      </div>
      <MergeModal open={merging} onClose={() => setMerging(false)} t={t} actions={actions} />
    </div>
  );
}

function ActivityLog({ t, actions }: { t: TicketDetailDTO; actions: TicketActions }) {
  const [filter, setFilter] = useState<LogFilter>('all');
  const [kind, setKind] = useState<'note' | 'public'>('note');
  const [body, setBody] = useState('');
  const items = t.log.filter((l) =>
    filter === 'all'
      ? true
      : filter === 'comments'
        ? l.kind === 'note' || l.kind === 'public' || l.kind === 'call'
        : l.kind === 'system',
  );
  const add = () => actions.comment.mutate({ kind, body: body.trim() }, { onSuccess: () => setBody('') });
  return (
    <Card
      title="Activity"
      actions={
        <Segmented<LogFilter>
          label="Show"
          value={filter}
          onChange={setFilter}
          items={[
            { key: 'all', label: 'All' },
            { key: 'comments', label: 'Comments' },
            { key: 'history', label: 'History' },
          ]}
        />
      }
      flush
    >
      <div style={{ padding: '12px 15px' }}>
        <label className="sr-only" htmlFor="log-body">
          {kind === 'note' ? 'Internal note' : 'Reply visible to the customer'}
        </label>
        <textarea
          id="log-body"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder={
            kind === 'note'
              ? 'Leave an internal note — the customer never sees this…'
              : 'Write a reply the customer will see…'
          }
          style={{
            width: '100%',
            minHeight: 64,
            border: '1px solid var(--line)',
            borderRadius: 9,
            padding: '10px 12px',
            resize: 'vertical',
            fontSize: 12.5,
          }}
        />
        <div className={s.composerFoot}>
          <Segmented<'note' | 'public'>
            label="Visibility"
            value={kind}
            onChange={setKind}
            items={[
              { key: 'note', label: 'Internal note' },
              { key: 'public', label: 'Reply to customer' },
            ]}
          />
          <span className={s.small} style={{ marginTop: 0 }}>
            {kind === 'note' ? 'Visible to your team only' : 'Goes to the customer'}
          </span>
          <span style={{ flex: 1 }} />
          <Button
            size="sm"
            variant="primary"
            disabled={!body.trim() || !t.permissions.canWork}
            loading={actions.comment.isPending}
            onClick={add}
          >
            Add
          </Button>
        </div>
      </div>
      {items.length === 0 ? (
        <EmptyState title="Nothing here yet" />
      ) : (
        items.map((l) => {
          const k = KIND_TONE[l.kind];
          return (
            <div key={l.id} className={s.logItem}>
              <Avatar
                initials={l.kind === 'system' ? 'AI' : l.authorInitials}
                size={24}
                fg={l.kind === 'system' ? 'var(--accent)' : undefined}
                bg={l.kind === 'system' ? 'var(--accent-bg)' : undefined}
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                  <b>{l.authorName}</b>
                  <Pill fg={k.fg} bg={k.bg}>
                    {k.label}
                  </Pill>
                  <span className="mono" style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--muted)' }}>
                    {clockTime(l.at)}
                  </span>
                </div>
                <div style={{ marginTop: 3, color: 'var(--ink-2)', whiteSpace: 'pre-wrap' }}>{l.body}</div>
              </div>
            </div>
          );
        })
      )}
    </Card>
  );
}

function PeopleCard({ t, actions }: { t: TicketDetailDTO; actions: TicketActions }) {
  const [open, setOpen] = useState(false);
  const people = usePeople();
  const eligible = (people.data?.staff ?? []).filter(
    (p) => t.departmentId && (p.clearances[t.departmentId] ?? 0) >= 2,
  );
  const rows: { initials: string; name: string; role: string }[] = [];
  if (t.assignee) rows.push({ initials: t.assignee.initials, name: t.assignee.name, role: 'Assignee' });
  rows.push({ initials: t.fromInitials, name: t.fromName, role: 'Reporter · customer' });
  const checker = t.gate.checker ?? t.gate.proposedChecker;
  if (checker) rows.push({ initials: checker.initials, name: checker.name, role: 'Second approver' });
  for (const w of t.watchers)
    if (!rows.some((r) => r.name === w.name))
      rows.push({ initials: w.initials, name: w.name, role: 'Watching' });
  return (
    <Card
      title={<Eyebrow>People</Eyebrow>}
      actions={
        t.permissions.canAssign ? (
          <div style={{ position: 'relative' }}>
            <Button size="sm" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
              Reassign
            </Button>
            <Popover
              open={open}
              onClose={() => setOpen(false)}
              label="Reassign to"
              style={{ top: 32, right: 0, width: 280, maxHeight: 320, overflowY: 'auto' }}
            >
              <div style={{ padding: '10px 12px 4px' }}>
                <Eyebrow>Cleared for {t.department}</Eyebrow>
              </div>
              <div style={{ padding: 6 }}>
                {eligible.length === 0 && (
                  <div style={{ padding: 8, fontSize: 12, color: 'var(--muted)' }}>
                    No one else is cleared for this team.
                  </div>
                )}
                {eligible.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    className={s.sug}
                    style={{ marginTop: 4 }}
                    onClick={() => {
                      setOpen(false);
                      actions.assign.mutate(p.id);
                    }}
                  >
                    <Avatar initials={p.initials} size={22} />
                    <span>{p.name}</span>
                    <span className={s.sugMeta}>
                      {p.open}/{p.capacity} · {p.availability}
                    </span>
                  </button>
                ))}
              </div>
            </Popover>
          </div>
        ) : undefined
      }
    >
      <div style={{ display: 'grid', gap: 10 }}>
        {rows.map((r) => (
          <div key={r.role + r.name} style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <Avatar initials={r.initials} size={28} />
            <div>
              <div style={{ fontSize: 12.5, fontWeight: 500 }}>{r.name}</div>
              <div style={{ fontSize: 11, color: 'var(--muted)' }}>{r.role}</div>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

function MergeModal({
  open,
  onClose,
  t,
  actions,
}: {
  open: boolean;
  onClose: () => void;
  t: TicketDetailDTO;
  actions: TicketActions;
}) {
  const [n, setN] = useState('');
  const valid = /^QRY-\d+$/.test(n.trim().toUpperCase()) && n.trim().toUpperCase() !== t.number;
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Merge ${t.number}`}
      subtitle="The thread and history move into the other ticket; this one closes with a link to it."
      width={420}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!valid}
            loading={actions.merge.isPending}
            onClick={() => actions.merge.mutate(n.trim().toUpperCase(), { onSuccess: onClose })}
          >
            Merge
          </Button>
        </>
      }
    >
      <Field label="Merge into ticket" hint="For example QRY-48188">
        <Input
          className="mono"
          value={n}
          onChange={(e) => setN(e.target.value)}
          placeholder="QRY-"
          autoFocus
        />
      </Field>
    </Modal>
  );
}
