import type { GapSeverity, Health } from '@ci/contracts';
import type { Tone } from '../../../lib/presentation';

/** Source health: word + colours (the prototype's Healthy / Watch / Broken). */
export const HEALTH_TONE: Record<Health, Tone & { dot: string; label: string }> = {
  ok: { fg: 'var(--ok)', bg: 'var(--ok-bg-2)', dot: 'var(--ok-dot)', label: 'Healthy' },
  warn: { fg: 'var(--warn)', bg: 'var(--warn-bg)', dot: 'var(--warn-dot)', label: 'Watch' },
  bad: { fg: 'var(--bad-text)', bg: 'var(--bad-bg)', dot: 'var(--bad)', label: 'Broken' },
};

export const GAP_TONE: Record<GapSeverity, Tone & { label: string }> = {
  blocking: { fg: 'var(--bad)', bg: 'var(--bad-bg)', label: 'BLOCKING' },
  stale: { fg: 'var(--warn)', bg: 'var(--warn-bg)', label: 'STALE' },
  unowned: { fg: 'var(--warn)', bg: 'var(--warn-bg)', label: 'UNOWNED' },
  resolved: { fg: 'var(--ok)', bg: 'var(--ok-bg-2)', label: 'RESOLVED' },
};

/** Readiness bar colour: ≥75 ready, ≥55 getting there, below that a blocker. */
export const readinessTone = (pct: number) =>
  pct >= 75 ? 'var(--ok)' : pct >= 55 ? 'var(--accent)' : 'var(--warn)';

export const SOURCE_KINDS = [
  { kind: 'SharePoint', abbr: 'SP', name: 'SharePoint', note: 'OAuth · watch chosen libraries for changes' },
  { kind: 'Confluence', abbr: 'CF', name: 'Confluence', note: 'OAuth · spaces and page trees' },
  { kind: 'Google Drive', abbr: 'GD', name: 'Google Drive', note: 'OAuth · folders of sheets and docs' },
  { kind: 'S3', abbr: 'S3', name: 'S3 / object store', note: 'Key pair · bucket prefix, read-only' },
  { kind: 'Upload', abbr: 'UP', name: 'Direct upload', note: 'PDF, DOCX, XLSX — versioned on re-upload' },
] as const;
