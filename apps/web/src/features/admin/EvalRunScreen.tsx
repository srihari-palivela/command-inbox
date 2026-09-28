/**
 * One eval run: the verdict, each gate against its threshold, the metrics, and every case's result. The
 * page follows the run live (`eval.updated` on the event stream) while it is queued or running.
 */
import type { EvalResultDTO, EvalRunDTO, EvalSplit } from '@ci/contracts';
import { LANE_WORD } from '@ci/contracts';
import { useMemo } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { clockTime } from '../../lib/format';
import { useDeployment, useEvalCases, useEvalResults, useEvalRun } from '../../lib/queries';
import { Card, Chip, cx, EmptyState, Loadable, Page, PageHeader, Skeleton } from '../../ui';
import { isForbidden, NoAccess, Tone } from './bits';
import { asConfig, formatMetric, gateLine, RUN_STATE, shortHash } from './model';
import s from './admin.module.css';

export default function EvalRunScreen() {
  const { runId } = useParams();
  const q = useEvalRun(runId);
  return (
    <Page>
      <nav className={s.crumbs} aria-label="Breadcrumb">
        <Link to="/admin/evals">Evals</Link>
        <span aria-hidden>/</span>
        <span>Run</span>
      </nav>
      {isForbidden(q.error) ? (
        <>
          <PageHeader title="Eval run" />
          <NoAccess error={q.error} what="evals" who="Evals are for team leads and admins." />
        </>
      ) : (
        <Loadable query={q} skeleton={<Skeleton h={300} />}>
          {(r) => <Run r={r} />}
        </Loadable>
      )}
    </Page>
  );
}

const done = (r: EvalRunDTO) => r.state === 'passed' || r.state === 'failed';

