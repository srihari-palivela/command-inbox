import type { StepState, TenantStatus } from '@ci/contracts';
import { cx } from '@web/ui';
import { STEP_STATE, TENANT_STATUS } from '../../lib/presentation';
import { css as s, ToneTag } from '../../ui/bits';

export const StatusTag = ({ status }: { status: TenantStatus }) => <ToneTag tone={TENANT_STATUS[status]} />;

export function StepIcon({ state }: { state: StepState }) {
  const t = STEP_STATE[state];
  return (
    <span
      className={cx(s.stepIcon, state === 'running' && s.spin)}
      style={{ color: t.fg, background: t.bg, borderColor: t.line }}
      aria-hidden
    >
      {t.icon}
    </span>
  );
}

/** Provisioning progress in words; `null` means there is nothing in flight. */
export function ProvisioningTag({ state }: { state: StepState | null }) {
  if (!state) return <span className={s.muted}>Done</span>;
  return (
    <span className={s.row} style={{ gap: 6, flexWrap: 'nowrap' }}>
      <StepIcon state={state} />
      <span style={{ color: STEP_STATE[state].fg, fontWeight: 500 }}>{STEP_STATE[state].label}</span>
    </span>
  );
}
