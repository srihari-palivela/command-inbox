import type { KpiBody, KpiMetric, PerformanceDTO } from '@ci/contracts';
import { useEffect, useId, useState } from 'react';
import { api } from '../../lib/api';
import { keys, useAction } from '../../lib/queries';
import { Button, Input, Modal, cx } from '../../ui';
import { fmtNum } from './bits';
import s from './KpiModal.module.css';

/** Direction hint for the target field; the server decides on-track/off-track. */
const LOWER_IS_BETTER: KpiMetric[] = ['fr', 'tat', 'reopen', 'cost', 'awo'];

const VIZ: { key: KpiBody['viz']; label: string }[] = [
  { key: 'bars', label: 'Trend bars' },
  { key: 'number', label: 'Big number' },
];
const SCOPE: { key: KpiBody['scope']; label: string }[] = [
  { key: 'team', label: 'Team dashboard' },
  { key: 'me', label: 'Just me' },
];

const blank = {
  name: '',
  metric: 'accept' as KpiMetric,
  viz: 'bars' as KpiBody['viz'],
  scope: 'team' as KpiBody['scope'],
  target: '',
};

export function KpiModal({
  open,
  onClose,
  metrics,
}: {
  open: boolean;
  onClose: () => void;
  metrics: PerformanceDTO['metrics'];
}) {
  const [f, setF] = useState(blank);
  const [err, setErr] = useState<string | null>(null);
  const ids = { name: useId(), target: useId(), measure: useId(), viz: useId(), scope: useId() };
  useEffect(() => {
    if (open) {
      setF(blank);
      setErr(null);
    }
  }, [open]);

  const create = useAction((b: KpiBody) => api.post('/v1/kpis', b), {
    invalidate: [keys.performance],
    success: (_r, b) =>
      `${b.name} is live on ${b.scope === 'team' ? 'the team dashboard' : 'your dashboard'} — alerts fire when it goes off track.`,
  });

  const lower = LOWER_IS_BETTER.includes(f.metric);
  const submit = () => {
    const name = f.name.trim();
    if (!name) return setErr('Name the KPI first.');
    const target = Number.parseFloat(f.target);
    if (!Number.isFinite(target)) return setErr('Set a numeric target.');
    setErr(null);
    create.mutate({ name, metric: f.metric, viz: f.viz, scope: f.scope, target }, { onSuccess: onClose });
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      width={560}
      title="New KPI"
      subtitle="Pick a measure, set a target, choose whose dashboard it lives on. It is monitored from the moment you create it."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="dark" style={{ marginLeft: 'auto' }} onClick={submit} loading={create.isPending}>
            Create &amp; monitor
          </Button>
        </>
      }
    >
      <form
        className={s.form}
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <div>
          <label htmlFor={ids.name} className={s.label}>
            Name
          </label>
          <Input
            id={ids.name}
            data-autofocus
            value={f.name}
            maxLength={80}
            placeholder="Draft acceptance — retail pod"
            onChange={(e) => setF({ ...f, name: e.target.value })}
          />
        </div>
        <div>
          <div id={ids.measure} className={s.label}>
            Measure
          </div>
          <div role="radiogroup" aria-labelledby={ids.measure} className={s.metrics}>
            {metrics.map((m) => (
              <button
                key={m.key}
                type="button"
                role="radio"
                aria-checked={f.metric === m.key}
                className={cx(s.metric, f.metric === m.key && s.metricOn)}
                onClick={() => setF({ ...f, metric: m.key })}
              >
                <span className={s.metricLabel}>{m.label}</span>
                <span className={cx('mono', s.metricCur)}>now {fmtNum(m.current)}</span>
              </button>
            ))}
          </div>
        </div>
        <div className={s.row3}>
          <div>
            <label htmlFor={ids.target} className={s.label}>
              Target <span className={s.hint}>({lower ? 'at most' : 'at least'})</span>
            </label>
            <div className={s.targetWrap}>
              <span className={cx('mono', s.op)} aria-hidden>
                {lower ? '≤' : '≥'}
              </span>
              <Input
                id={ids.target}
                className={cx('mono', s.target)}
                inputMode="decimal"
                value={f.target}
                placeholder="80"
                onChange={(e) => setF({ ...f, target: e.target.value })}
              />
            </div>
          </div>
          <Choice
            id={ids.viz}
            label="Shown as"
            items={VIZ}
            value={f.viz}
            onChange={(viz) => setF({ ...f, viz })}
          />
          <Choice
            id={ids.scope}
            label="Lives on"
            items={SCOPE}
            value={f.scope}
            onChange={(scope) => setF({ ...f, scope })}
          />
        </div>
        {err && (
          <div role="alert" className={s.err}>
            {err}
          </div>
        )}
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}

function Choice<K extends string>({
  id,
  label,
  items,
  value,
  onChange,
}: {
  id: string;
  label: string;
  items: { key: K; label: string }[];
  value: K;
  onChange: (k: K) => void;
}) {
  return (
    <div>
      <div id={id} className={s.label}>
        {label}
      </div>
      <div role="radiogroup" aria-labelledby={id} className={s.choice}>
        {items.map((i) => (
          <button
            key={i.key}
            type="button"
            role="radio"
            aria-checked={value === i.key}
            className={cx(s.choiceBtn, value === i.key && s.choiceOn)}
            onClick={() => onChange(i.key)}
          >
            {i.label}
          </button>
        ))}
      </div>
    </div>
  );
}