function Run({ r }: { r: EvalRunDTO }) {
  const failed = r.gates.filter((g) => !g.passed);
  const dep = useDeployment(r.deploymentId);
  const version = dep.data?.versions.find((v) => v.id === r.deploymentVersionId);
  const names = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of version ? (asConfig(version.config).taxonomy?.categories ?? []) : [])
      m.set(c.key, c.name);
    return m;
  }, [version]);

  return (
    <>
      <div className="rise">
        <PageHeader
          title={`Eval run — v${r.version} on ${r.datasetName}`}
          subtitle={
            <>
              {dep.data ? (
                <Link to={`/admin/deployments/${r.deploymentId}?version=${r.deploymentVersionId}`}>
                  {dep.data.name} v{r.version}
                </Link>
              ) : (
                `v${r.version}`
              )}
              {' · '}started {clockTime(r.createdAt)}
              {r.createdBy ? ` by ${r.createdBy.name}` : ''} · engine <span className="mono">{r.engine}</span>
            </>
          }
          actions={<Tone tone={RUN_STATE[r.state]} />}
        />
      </div>

      <div
        className={cx(
          s.verdict,
          r.state === 'passed' && s.verdictPass,
          (r.state === 'failed' || r.state === 'error') && s.verdictFail,
        )}
        aria-live="polite"
      >
        {r.state === 'queued' || r.state === 'running' ? (
          <span>
            {r.state === 'queued' ? 'Queued' : 'Running'} — {r.split.test} test and {r.split.calibration}{' '}
            calibration cases. This page updates when the run finishes.
          </span>
        ) : r.state === 'error' ? (
          <span>The run could not finish: {r.error ?? 'unknown error'}.</span>
        ) : r.state === 'passed' ? (
          <span>
            <b>Passed every gate.</b>{' '}
            {r.current
              ? 'It counts for publishing this exact configuration.'
              : 'The version was edited after this run, so it no longer counts for publishing.'}
          </span>
        ) : (
          <span>
            <b>
              Failed {failed.length} of {r.gates.length} gates.
            </b>{' '}
            The version cannot take real mail on this run.
          </span>
        )}
      </div>

      {r.gates.length > 0 && (
        <Card className={s.card} title="Gates" meta="each must pass" flush>
          <table className={s.table} aria-label="Gates">
            <thead>
              <tr>
                <th scope="col">Gate</th>
                <th scope="col" className={s.num}>
                  Result
                </th>
                <th scope="col" className={s.num}>
                  Threshold
                </th>
                <th scope="col">Verdict</th>
              </tr>
            </thead>
            <tbody>
              {r.gates.map((g) => (
                <tr key={g.key}>
                  <td className={s.name}>{g.label}</td>
                  <td className={s.num}>{formatMetric(g.metric, g.value)}</td>
                  <td className={s.num}>{gateLine(g)}</td>
                  <td>
                    <span className={g.passed ? s.pass : s.fail}>{g.passed ? '✓ Pass' : '✕ Fail'}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {r.metrics && <Metrics r={r} />}

      <dl className={s.meta} style={{ marginBottom: 13 }}>
        <dt>Config hash</dt>
        <dd className={s.mono}>
          {r.configHash}{' '}
          {!r.current && <span className={s.muted}>(outdated — the version changed since)</span>}
        </dd>
        <dt>Dataset snapshot</dt>
        <dd className={s.mono}>{shortHash(r.datasetSnapshot)}</dd>
        {r.finishedAt && (
          <>
            <dt>Finished</dt>
            <dd>{clockTime(r.finishedAt)}</dd>
          </>
        )}
      </dl>

      {done(r) && <Results r={r} names={names} />}
    </>
  );
}

function Metrics({ r }: { r: EvalRunDTO }) {
  const m = r.metrics!;
  const items: [string, string][] = [
    ['Hard-stop recall', formatMetric('hardStopRecall', m.hardStopRecall)],
    ['Category accuracy', formatMetric('accuracy', m.accuracy)],
    ['Macro-F1', formatMetric('macroF1', m.macroF1)],
    [
      'Calibration error (ECE)',
      `${formatMetric('ece', m.ece)}${m.eceUncalibrated !== null && m.eceUncalibrated !== m.ece ? ` (was ${formatMetric('ece', m.eceUncalibrated)})` : ''}`,
    ],
    ['Selective accuracy', formatMetric('selectiveAccuracy', m.selectiveAccuracy)],
    ['System 1 coverage', formatMetric('coverage', m.coverage)],
    ['Conformal coverage', formatMetric('conformalCoverage', m.conformalCoverage)],
    ['Auto lane on hard stops', formatMetric('laneSafetyViolations', m.laneSafetyViolations)],
    ['Escalated to System 2', formatMetric('escalationRate', m.escalationRate)],
    ['p95 latency', m.p95LatencyMs === null ? '—' : `${m.p95LatencyMs.toFixed(1)} ms`],
    ['Temperature', m.temperature.toFixed(2)],
    ['Cases (test / calibration)', `${m.cases} / ${m.calibrationCases}`],
  ];
  return (
    <Card className={s.card} title="Metrics" meta="scored on the test split">
      <div className={s.metrics}>
        {items.map(([label, value]) => (
          <div key={label} className={s.metric}>
            <div className={s.metricVal}>{value}</div>
            <div className={s.metricLbl}>{label}</div>
          </div>
        ))}
      </div>
    </Card>
  );
}

function Results({ r, names }: { r: EvalRunDTO; names: Map<string, string> }) {
  const results = useEvalResults(r.id);
  // Results carry the case id only; the dataset's live cases give the subject (archived ones don't).
  const cases = useEvalCases(r.datasetId);
  const subjects = useMemo(
    () => new Map((cases.data ?? []).map((c) => [c.id, c.input.subject])),
    [cases.data],
  );
  const [params, setParams] = useSearchParams();
  const wrongOnly = params.get('wrong') === '1';
  const hardOnly = params.get('hard') === '1';
  const split = (params.get('split') as EvalSplit | null) ?? null;
  const flip = (k: string, on: boolean, v = '1') =>
    setParams(
      (p) => {
        if (on) p.set(k, v);
        else p.delete(k);
        return p;
      },
      { replace: true },
    );
  const label = (k: string) => names.get(k) ?? k;

  return (
    <Card className={s.card} title="Cases" flush>
      <Loadable query={results} skeleton={<Skeleton h={160} style={{ margin: 12 }} />}>
        {(all) => {
          const wrong = all.filter((x) => !x.correct).length;
          const list = all.filter(
            (x: EvalResultDTO) =>
              (!wrongOnly || !x.correct) &&
              (!hardOnly || x.hardStopExpected) &&
              (!split || x.split === split),
          );
          return (
            <>
              <div
                className={s.filters}
                style={{ padding: '10px 12px' }}
                role="group"
                aria-label="Filter cases"
              >
                <Chip on={wrongOnly} count={wrong} onClick={() => flip('wrong', !wrongOnly)}>
                  Wrong only
                </Chip>
                <Chip
                  on={hardOnly}
                  count={all.filter((x) => x.hardStopExpected).length}
                  onClick={() => flip('hard', !hardOnly)}
                >
                  Hard-stop cases
                </Chip>
                <Chip on={split === 'test'} onClick={() => flip('split', split !== 'test', 'test')}>
                  Test split
                </Chip>
                <Chip
                  on={split === 'calibration'}
                  onClick={() => flip('split', split !== 'calibration', 'calibration')}
                >
                  Calibration split
                </Chip>
                <span className={`${s.muted} ${s.push}`}>
                  {list.length} of {all.length} shown
                </span>
              </div>
              {list.length === 0 ? (
                <EmptyState
                  title="No cases match"
                  text={wrongOnly ? 'Every case was categorised correctly.' : undefined}
                />
              ) : (
                <div className={s.scroll}>
                  <table className={s.table} aria-label="Case results">
                    <thead>
                      <tr>
                        <th scope="col">Case</th>
                        <th scope="col">Expected</th>
                        <th scope="col">Predicted</th>
                        <th scope="col" className={s.num}>
                          Confidence
                        </th>
                        <th scope="col">Hard stop (expected / caught)</th>
                        <th scope="col">Lane</th>
                        <th scope="col">Verdict</th>
                      </tr>
                    </thead>
                    <tbody>
                      {list.map((x) => (
                        <tr key={x.id} className={cx(!x.correct && s.wrong)}>
                          <td style={{ maxWidth: 360 }}>
                            <div>
                              {subjects.get(x.caseId) ?? <span className={s.muted}>(archived case)</span>}
                            </div>
                            <div className={s.sub}>{x.split === 'test' ? 'test' : 'calibration'}</div>
                          </td>
                          <td>{label(x.expected)}</td>
                          <td>
                            {label(x.predicted)}
                            {x.escalated && <div className={s.sub}>escalated to System 2</div>}
                          </td>
                          <td className={s.num}>{(x.confidence * 100).toFixed(0)}%</td>
                          <td>
                            {x.hardStopExpected ? 'Yes' : 'No'} / {x.hardStopPredicted ? 'Yes' : 'No'}
                            {x.hardStopExpected && !x.hardStopPredicted && (
                              <div className={s.fail} style={{ fontSize: 11 }}>
                                missed
                              </div>
                            )}
                          </td>
                          <td>{LANE_WORD[x.lane]}</td>
                          <td>
                            <span className={x.correct ? s.pass : s.fail}>
                              {x.correct ? 'Right' : 'Wrong'}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          );
        }}
      </Loadable>
    </Card>
  );
}
