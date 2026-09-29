import type { ConnectorState } from '@ci/contracts';
import type { Tone } from '../../../lib/presentation';

export const CONNECTOR_TONE: Record<
  ConnectorState,
  Tone & { iconFg: string; iconBg: string; label: string }
> = {
  connected: {
    fg: 'var(--ok)',
    bg: 'var(--ok-bg-2)',
    iconFg: 'var(--accent)',
    iconBg: 'var(--accent-bg)',
    label: 'Connected',
  },
  read_only: {
    fg: 'var(--accent)',
    bg: 'var(--accent-bg-2)',
    iconFg: 'var(--muted)',
    iconBg: 'var(--surface-4)',
    label: 'Read only',
  },
  suggest_only: {
    fg: 'var(--bad-text)',
    bg: 'var(--bad-bg)',
    iconFg: 'var(--bad)',
    iconBg: 'var(--bad-bg)',
    label: 'Suggest only',
  },
  error: {
    fg: 'var(--bad-text)',
    bg: 'var(--bad-bg)',
    iconFg: 'var(--bad)',
    iconBg: 'var(--bad-bg)',
    label: 'Error',
  },
};
