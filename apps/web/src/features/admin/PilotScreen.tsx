/**
 * The pilot at the bank: which stage the workspace is in (onboarding → shadow → assisted → live), the gates for
 * the next one with today's numbers, the four-eyes sign-off, the pilot's KPIs, the AI-vs-people comparison from
 * shadow mode, and the incident log. Every number comes from records on the server.
 */
import type {
  IncidentKind,
  IncidentSeverity,
  PilotBaselineBody,
  PilotDTO,
  PilotGateDTO,
  PilotIncidentBody,
  PilotIncidentDTO,
  PilotRequestDTO,
  PilotSettingsBody,
  PilotStage,
  PilotStageBody,
  ShadowReportDTO,
} from '@ci/contracts';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../lib/api';
import { ago } from '../../lib/format';
import { keys, useMembers } from '../../lib/queries';
import {
  Button,
  Card,
  Chip,
  EmptyState,
  Field,
  Input,
  Loadable,
  Modal,
  Page,
  PageHeader,
  Skeleton,
  TextArea,
} from '../../ui';
import { Check, isForbidden, NoAccess, Notice, ProblemAlert, Select, useCaps, useInlineAction } from './bits';
import s from './admin.module.css';

const STAGES: { key: PilotStage; label: string; text: string }[] = [
  { key: 'onboarding', label: 'Onboarding', text: 'Set up, evaluate, record the baseline' },
  { key: 'shadow', label: 'Shadow', text: 'AI decides and drafts; nothing is sent' },
  { key: 'assisted', label: 'Assisted', text: 'Drafts go out after a person approves' },
  { key: 'live', label: 'Live', text: 'The whole team works this way' },
];
const LABEL: Record<string, string> = Object.fromEntries(STAGES.map((x) => [x.key, x.label]));
const GATE_TONE: Record<PilotGateDTO['state'], { word: string; color: string }> = {
  pass: { word: 'Met', color: 'var(--ok)' },
  fail: { word: 'Not met', color: 'var(--bad-text)' },
  pending: { word: 'Waiting', color: 'var(--muted)' },
};
const SEVERITIES: IncidentSeverity[] = ['P1', 'P2', 'P3', 'P4'];
const KINDS: { key: IncidentKind; label: string }[] = [
  { key: 'hard_stop_miss', label: 'Hard stop missed' },
  { key: 'wrong_reply', label: 'Wrong or harmful reply' },
  { key: 'data_exposure', label: 'Data shown or sent wrongly' },
  { key: 'outage', label: 'Outage or delay' },
  { key: 'other', label: 'Other' },
];
const KIND_LABEL = Object.fromEntries(KINDS.map((k) => [k.key, k.label])) as Record<IncidentKind, string>;
const pilotKey = ['pilot'] as const;
const incidentsKey = ['pilot', 'incidents'] as const;
const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`);

export default function PilotScreen() {
  const q = useQuery({ queryKey: pilotKey, queryFn: () => api.get<PilotDTO>('/v1/pilot') });
  return (
    <Page>
      <PageHeader
        title="Pilot"
        subtitle="Move from shadow to assisted to live on evidence, with a second person signing off each step forward."
      />
      {isForbidden(q.error) ? (
        <NoAccess error={q.error} what="the pilot" who="The pilot is run by admins and team leads." />
      ) : (
        <Loadable query={q} skeleton={<Skeleton h={520} />}>
          {(p) => (
            <div className={s.stack}>
              <StageCard p={p} />
              <GatesCard p={p} />
              <KpiCard p={p} />
              <ShadowCard />
              <IncidentsCard />
              {p.canEditSettings && <SettingsCard p={p} />}
            </div>
          )}
        </Loadable>
      )}
    </Page>
  );
}

function StageCard({ p }: { p: PilotDTO }) {
  const at = STAGES.findIndex((x) => x.key === p.stage);
  const [back, setBack] = useState<PilotStage | null>(null);
  const [reason, setReason] = useState('');
  const step = useInlineAction((b: PilotStageBody) => api.post<PilotDTO>('/v1/pilot/step-back', b), {
    invalidate: [pilotKey, keys.onboarding],
    success: (r) => `The pilot is back in ${LABEL[r.stage] ?? r.stage}.`,
  });
  return (
    <Card
      title="Stage"
      meta={`${LABEL[p.stage] ?? p.stage} for ${p.daysInStage} day${p.daysInStage === 1 ? '' : 's'}`}
    >
      <ol
        aria-label="Pilot stages"
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
          gap: 8,
          margin: 0,
          padding: 0,
          listStyle: 'none',
        }}
      >
        {STAGES.map((x, i) => (
          <li
            key={x.key}
            aria-current={i === at ? 'step' : undefined}
            className={s.metric}
            style={{
              borderColor: i === at ? 'var(--accent)' : undefined,
              opacity: at >= 0 && i > at ? 0.6 : 1,
            }}
          >
            <div className={s.metricVal} style={{ fontSize: 14 }}>
              {i < at ? '✓ ' : ''}
              {x.label}
            </div>
            <div className={s.metricLbl}>{x.text}</div>
          </li>
        ))}
      </ol>
      <div className={s.stack} style={{ marginTop: 12 }}>
        {p.sendsAllowed ? (
          <Notice tone="ok">
            Replies are sent from Command Inbox after a person approves them. The automatic lane stays off in
            this release.
          </Notice>
        ) : (
          <Notice tone="warn">
            Nothing is sent from Command Inbox yet: your team replies from its usual mail client while the
            AI’s decisions are recorded for comparison.
          </Notice>
        )}
        {p.canStepBack && (
          <div className={s.row}>
            <span className={s.muted}>
              Something wrong? Stepping back takes effect at once and needs no sign-off.
            </span>
            <Select
              aria-label="Step back to"
              value=""
              onChange={(e) => setBack(e.target.value as PilotStage)}
            >
              <option value="" disabled>
                Step back to…
              </option>
              {STAGES.slice(0, Math.max(at, 0)).map((x) => (
                <option key={x.key} value={x.key}>
                  {x.label}
                </option>
              ))}
            </Select>
          </div>
        )}
      </div>
      <Modal
        open={back !== null}
        onClose={() => setBack(null)}
        title={`Step back to ${back ? LABEL[back] : ''}`}
        width={460}
        footer={
          <>
            <Button variant="ghost" onClick={() => setBack(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              loading={step.isPending}
              disabled={!reason.trim()}
              onClick={() =>
                back &&
                step.mutate(
                  { toStage: back, reason: reason.trim() },
                  { onSuccess: () => (setBack(null), setReason('')) },
                )
              }
            >
              Step back now
            </Button>
          </>
        }
      >
        <div className={s.stack}>
          <Field label="Why">
            <TextArea aria-label="Why" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
          <ProblemAlert error={step.error} />
        </div>
      </Modal>
    </Card>
  );
}

function GateTable({ gates, label }: { gates: PilotGateDTO[]; label: string }) {
  return (
    <table className={s.table} aria-label={label}>
      <thead>
        <tr>
          <th scope="col">Gate</th>
          <th scope="col" className={s.num}>
            Now
          </th>
          <th scope="col" className={s.num}>
            Target
          </th>
          <th scope="col">State</th>
        </tr>
      </thead>
      <tbody>
        {gates.map((g) => (
          <tr key={g.key}>
            <td>{g.label}</td>
            <td className={s.num}>{g.value}</td>
            <td className={s.num}>{g.target}</td>
            <td style={{ color: GATE_TONE[g.state].color, fontWeight: 600 }}>{GATE_TONE[g.state].word}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function GatesCard({ p }: { p: PilotDTO }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const ask = useInlineAction((b: PilotStageBody) => api.post<PilotDTO>('/v1/pilot/requests', b), {
    invalidate: [pilotKey],
    success: 'Sent for sign-off.',
  });
  if (!p.next) {
    return (
      <Card title="Next stage">
        <EmptyState
          title="The pilot is complete"
          text="Keep watching the KPIs below. Autonomy beyond approved drafts is not part of this release."
        />
      </Card>
    );
  }
  return (
    <Card title={`Moving to ${LABEL[p.next]}`} meta={p.ready ? 'All gates met' : 'Gates not met yet'}>
      <div className={s.stack}>
        <GateTable gates={p.gates} label={`Gates for ${LABEL[p.next]}`} />
        {p.pending ? (
          <PendingRequest r={p.pending} />
        ) : (
          p.canRequest && (
            <div className={s.row}>
              <Button variant="dark" disabled={!p.ready} onClick={() => setOpen(true)}>
                Ask to move to {LABEL[p.next]}
              </Button>
              {!p.ready && <span className={s.muted}>Every gate has to be met first.</span>}
            </div>
          )
        )}
      </div>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={`Ask to move to ${LABEL[p.next]}`}
        width={480}
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="dark"
              loading={ask.isPending}
              disabled={!reason.trim()}
              onClick={() =>
                ask.mutate(
                  { toStage: p.next as PilotStage, reason: reason.trim() },
                  { onSuccess: () => (setOpen(false), setReason('')) },
                )
              }
            >
              Send for sign-off
            </Button>
          </>
        }
      >
        <div className={s.stack}>
          <p className={s.muted} style={{ margin: 0 }}>
            {p.riskApprovers.length
              ? `${p.riskApprovers.map((u) => u.name).join(', ')} can sign this off.`
              : 'Another admin signs this off.'}{' '}
            The gates are checked again when they do.
          </p>
          <Field label="Why now">
            <TextArea
              aria-label="Why now"
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </Field>
          <ProblemAlert error={ask.error} />
        </div>
      </Modal>
    </Card>
  );
}

function PendingRequest({ r }: { r: PilotRequestDTO }) {
  const [refusing, setRefusing] = useState(false);
  const [note, setNote] = useState('');
  const decide = useInlineAction(
    (b: { approve: boolean; note: string }) => api.post<PilotDTO>(`/v1/pilot/requests/${r.id}/decision`, b),
    {
      invalidate: [pilotKey, keys.onboarding, keys.me],
      success: (res, v) =>
        v.approve ? `Signed off: the pilot is now in ${LABEL[res.stage] ?? res.stage}.` : 'Refused.',
    },
  );
  const withdraw = useInlineAction(() => api.del<PilotDTO>(`/v1/pilot/requests/${r.id}`), {
    invalidate: [pilotKey],
    success: 'Request withdrawn.',
  });
  return (
    <Notice tone="info">
      <div className={s.stack}>
        <div>
          <strong>Waiting for sign-off:</strong> {LABEL[r.fromStage]} → {LABEL[r.toStage]}, asked by{' '}
          {r.requestedBy?.name ?? 'someone'} {ago(r.requestedAt)}.
        </div>
        <div className={s.muted}>“{r.reason}”</div>
        <div className={s.row}>
          {r.canDecide && (
            <>
              <Button
                variant="dark"
                loading={decide.isPending}
                onClick={() => decide.mutate({ approve: true, note: '' })}
              >
                Sign off
              </Button>
              <Button onClick={() => setRefusing(true)}>Refuse</Button>
            </>
          )}
          {r.canWithdraw && (
            <Button variant="ghost" loading={withdraw.isPending} onClick={() => withdraw.mutate(undefined)}>
              Withdraw
            </Button>
          )}
          {!r.canDecide && <span className={s.muted}>A second person signs this off.</span>}
        </div>
        <ProblemAlert error={decide.error ?? withdraw.error} />
      </div>
      <Modal
        open={refusing}
        onClose={() => setRefusing(false)}
        title="Refuse this stage change"
        width={460}
        footer={
          <>
            <Button variant="ghost" onClick={() => setRefusing(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              disabled={!note.trim()}
              loading={decide.isPending}
              onClick={() =>
                decide.mutate({ approve: false, note: note.trim() }, { onSuccess: () => setRefusing(false) })
              }
            >
              Refuse
            </Button>
          </>
        }
      >
        <Field label="Why">
          <TextArea
            aria-label="Why refused"
            rows={3}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </Field>
      </Modal>
    </Notice>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className={s.metric}>
      <div className={s.metricVal}>{value}</div>
      <div className={s.metricLbl}>{label}</div>
    </div>
  );
}

function KpiCard({ p }: { p: PilotDTO }) {
  const k = p.kpis;
  const b = p.baseline;
  return (
    <Card title="Pilot KPIs" meta={`since ${new Date(k.windowStart).toLocaleDateString()}`}>
      <div className={s.metrics}>
        <Stat
          label={`Drafts accepted (target ${pct(p.targets.acceptance)})`}
          value={`${pct(k.acceptanceRate)} of ${k.draftsDecided}`}
        />
        <Stat label={`Category agreement (${k.labelled} labelled)`} value={pct(k.categoryAgreement)} />
        <Stat label={`Lane agreement (${k.laneCompared} mails)`} value={pct(k.laneAgreement)} />
        <Stat label="Hard-stop misses" value={String(k.hardStopMisses)} />
        <Stat label={`On time (baseline ${pct(b.onTimeRate)})`} value={pct(k.onTimeRate)} />
        <Stat
          label={`First reply, median (baseline ${b.firstReplyMinutes == null ? '—' : `${Math.round(b.firstReplyMinutes)} min`})`}
          value={k.medianFirstReplyMinutes == null ? '—' : `${Math.round(k.medianFirstReplyMinutes)} min`}
        />
        <Stat label="P1 incidents in this stage" value={String(k.p1Incidents)} />
        <Stat label="Open incidents" value={String(k.openIncidents)} />
      </div>
      {p.history.length > 0 && (
        <details style={{ marginTop: 12 }}>
          <summary className={s.muted}>Stage decisions ({p.history.length})</summary>
          <ul className={s.stack} style={{ paddingLeft: 18, marginTop: 8 }}>
            {p.history.map((r) => (
              <li key={r.id} style={{ fontSize: 12.5 }}>
                {LABEL[r.fromStage]} → {LABEL[r.toStage]}: <strong>{r.state}</strong>
                {r.decidedBy ? ` by ${r.decidedBy.name}` : ''} {r.decidedAt ? ago(r.decidedAt) : ''}
                {r.decisionNote ? ` — “${r.decisionNote}”` : ''}
              </li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  );
}

const RANGES = [14, 30, 60] as const;

function ShadowCard() {
  const [days, setDays] = useState<(typeof RANGES)[number]>(30);
  const q = useQuery({
    queryKey: ['pilot', 'shadow', days],
    queryFn: () => api.get<ShadowReportDTO>(`/v1/pilot/shadow?days=${days}`),
  });
  return (
    <Card
      title="AI compared with your people"
      meta={
        <span role="group" aria-label="Comparison period" style={{ display: 'inline-flex', gap: 6 }}>
          {RANGES.map((d) => (
            <Chip key={d} on={days === d} onClick={() => setDays(d)}>
              {d} days
            </Chip>
          ))}
        </span>
      }
    >
      <Loadable query={q} skeleton={<Skeleton h={160} />}>
        {(r) =>
          r.compared === 0 ? (
            <EmptyState title="No triaged mail in this period" />
          ) : (
            <div className={s.stack}>
              <p className={s.muted} style={{ margin: 0 }}>
                {r.compared} mails triaged, {r.labelled} labelled by people. The final lane is a person’s
                label, or the lane after anyone moved the ticket.
              </p>
              <div className={s.two}>
                <table className={s.table} aria-label="Lanes: AI against final">
                  <thead>
                    <tr>
                      <th scope="col">AI lane</th>
                      <th scope="col">Final lane</th>
                      <th scope="col" className={s.num}>
                        Mails
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.lanes.map((l) => (
                      <tr key={`${l.ai}-${l.final}`} className={l.ai !== l.final ? s.wrong : undefined}>
                        <td>{l.ai}</td>
                        <td>{l.final}</td>
                        <td className={s.num}>{l.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <table className={s.table} aria-label="Categories: agreement with labels">
                  <thead>
                    <tr>
                      <th scope="col">Labelled category</th>
                      <th scope="col" className={s.num}>
                        Labelled
                      </th>
                      <th scope="col" className={s.num}>
                        AI agreed
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.categories.length === 0 ? (
                      <tr>
                        <td colSpan={3} className={s.muted}>
                          Label mail in Evals → a dataset → Label real mail.
                        </td>
                      </tr>
                    ) : (
                      r.categories.map((c) => (
                        <tr key={c.key}>
                          <td className="mono">{c.key}</td>
                          <td className={s.num}>{c.labelled}</td>
                          <td className={s.num}>{pct(c.agreed / c.labelled)}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
              {r.disagreements.length > 0 && (
                <table className={s.table} aria-label="Disagreements">
                  <thead>
                    <tr>
                      <th scope="col">Mail</th>
                      <th scope="col">Where they differ</th>
                      <th scope="col">AI</th>
                      <th scope="col">People</th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.disagreements.map((d) => (
                      <tr key={d.ticketId} className={d.kind === 'hard_stop_miss' ? s.wrong : undefined}>
                        <td>
                          <Link to={`/tickets?ticket=QRY-${d.number}`}>QRY-{d.number}</Link>{' '}
                          <span className={s.muted}>{d.subject}</span>
                        </td>
                        <td>
                          {d.kind === 'hard_stop_miss'
                            ? 'Hard stop missed'
                            : d.kind === 'lane'
                              ? 'Lane'
                              : 'Category'}
                        </td>
                        <td className="mono">
                          {d.kind === 'lane'
                            ? d.aiLane
                            : d.kind === 'category'
                              ? (d.aiCategory ?? 'none')
                              : 'no stop'}
                        </td>
                        <td className="mono">
                          {d.kind === 'lane'
                            ? d.finalLane
                            : d.kind === 'category'
                              ? d.humanCategory
                              : 'hard stop'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )
        }
      </Loadable>
    </Card>
  );
}

function IncidentsCard() {
  const has = useCaps();
  const q = useQuery({
    queryKey: incidentsKey,
    queryFn: () => api.get<PilotIncidentDTO[]>('/v1/pilot/incidents'),
  });
  const [open, setOpen] = useState(false);
  const empty: PilotIncidentBody = {
    severity: 'P3',
    kind: 'other',
    title: '',
    detail: '',
    ticketNumber: null,
  };
  const [form, setForm] = useState<PilotIncidentBody>(empty);
  const [resolving, setResolving] = useState<PilotIncidentDTO | null>(null);
  const [resolution, setResolution] = useState('');
  const log = useInlineAction(
    (b: PilotIncidentBody) => api.post<PilotIncidentDTO[]>('/v1/pilot/incidents', b),
    {
      invalidate: [incidentsKey, pilotKey],
      success: 'Incident logged.',
    },
  );
  const resolve = useInlineAction(
    (v: { id: string; resolution: string }) =>
      api.post<PilotIncidentDTO[]>(`/v1/pilot/incidents/${v.id}/resolve`, { resolution: v.resolution }),
    { invalidate: [incidentsKey, pilotKey], success: 'Incident closed.' },
  );
  return (
    <Card
      title="Incident log"
      meta={
        has('ticket.work') && (
          <Button size="sm" onClick={() => setOpen(true)}>
            Log an incident
          </Button>
        )
      }
    >
      <Loadable query={q} skeleton={<Skeleton h={100} />}>
        {(list) =>
          list.length === 0 ? (
            <EmptyState
              title="No incidents logged"
              text="Log anything that went wrong: a P1 raises an alert for admins."
            />
          ) : (
            <table className={s.table} aria-label="Incidents">
              <thead>
                <tr>
                  <th scope="col">Severity</th>
                  <th scope="col">What happened</th>
                  <th scope="col">Logged</th>
                  <th scope="col">State</th>
                </tr>
              </thead>
              <tbody>
                {list.map((i) => (
                  <tr key={i.id} className={i.severity === 'P1' && !i.resolvedAt ? s.wrong : undefined}>
                    <td className="mono">{i.severity}</td>
                    <td>
                      <div>{i.title}</div>
                      <div className={s.muted} style={{ fontSize: 12 }}>
                        {KIND_LABEL[i.kind]}
                        {i.ticketNumber && (
                          <>
                            {' · '}
                            <Link to={`/tickets?ticket=QRY-${i.ticketNumber}`}>QRY-{i.ticketNumber}</Link>
                          </>
                        )}
                        {i.resolution && ` · ${i.resolution}`}
                      </div>
                    </td>
                    <td>
                      {i.openedBy?.name ?? '—'} {ago(i.openedAt)}
                    </td>
                    <td>
                      {i.resolvedAt ? (
                        `Closed ${ago(i.resolvedAt)}`
                      ) : has('autonomy.change') ? (
                        <Button size="sm" onClick={() => setResolving(i)}>
                          Close
                        </Button>
                      ) : (
                        'Open'
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
      </Loadable>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Log an incident"
        width={500}
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="dark"
              loading={log.isPending}
              disabled={!form.title.trim()}
              onClick={() => log.mutate(form, { onSuccess: () => (setOpen(false), setForm(empty)) })}
            >
              Log it
            </Button>
          </>
        }
      >
        <div className={s.stack}>
          <div className={s.row}>
            <Field label="Severity">
              <Select
                aria-label="Severity"
                value={form.severity}
                onChange={(e) => setForm({ ...form, severity: e.target.value as IncidentSeverity })}
              >
                {SEVERITIES.map((x) => (
                  <option key={x}>{x}</option>
                ))}
              </Select>
            </Field>
            <Field label="Kind">
              <Select
                aria-label="Kind"
                value={form.kind}
                onChange={(e) => setForm({ ...form, kind: e.target.value as IncidentKind })}
              >
                {KINDS.map((x) => (
                  <option key={x.key} value={x.key}>
                    {x.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Ticket number (optional)">
              <Input
                aria-label="Ticket number"
                inputMode="numeric"
                value={form.ticketNumber ?? ''}
                onChange={(e) => {
                  const n = parseInt(e.target.value.replace(/\D/g, ''), 10);
                  setForm({ ...form, ticketNumber: Number.isFinite(n) ? n : null });
                }}
              />
            </Field>
          </div>
          <Field label="What happened">
            <Input
              aria-label="Title"
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
            />
          </Field>
          <Field label="Details">
            <TextArea
              aria-label="Details"
              rows={4}
              value={form.detail}
              onChange={(e) => setForm({ ...form, detail: e.target.value })}
            />
          </Field>
          <ProblemAlert error={log.error} />
        </div>
      </Modal>
      <Modal
        open={resolving !== null}
        onClose={() => setResolving(null)}
        title={`Close: ${resolving?.title ?? ''}`}
        width={460}
        footer={
          <>
            <Button variant="ghost" onClick={() => setResolving(null)}>
              Cancel
            </Button>
            <Button
              variant="dark"
              loading={resolve.isPending}
              disabled={!resolution.trim()}
              onClick={() =>
                resolving &&
                resolve.mutate(
                  { id: resolving.id, resolution: resolution.trim() },
                  { onSuccess: () => (setResolving(null), setResolution('')) },
                )
              }
            >
              Close incident
            </Button>
          </>
        }
      >
        <div className={s.stack}>
          <Field label="What was done">
            <TextArea
              aria-label="Resolution"
              rows={3}
              value={resolution}
              onChange={(e) => setResolution(e.target.value)}
            />
          </Field>
          <ProblemAlert error={resolve.error} />
        </div>
      </Modal>
    </Card>
  );
}

function SettingsCard({ p }: { p: PilotDTO }) {
  const members = useMembers();
  const [t, setT] = useState(p.targets);
  const [approvers, setApprovers] = useState<string[]>(p.riskApprovers.map((u) => u.id));
  const [days, setDays] = useState(30);
  const [manual, setManual] = useState({ onTime: '', firstReply: '' });
  const save = useInlineAction((b: PilotSettingsBody) => api.put<PilotDTO>('/v1/pilot/settings', b), {
    invalidate: [pilotKey],
    success: 'Pilot targets saved.',
  });
  const baseline = useInlineAction((b: PilotBaselineBody) => api.post<PilotDTO>('/v1/pilot/baseline', b), {
    invalidate: [pilotKey, keys.onboarding],
    success: 'Baseline recorded.',
  });
  const admins = (members.data ?? []).filter((m) => m.role === 'admin');
  const num = (key: keyof typeof t, label: string, step: number, asPct = false) => (
    <Field label={label}>
      <Input
        aria-label={label}
        type="number"
        step={step}
        value={asPct ? Math.round(t[key] * 100) : t[key]}
        onChange={(e) => setT({ ...t, [key]: asPct ? Number(e.target.value) / 100 : Number(e.target.value) })}
      />
    </Field>
  );
  return (
    <Card title="Targets, baseline and sign-off">
      <div className={s.stack}>
        <div className={s.fields}>
          {num('acceptance', 'Drafts accepted, % at least', 1, true)}
          {num('lightEditMax', 'Light edit, % changed at most', 1, true)}
          {num('agreement', 'Agreement in shadow, % at least', 1, true)}
          {num('shadowDays', 'Days in shadow, at least', 1)}
          {num('assistedDays', 'Days assisted, at least', 1)}
          {num('minLabelled', 'Mails labelled, at least', 1)}
          {num('minDrafts', 'Drafts decided, at least', 1)}
        </div>
        <fieldset className={s.fieldset}>
          <legend className={s.fieldLabel}>Risk approvers (sign off moves forward)</legend>
          <p className={s.muted} style={{ margin: '0 0 6px', fontSize: 12 }}>
            With none chosen, any admin other than the one who asked can sign off.
          </p>
          {admins.map((m) => (
            <Check
              key={m.user.id}
              checked={approvers.includes(m.user.id)}
              onChange={(v) =>
                setApprovers(v ? [...approvers, m.user.id] : approvers.filter((x) => x !== m.user.id))
              }
            >
              {m.user.name}
            </Check>
          ))}
        </fieldset>
        <div className={s.row}>
          <Button
            variant="dark"
            loading={save.isPending}
            onClick={() => save.mutate({ targets: t, riskApprovers: approvers })}
          >
            Save targets
          </Button>
        </div>
        <ProblemAlert error={save.error} />

        <span className={s.fieldLabel}>Reply-time baseline</span>
        <p className={s.muted} style={{ margin: 0, fontSize: 12 }}>
          {p.baseline.capturedAt
            ? `${pct(p.baseline.onTimeRate)} on time${
                p.baseline.firstReplyMinutes != null
                  ? `, first reply ${Math.round(p.baseline.firstReplyMinutes)} min`
                  : ''
              } — ${p.baseline.source === 'records' ? `measured over ${p.baseline.days} days` : 'entered by hand'}, ${ago(
                p.baseline.capturedAt,
              )}.`
            : 'Not recorded. Record it before shadow starts, so the pilot has something to beat.'}
        </p>
        <div className={s.row}>
          <Input
            aria-label="Days to measure"
            type="number"
            min={7}
            max={180}
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            style={{ width: 90 }}
          />
          <Button loading={baseline.isPending} onClick={() => baseline.mutate({ days })}>
            Measure from records
          </Button>
          <span className={s.muted}>or enter</span>
          <Input
            aria-label="On time, %"
            placeholder="On time %"
            value={manual.onTime}
            onChange={(e) => setManual({ ...manual, onTime: e.target.value })}
            style={{ width: 100 }}
          />
          <Input
            aria-label="First reply, minutes"
            placeholder="First reply min"
            value={manual.firstReply}
            onChange={(e) => setManual({ ...manual, firstReply: e.target.value })}
            style={{ width: 130 }}
          />
          <Button
            disabled={!manual.onTime}
            loading={baseline.isPending}
            onClick={() =>
              baseline.mutate({
                onTimeRate: Number(manual.onTime) / 100,
                ...(manual.firstReply ? { firstReplyMinutes: Number(manual.firstReply) } : {}),
              })
            }
          >
            Save figures
          </Button>
        </div>
        <ProblemAlert error={baseline.error} />
      </div>
    </Card>
  );
}
