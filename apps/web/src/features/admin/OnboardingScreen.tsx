/**
 * Getting started: the admin's landing page until go-live. Every step's state comes from the workspace's
 * real data on the server (nothing is ticked by hand), so the checklist cannot claim what is not true.
 */
import type { OnboardingDTO, OnboardingStepDTO } from '@ci/contracts';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../../lib/api';
import { keys } from '../../lib/queries';
import { Card, cx, Loadable, Page, PageHeader, Skeleton } from '../../ui';
import { isForbidden, NoAccess } from './bits';
import s from './workspace.module.css';

const STATE_WORD: Record<OnboardingStepDTO['state'], string> = {
  not_started: 'Not started',
  in_progress: 'In progress',
  done: 'Done',
  later: 'Later release',
};

export default function OnboardingScreen() {
  const q = useQuery({
    queryKey: keys.onboarding,
    queryFn: () => api.get<OnboardingDTO>('/v1/onboarding'),
  });
  return (
    <Page narrow>
      <div className="rise">
        <PageHeader
          title="Getting started"
          subtitle="Everything your workspace needs before the AI reads real customer mail. Each step checks itself."
        />
      </div>
      {isForbidden(q.error) ? (
        <NoAccess error={q.error} what="the onboarding checklist" who="Onboarding is run by admins." />
      ) : (
        <Loadable query={q} skeleton={<Skeleton h={420} />}>
          {(ob) => (
            <>
              <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>
                {ob.done} of {ob.total} steps done
              </div>
              <div
                className={s.progress}
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={ob.total}
                aria-valuenow={ob.done}
              >
                <div className={s.progressBar} style={{ width: `${(ob.done / ob.total) * 100}%` }} />
              </div>
              <Card flush>
                <ol className={s.steps}>
                  {ob.steps.map((step, i) => (
                    <Step key={step.key} n={i + 1} step={step} />
                  ))}
                </ol>
              </Card>
            </>
          )}
        </Loadable>
      )}
    </Page>
  );
}

function Step({ n, step }: { n: number; step: OnboardingStepDTO }) {
  const done = step.state === 'done';
  return (
    <li
      className={cx(s.step, step.state === 'later' && s.later)}
      aria-label={`${step.title}: ${STATE_WORD[step.state]}`}
    >
      <span
        className={cx(s.dot, done && s.dotDone, step.state === 'in_progress' && s.dotProgress)}
        aria-hidden
      >
        {done ? '✓' : n}
      </span>
      <div style={{ minWidth: 0 }}>
        <div className={s.stepTitle}>{step.title}</div>
        <div className={s.stepText}>{step.description}</div>
        <div className={s.stepDetail}>
          {STATE_WORD[step.state]}
          {step.detail ? ` · ${step.detail}` : ''}
        </div>
      </div>
      {step.to && !done && (
        <Link to={step.to} style={{ fontSize: 12.5, fontWeight: 500, whiteSpace: 'nowrap' }}>
          {step.state === 'not_started' ? 'Start' : 'Continue'} →
        </Link>
      )}
    </li>
  );
}
