/**
 * Which System 2 model providers this workspace's agreements allow, and the monthly model budget. The
 * installation decides which providers can be called at all; the bank narrows that to what it has agreed.
 */
import type { ModelPolicyBody, ModelPolicyDTO, ModelProviderKey } from '@ci/contracts';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../lib/api';
import { minorExponent, money, num } from '../../lib/format';
import { keys } from '../../lib/queries';
import { Button, Card, Field, Input, Meter, Skeleton } from '../../ui';
import { Check, Notice, ProblemAlert, useInlineAction } from './bits';
import s from './workspace.module.css';

export function ModelPolicyCard() {
  const q = useQuery({
    queryKey: keys.modelPolicy,
    queryFn: () => api.get<ModelPolicyDTO>('/v1/workspace/model-policy'),
  });
  if (!q.data) return q.isPending ? <Skeleton h={220} /> : <ProblemAlert error={q.error} />;
  return <PolicyForm key={JSON.stringify(q.data)} policy={q.data} />;
}

function PolicyForm({ policy }: { policy: ModelPolicyDTO }) {
  const exp = minorExponent();
  const [allowed, setAllowed] = useState<Set<ModelProviderKey>>(
    new Set(policy.providers.filter((p) => p.allowed).map((p) => p.key)),
  );
  const [budget, setBudget] = useState(
    policy.monthlyBudgetMinor == null ? '' : String(policy.monthlyBudgetMinor / 10 ** exp),
  );
  const save = useInlineAction(
    (body: ModelPolicyBody) => api.put<ModelPolicyDTO>('/v1/workspace/model-policy', body),
    { invalidate: [keys.modelPolicy], success: 'Model policy saved.' },
  );
  const cap = budget.trim() === '' ? null : Math.round(Number(budget) * 10 ** exp);
  const badBudget = cap !== null && (!Number.isFinite(cap) || cap < 0);
  const toggle = (k: ModelProviderKey, on: boolean) => {
    const next = new Set(allowed);
    if (on) next.add(k);
    else next.delete(k);
    setAllowed(next);
  };
  const used = policy.monthlyBudgetMinor ? Math.min(1, policy.spentMinor / policy.monthlyBudgetMinor) : 0;
  const month = new Date(policy.month + 'T00:00:00Z').toLocaleDateString(undefined, {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  });

  return (
    <Card title="AI model providers">
      <div style={{ display: 'grid', gap: 12 }}>
        <p className={s.lede} style={{ margin: 0 }}>
          Drafting, summaries and second opinions use a large language model. Allow only the providers your
          bank has agreements with (data processing, retention, residency). Classification always runs on the
          model hosted in your own environment.
        </p>
        <table className={s.providers} aria-label="Model providers">
          <thead>
            <tr>
              <th scope="col">Provider</th>
              <th scope="col">Default model</th>
              <th scope="col">This month</th>
              <th scope="col">Allowed</th>
            </tr>
          </thead>
          <tbody>
            {policy.providers.map((p) => (
              <tr key={p.key}>
                <td>
                  <strong>{p.name}</strong>
                  <div className={s.subtle}>
                    {p.configured ? (p.isDefault ? 'Installed · the default' : 'Installed') : 'Not installed'}
                  </div>
                </td>
                <td className="mono">{p.defaultModel}</td>
                <td>
                  {money(p.spentMinor)} · {num(p.calls)} calls
                </td>
                <td>
                  <Check
                    checked={allowed.has(p.key)}
                    disabled={!policy.canEdit}
                    onChange={(on) => toggle(p.key, on)}
                  >
                    {allowed.has(p.key) ? 'Allowed' : 'Not allowed'}
                  </Check>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <Field
          label="Monthly budget"
          hint="Leave blank for no cap. Once it is spent, drafting stops and new mail goes to people until the month ends."
        >
          <Input
            inputMode="decimal"
            value={budget}
            disabled={!policy.canEdit}
            aria-invalid={badBudget}
            onChange={(e) => setBudget(e.target.value)}
            style={{ maxWidth: 220 }}
          />
        </Field>
        <div>
          <div className={s.subtle} style={{ marginBottom: 5 }}>
            {month}: {money(policy.spentMinor)} spent
            {policy.monthlyBudgetMinor != null && ` of ${money(policy.monthlyBudgetMinor)}`}
          </div>
          {policy.monthlyBudgetMinor != null && (
            <Meter
              pct={used * 100}
              label="Share of the monthly budget spent"
              color={policy.budgetReached ? 'var(--bad)' : 'var(--accent)'}
            />
          )}
        </div>
        {policy.budgetReached && (
          <Notice tone="warn">The budget for this month is spent: new mail is handled by people only.</Notice>
        )}
        <ProblemAlert error={save.error} />
        {policy.canEdit && (
          <div>
            <Button
              variant="dark"
              disabled={badBudget}
              loading={save.isPending}
              onClick={() => save.mutate({ allowedProviders: [...allowed].sort(), monthlyBudgetMinor: cap })}
            >
              Save model policy
            </Button>
          </div>
        )}
      </div>
    </Card>
  );
}
