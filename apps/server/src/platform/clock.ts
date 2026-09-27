/** Injectable clock so time-dependent rules (SLA, undo windows) are testable. */
let offsetMs = 0;
export const clock = {
  now: (): Date => new Date(Date.now() + offsetMs),
  /** Tests only. */
  advance(ms: number) {
    offsetMs += ms;
  },
  reset() {
    offsetMs = 0;
  },
};
