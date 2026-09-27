import type { MeDTO } from '@ci/contracts';

/** Personal preferences (Settings screen) with their defaults. */
export const PREF_DEFAULTS = {
  digest: true,
  breach: true,
  mentions: true,
  quiet: false,
  autoAdvance: true,
  keyboard: true,
  dense: false,
  stream: true,
  signature: true,
} as const;

export type PrefKey = keyof typeof PREF_DEFAULTS;

export function pref(me: MeDTO | null | undefined, key: PrefKey): boolean {
  const v = me?.settings.prefs[key];
  return v === undefined ? PREF_DEFAULTS[key] : v;
}
