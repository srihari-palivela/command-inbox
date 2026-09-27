import type { ConnectorState, MailboxState } from '@ci/contracts';
import type { Tone } from '../../../lib/presentation';

export const MAILBOX_TONE: Record<MailboxState, Tone & { dot: string; label: string }> = {
  streaming: { fg: 'var(--ok)', bg: 'var(--ok-bg-2)', line: 'var(--ok-line)', dot: 'var(--ok-dot)', label: 'Streaming' },
  triage_only: { fg: 'var(--accent)', bg: 'var(--accent-bg-2)', line: 'var(--accent-line)', dot: 'var(--accent)', label: 'Triage only' },
  observe: { fg: 'var(--muted)', bg: 'var(--surface-4)', line: 'var(--line)', dot: 'var(--dot-idle)', label: 'Observe' },
  connecting: { fg: 'var(--warn)', bg: 'var(--warn-bg)', line: 'var(--warn-line)', dot: 'var(--warn-dot)', label: 'Connecting' },
  error: { fg: 'var(--bad-text)', bg: 'var(--bad-bg)', line: 'var(--bad-line)', dot: 'var(--bad)', label: 'Error' },
};

export const CONNECTOR_TONE: Record<ConnectorState, Tone & { iconFg: string; iconBg: string; label: string }> = {
  connected: { fg: 'var(--ok)', bg: 'var(--ok-bg-2)', iconFg: 'var(--accent)', iconBg: 'var(--accent-bg)', label: 'Connected' },
  read_only: { fg: 'var(--accent)', bg: 'var(--accent-bg-2)', iconFg: 'var(--muted)', iconBg: 'var(--surface-4)', label: 'Read only' },
  suggest_only: { fg: 'var(--bad-text)', bg: 'var(--bad-bg)', iconFg: 'var(--bad)', iconBg: 'var(--bad-bg)', label: 'Suggest only' },
  error: { fg: 'var(--bad-text)', bg: 'var(--bad-bg)', iconFg: 'var(--bad)', iconBg: 'var(--bad-bg)', label: 'Error' },
};
