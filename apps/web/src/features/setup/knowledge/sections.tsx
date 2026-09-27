import type { GapDTO, KnowledgeDTO, KnowledgeSourceDTO } from '@ci/contracts';
import { api } from '../../../lib/api';
import { keys, useAction } from '../../../lib/queries';
import { Button, Card, EmptyState, Meter } from '../../../ui';
import s from './Knowledge.module.css';
import { GAP_TONE, HEALTH_TONE, readinessTone } from './tones';

const NO_EDIT = 'Only an Admin can change knowledge sources and gap tickets.';

const messageToast = (r: { message: string }) => r.message;

export function SourcesCard({ sources, canEdit }: { sources: KnowledgeSourceDTO[]; canEdit: boolean }) {
  const sync = useAction((id: string) => api.post<{ message: string }>(`/v1/knowledge/sources/${id}/sync`), {
    invalidate: [keys.knowledge],
    success: messageToast,
  });
  return (
    <Card
      title="Connected sources"
      actions={
        <span className={s.gapHint}>
          Watched for changes — an edited document drops back to pending until re-approved
        </span>
      }
      flush
      className={s.rise}
    >
      {sources.length === 0 ? (
        <EmptyState
          title="No sources connected"
          text="Connect SharePoint, Confluence, a drive or an upload folder. Nothing is citable until it is approved."
        />
      ) : (
        <div className={s.sources}>
          {sources.map((k, i) => {
            const t = HEALTH_TONE[k.health];
            const bad = k.health === 'bad';
            return (
              <div key={k.id} className={s.source} style={{ animationDelay: `${i * 0.05}s` }}>
                <div className={s.sourceHead}>
                  <span className={s.abbr} aria-hidden>
                    {k.abbr}
                  </span>
                  <div style={{ minWidth: 0 }}>
                    <div className={s.sourceName} title={k.name}>
                      {k.name}
                    </div>
                    <div className={s.sourceKind}>{k.kind}</div>
                  </div>
                  <span className={s.health} style={{ color: t.fg, background: t.bg }}>
                    <span className={s.healthDot} style={{ background: t.dot }} aria-hidden />
                    {t.label}
                  </span>
                </div>
                <div className={s.docs}>
                  {k.docs} · {k.approved}
                </div>
                <div className={s.sync} style={{ color: t.fg }}>
                  {k.sync}
                </div>
                <div className={s.note}>{k.note}</div>
                <Button
                  size="sm"
                  className={s.smallBtn}
                  disabled={!canEdit}
                  title={canEdit ? undefined : NO_EDIT}
                  loading={sync.isPending && sync.variables === k.id}
                  onClick={() => sync.mutate(k.id)}
                >
                  {bad ? 'Fix auth' : 'Sync now'}
                </Button>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

export function ReadinessCard({ readiness }: { readiness: KnowledgeDTO['readiness'] }) {
  return (
    <Card className={s.rise} bodyClassName={s.plainBody} style={{ animationDelay: '.05s' }}>
      <h3 className={s.plainTitle} style={{ marginBottom: 13 }}>
        Readiness by department
      </h3>
      <div className={s.readiness}>
        {readiness.map((r, i) => {
          const c = readinessTone(r.pct);
          return (
            <div key={r.department}>
              <div className={s.readRow}>
                <span className={s.readName}>{r.department}</span>
                <span className={s.readPct} style={{ color: c }}>
                  {r.pct}%
                </span>
              </div>
              <Meter pct={r.pct} color={c} height={6} label={`${r.department} readiness`} delay={i * 0.06} />
              <div className={s.readNote}>{r.note}</div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

export function HonestCard({ honest }: { honest: KnowledgeDTO['honest'] }) {
  const rows = [
    {
      n: `${honest.quotablePct}%`,
      color: 'var(--accent)',
      text: `of all query types have content the AI can safely quote today. The remaining ${100 - honest.quotablePct}% is why some queries still come straight to a person.`,
    },
    {
      n: String(honest.ungroundedSentences),
      color: honest.ungroundedSentences === 0 ? 'var(--ok)' : 'var(--bad)',
      text:
        honest.ungroundedSentences === 0
          ? 'customer-facing sentences have ever been generated from un-approved sources. The agent flags instead of filling.'
          : 'sent replies cite content that is not approved. Review them now — this should never happen.',
    },
    {
      n: String(honest.openGaps),
      color: 'var(--warn)',
      text: 'open gap tickets, each traceable to the specific queries it is blocking and a named owner.',
    },
  ];
  return (
    <Card className={s.rise} bodyClassName={s.plainBody} style={{ animationDelay: '.1s' }}>
      <h3 className={s.plainTitle} style={{ marginBottom: 4 }}>
        The honest position
      </h3>
      <p className={s.lede}>
        Knowledge readiness, not model quality, is what limits how much the AI can draft. The agent will not
        fill a gap with recall.
      </p>
      <div className={s.honest}>
        {rows.map((h) => (
          <div key={h.text} className={s.honestRow}>
            <span className={s.honestN} style={{ color: h.color }}>
              {h.n}
            </span>
            <span className={s.honestText}>{h.text}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

export function GapsCard({ gaps, open, canEdit }: { gaps: GapDTO[]; open: number; canEdit: boolean }) {
  const act = useAction((id: string) => api.post<{ message: string }>(`/v1/knowledge/gaps/${id}/act`), {
    invalidate: [keys.knowledge, keys.me],
    success: messageToast,
  });
  return (
    <Card
      title="Gap tickets"
      meta={<span className={s.gapCount}>{open} open</span>}
      actions={
        <span className={s.gapHint}>Raised automatically when the agent hits an answer it cannot ground</span>
      }
      flush
      className={s.rise}
      style={{ animationDelay: '.14s' }}
    >
      {gaps.length === 0 ? (
        <EmptyState
          title="No gaps"
          text="Every query type the agent has met is grounded in approved content."
        />
      ) : (
        gaps.map((g, i) => {
          const t = GAP_TONE[g.severity];
          return (
            <article key={g.id} className={s.gap} style={{ animationDelay: `${i * 0.05}s` }}>
              <div className={s.gapTop}>
                <span className={s.gapNum}>{g.number}</span>
                <span className={s.sev} style={{ color: t.fg, background: t.bg }}>
                  {t.label}
                </span>
                <span className={s.gapMeta}>
                  <span className="mono">{g.hits}</span> queries blocked · {g.age}
                </span>
              </div>
              <div className={s.gapQ}>{g.question}</div>
              <div className={s.gapDetail}>{g.detail}</div>
              <div className={s.gapFoot}>
                <span>
                  Owner <b>{g.owner}</b>
                </span>
                <span className={s.sep} aria-hidden />
                <span>{g.state}</span>
                <Button
                  size="sm"
                  variant="soft"
                  className={s.cta}
                  disabled={!canEdit}
                  title={canEdit ? undefined : NO_EDIT}
                  loading={act.isPending && act.variables === g.id}
                  onClick={() => act.mutate(g.id)}
                >
                  {g.cta}
                </Button>
              </div>
            </article>
          );
        })
      )}
    </Card>
  );
}
