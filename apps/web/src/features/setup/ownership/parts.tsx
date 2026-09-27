import type { TaxonomyDTO } from '@ci/contracts';
import { api } from '../../../lib/api';
import { LANE_TONE } from '../../../lib/presentation';
import { keys, useAction } from '../../../lib/queries';
import { Button, Card, cx } from '../../../ui';
import s from './Ownership.module.css';

type Dept = TaxonomyDTO['departments'][number];

export function DepartmentColumn({ d, index, canEdit }: { d: Dept; index: number; canEdit: boolean }) {
  const change = useAction(
    (v: { id: string; name: string }) =>
      api.put<{ owner: string }>(`/v1/taxonomy/departments/${v.id}/owner`, {}),
    {
      invalidate: [keys.taxonomy],
      success: (r, v) => `${v.name} now owned by ${r.owner} — cleared to approve here.`,
    },
  );
  return (
    <section className={s.col} style={{ animationDelay: `${index * 0.06}s` }} aria-label={d.name}>
      <header className={cx(s.head, d.tone === 'risk' && s.headRisk)}>
        <div style={{ minWidth: 0 }}>
          <h3 className={s.dept}>{d.name}</h3>
          <div className={s.owner}>
            {d.owner ? d.owner.name : 'No owner'} · <span className="mono">{d.queryTypes.length}</span> query
            types
          </div>
        </div>
        <Button
          size="sm"
          className={s.change}
          disabled={!canEdit}
          title={
            canEdit
              ? 'Hand ownership to the next person with approve clearance for this team'
              : 'Only an Admin can change who owns a query type.'
          }
          loading={change.isPending}
          onClick={() => change.mutate({ id: d.id, name: d.name })}
        >
          Change owner
        </Button>
      </header>
      {d.queryTypes.map((q) => {
        const lane = LANE_TONE[q.lane];
        return (
          <div key={q.id} className={s.item}>
            <div className={s.itemTop}>
              <span className={s.lane} style={{ color: lane.fg, background: lane.bg }}>
                {lane.word}
              </span>
              <span className={s.name}>{q.name}</span>
            </div>
            <div className={s.itemMeta}>
              <span className={s.vol}>{q.volume}/mo</span>
              <span style={{ color: q.live ? 'var(--ok)' : 'var(--warn)' }}>
                {q.live ? 'live' : 'blocked'}
              </span>
            </div>
          </div>
        );
      })}
      <div className={s.spacer} />
      {d.gapNote && (
        <div className={s.gapNote}>
          <span className={s.gapDot} aria-hidden />
          <span>{d.gapNote}</span>
        </div>
      )}
    </section>
  );
}

const CONTRACT_LOOK = [
  { icon: '✓', icon_bg: 'var(--ok)', bg: 'var(--ok-bg-3)', line: 'var(--ok-line)' },
  { icon: '=', icon_bg: 'var(--accent)', bg: 'var(--accent-bg-4)', line: 'var(--accent-line)' },
  { icon: '×', icon_bg: 'var(--bad)', bg: 'var(--bad-bg-2)', line: 'var(--bad-line)' },
  { icon: 'i', icon_bg: 'var(--muted)', bg: 'var(--surface-2)', line: 'var(--line)' },
];

export function ContractCard({ contract }: { contract: TaxonomyDTO['contract'] }) {
  return (
    <Card className={s.rise} bodyClassName={s.plainBody} style={{ marginTop: 13, animationDelay: '.2s' }}>
      <div className={s.contractHead}>
        <h3 className={s.plainTitle}>Accountability contract</h3>
        <span className={s.owner}>what the platform will and will not claim</span>
      </div>
      <div className={s.contract}>
        {contract.map((c, i) => {
          const look = CONTRACT_LOOK[i % CONTRACT_LOOK.length]!;
          return (
            <div key={c.title} className={s.tile} style={{ background: look.bg, borderColor: look.line }}>
              <span className={s.icon} style={{ background: look.icon_bg }} aria-hidden>
                {look.icon}
              </span>
              <div>
                <div className={s.tileTitle}>{c.title}</div>
                <div className={s.tileText}>{c.text}</div>
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
