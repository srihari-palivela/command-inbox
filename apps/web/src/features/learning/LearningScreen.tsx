import { useUi } from '../../app/ui-context';
import { useLearning, useMe } from '../../lib/queries';
import { Button, EmptyState, Loadable, Meter, Page, PageHeader, Pill, Skeleton } from '../../ui';
import { completionTone, courseMeta } from './course';
import s from './Learning.module.css';

export default function LearningScreen() {
  const q = useLearning();
  const me = useMe().data;
  const ui = useUi();
  const canSend = !!me?.capabilities.includes('learning.send');

  return (
    <Page narrow>
      <PageHeader
        title="Learning"
        subtitle="Short card decks with a quiz at the end — how process and policy changes reach the team."
        actions={
          canSend && (
            <Button variant="dark" onClick={ui.openCompose}>
              + Send update
            </Button>
          )
        }
      />
      <Loadable
        query={q}
        skeleton={
          <div className={s.list}>
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} h={64} />
            ))}
          </div>
        }
      >
        {(l) =>
          l.courses.length === 0 ? (
            <EmptyState
              title="No courses yet"
              text="When a process or policy changes, a short card deck with a quiz shows up here."
            />
          ) : (
            <div className={s.list}>
              {l.courses.map((c, i) => {
                const tone = completionTone(c.teamCompletionPct);
                return (
                  <article key={c.id} className={s.course} style={{ animationDelay: `${i * 0.05}s` }}>
                    <div className={s.info}>
                      <div className={s.titleRow}>
                        <h2 className={s.title}>{c.title}</h2>
                        {c.completedByMe && (
                          <Pill fg="var(--ok)" bg="var(--ok-bg)">
                            ✓ Completed
                          </Pill>
                        )}
                      </div>
                      <div className={s.meta}>{courseMeta(c)}</div>
                    </div>
                    <div className={s.progress}>
                      <div className={s.progressHead}>
                        <span>team completion</span>
                        <span className={s.pct} style={{ color: tone }}>
                          {c.teamCompletionPct}% of team
                        </span>
                      </div>
                      <Meter
                        pct={c.teamCompletionPct}
                        color={tone}
                        label={`${c.title}: ${c.teamCompletionPct}% of the team completed`}
                        delay={i * 0.05}
                      />
                    </div>
                    <Button
                      variant={c.completedByMe ? 'secondary' : 'primary'}
                      className={s.go}
                      onClick={() => ui.openLearn(c.id)}
                      aria-label={`${c.completedByMe ? 'Retake' : 'Start'} ${c.title}`}
                    >
                      {c.completedByMe ? 'Retake' : 'Start'}
                    </Button>
                  </article>
                );
              })}
            </div>
          )
        }
      </Loadable>
    </Page>
  );
}
