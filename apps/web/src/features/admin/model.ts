/**
 * Pure helpers for the tenant-administration screens: the deployment config shape the editor works on,
 * the server's `invalid_config` detail, and how metrics, gates and states read.
 */
import type {
  Capability,
  DeploymentVersionState,
  EvalGateDTO,
  EvalRunState,
  InvitationState,
  Role,
} from '@ci/contracts';

// ── Deployment config (DeploymentConfig in apps/api/src/command_inbox/agents/config.py) ──────────────
export type Lane = 'auto' | 'draft' | 'manual';
export type Sensitivity = 'standard' | 'restricted' | 'secret';

export interface CategoryConfig {
  key: string;
  name: string;
  description?: string;
  examples?: string[];
  department: string;
  defaultLane?: Lane;
  actionTemplate?: string | null;
  sensitivity?: Sensitivity;
}

export interface HardStopConfig {
  key: string;
  label: string;
  keywords?: string[];
  question?: string | null;
  threshold?: number | string;
}

/** Only the parts the structured editor touches are typed; everything else round-trips untouched. */
export interface DeploymentConfig {
  taxonomy: { categories: CategoryConfig[]; fallback?: string };
  rules?: { hardStops?: HardStopConfig[]; [k: string]: unknown };
  thresholds?: Record<string, number | string>;
  gates?: Record<string, number | string>;
  [k: string]: unknown;
}

export const asConfig = (raw: Record<string, unknown>): DeploymentConfig =>
  structuredClone(raw) as unknown as DeploymentConfig;

export interface NumberSpec {
  key: string;
  label: string;
  hint: string;
  min: number;
  max: number;
  step: number;
}

export const THRESHOLDS: NumberSpec[] = [
  {
    key: 'autoMinConfidence',
    label: 'Auto lane — minimum confidence',
    hint: 'The AI acts on its own only at or above this. 0.5–1.',
    min: 0.5,
    max: 1,
    step: 0.01,
  },
  {
    key: 'draftMinConfidence',
    label: 'Draft lane — minimum confidence',
    hint: 'Below this a person handles the mail. 0.3–1, never above the auto bar.',
    min: 0.3,
    max: 1,
    step: 0.01,
  },
  {
    key: 'escalateBelow',
    label: 'Escalate to System 2 below',
    hint: 'System 1 answers under this go to the adjudicator. 0–1.',
    min: 0,
    max: 1,
    step: 0.01,
  },
  {
    key: 'conformalCoverage',
    label: 'Conformal coverage target',
    hint: 'How often the prediction set must contain the right label. 0.8–0.995.',
    min: 0.8,
    max: 0.995,
    step: 0.005,
  },
];

export const GATES: NumberSpec[] = [
  {
    key: 'hardStopRecall',
    label: 'Hard-stop recall at least',
    hint: 'A missed regulator, fraud or vulnerability signal is the worst failure. 0.9–1.',
    min: 0.9,
    max: 1,
    step: 0.01,
  },
  { key: 'accuracy', label: 'Category accuracy at least', hint: '0–1.', min: 0, max: 1, step: 0.01 },
  {
    key: 'macroF1',
    label: 'Macro-F1 at least',
    hint: 'Quality across rare categories. 0–1.',
    min: 0,
    max: 1,
    step: 0.01,
  },
  {
    key: 'eceMax',
    label: 'Expected calibration error at most',
    hint: 'Confidence must mean what it says. 0–0.5.',
    min: 0,
    max: 0.5,
    step: 0.01,
  },
  {
    key: 'selectiveAccuracy',
    label: 'Selective accuracy at least',
    hint: 'What System 1 accepts must be right. 0–1.',
    min: 0,
    max: 1,
    step: 0.01,
  },
  {
    key: 'minCoverage',
    label: 'System 1 coverage at least',
    hint: 'Share of mail System 1 decides without escalating. 0–1.',
    min: 0,
    max: 1,
    step: 0.01,
  },
];

/** A number input's text as the config should hold it: a number when it parses, else the raw text. */
export const numberOrRaw = (text: string): number | string => {
  const t = text.trim();
  return t !== '' && Number.isFinite(Number(t)) ? Number(t) : t;
};

export const listFromText = (text: string): string[] =>
  text
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean);

// ── Server problems ───────────────────────────────────────────────────────────
export interface ConfigError {
  path: string;
  message: string;
}

