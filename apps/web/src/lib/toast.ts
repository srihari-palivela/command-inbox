/** Minimal toast store: screens call toast.show(); <Toaster/> renders the latest. */
export interface ToastItem {
  id: number;
  message: string;
  tone: 'info' | 'error';
  action?: { label: string; run: () => void };
}

type Listener = (t: ToastItem | null) => void;
const listeners = new Set<Listener>();
let current: ToastItem | null = null;
let timer: ReturnType<typeof setTimeout> | undefined;
let seq = 0;

function emit(t: ToastItem | null) {
  current = t;
  for (const l of listeners) l(t);
}

export const toast = {
  show(message: string, action?: ToastItem['action']) {
    clearTimeout(timer);
    emit({ id: ++seq, message, tone: 'info', action });
    timer = setTimeout(() => emit(null), action ? 6000 : 3200);
  },
  error(message: string) {
    clearTimeout(timer);
    emit({ id: ++seq, message, tone: 'error' });
    timer = setTimeout(() => emit(null), 5000);
  },
  dismiss() {
    clearTimeout(timer);
    emit(null);
  },
  subscribe(l: Listener) {
    listeners.add(l);
    l(current);
    return () => listeners.delete(l);
  },
};