/**
 * `invalid_config` names each failing path: "thresholds.autoMinConfidence: Input should be …; taxonomy: …".
 * A part without a path (a whole-document rule) comes back with an empty path.
 */
export function parseConfigErrors(detail: string | undefined): ConfigError[] {
  if (!detail) return [];
  return detail
    .split('; ')
    .map((part) => {
      const m = /^([A-Za-z0-9_.]+): (.+)$/s.exec(part.trim());
      return m ? { path: m[1]!, message: m[2]! } : { path: '', message: part.trim() };
    })
    .filter((e) => e.message);
}

/** The errors that belong to a field: its exact path, or a rule on its section (e.g. `thresholds`). */
export const errorsFor = (errors: ConfigError[], path: string) => errors.filter((e) => e.path === path);
export const errorsUnder = (errors: ConfigError[], prefix: string) =>
  errors.filter((e) => e.path === prefix || e.path.startsWith(`${prefix}.`));

// ── Presentation ──────────────────────────────────────────────────────────────
interface Tone {
  label: string;
  fg: string;
  bg: string;
  line: string;
}

export const VERSION_STATE: Record<DeploymentVersionState, Tone> = {
  draft: { label: 'Draft', fg: 'var(--text-2)', bg: 'var(--surface-3)', line: 'var(--line)' },
  shadow: { label: 'Shadow', fg: 'var(--accent)', bg: 'var(--accent-bg)', line: 'var(--accent-line)' },
  canary: { label: 'Canary', fg: 'var(--warn)', bg: 'var(--warn-bg)', line: 'var(--warn-line)' },
  published: { label: 'Published', fg: 'var(--ok)', bg: 'var(--ok-bg)', line: 'var(--ok-line)' },
  retired: { label: 'Retired', fg: 'var(--muted)', bg: 'var(--surface-4)', line: 'var(--line-soft)' },
};

export const RUN_STATE: Record<EvalRunState, Tone> = {
  queued: { label: 'Queued', fg: 'var(--text-2)', bg: 'var(--surface-3)', line: 'var(--line)' },
  running: { label: 'Running', fg: 'var(--accent)', bg: 'var(--accent-bg)', line: 'var(--accent-line)' },
  passed: { label: 'Passed', fg: 'var(--ok)', bg: 'var(--ok-bg)', line: 'var(--ok-line)' },
  failed: { label: 'Failed', fg: 'var(--bad-text)', bg: 'var(--bad-bg)', line: 'var(--bad-line)' },
  error: { label: 'Error', fg: 'var(--bad-text)', bg: 'var(--bad-bg)', line: 'var(--bad-line)' },
};

export const INVITATION_STATE: Record<InvitationState, Tone> = {
  pending: { label: 'Pending', fg: 'var(--accent)', bg: 'var(--accent-bg)', line: 'var(--accent-line)' },
  accepted: { label: 'Accepted', fg: 'var(--ok)', bg: 'var(--ok-bg)', line: 'var(--ok-line)' },
  revoked: { label: 'Revoked', fg: 'var(--muted)', bg: 'var(--surface-4)', line: 'var(--line-soft)' },
  expired: { label: 'Expired', fg: 'var(--warn)', bg: 'var(--warn-bg)', line: 'var(--warn-line)' },
};

export const ROLES: Role[] = ['staff', 'lead', 'admin'];

const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

/** A metric as people read it: shares as percentages, calibration error as a decimal, counts as counts. */
export function formatMetric(metric: string, v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  if (metric === 'laneSafetyViolations') return String(Math.round(v));
  if (metric === 'ece' || metric === 'eceUncalibrated') return v.toFixed(3);
  return pct(v);
}

export function gateLine(g: EvalGateDTO): string {
  return `${g.op === 'gte' ? '≥' : '≤'} ${formatMetric(g.metric, g.threshold)}`;
}

export const shortHash = (h: string) => (h ? h.slice(0, 8) : '—');

/** Why a permission cell cannot be changed by this tenant. */
export function lockReason(capability: Capability, adminOnly: boolean): string {
  if (capability === 'action.approve_checker')
    return 'Separation of duties: staff can never counter-approve and team leads always can.';
  if (adminOnly) return 'Admin only. A tenant cannot hand this to staff or team leads.';
  return 'Fixed by product policy for every role.';
}

export const slugify = (name: string) =>
  name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^[^a-z]+/, '')
    .replace(/_+$/, '')
    .slice(0, 48);
