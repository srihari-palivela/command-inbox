/**
 * UI primitives. Screens compose these; they carry the design language (metrics, colours, motion) so
 * feature code stays about the product, not about pixels.
 */
import {
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type CSSProperties,
  type InputHTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
  type TextareaHTMLAttributes,
} from 'react';
import { createPortal } from 'react-dom';
import { ApiError } from '../lib/api';
import { toast, type ToastItem } from '../lib/toast';
import s from './ui.module.css';

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(' ');
export { cx };

// ── Button ────────────────────────────────────────────────────────────────────
export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'dark' | 'secondary' | 'ghost' | 'danger' | 'soft';
  size?: 'sm' | 'md' | 'lg';
  loading?: boolean;
  kbd?: string;
  icon?: ReactNode;
}

export function Button({ variant = 'secondary', size = 'md', loading, kbd, icon, className, children, disabled, ...rest }: ButtonProps) {
  return (
    <button {...rest} disabled={disabled || loading} aria-busy={loading || undefined} className={cx(s.btn, s[size], s[variant], className)}>
      {loading ? <span className={s.spinner} aria-hidden /> : icon}
      {children}
      {kbd && <span className={s.kbd} aria-hidden>{kbd}</span>}
    </button>
  );
}

// ── Pill & chip ───────────────────────────────────────────────────────────────
export interface PillProps {
  fg: string;
  bg: string;
  line?: string;
  mono?: boolean;
  large?: boolean;
  children: ReactNode;
  title?: string;
  className?: string;
  style?: CSSProperties;
}

export function Pill({ fg, bg, line, mono, large, children, title, className, style }: PillProps) {
  return (
    <span
      title={title}
      className={cx(s.pill, mono && s.pillMono, large && s.pillLg, className)}
      style={{ color: fg, background: bg, borderColor: line ?? 'transparent', ...style }}
    >
      {children}
    </span>
  );
}

export function Chip({ on, count, children, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { on?: boolean; count?: number | string }) {
  return (
    <button type="button" aria-pressed={on} {...rest} className={cx(s.chip, on && s.chipOn, rest.className)}>
      {children}
      {count !== undefined && count !== '' && <span className={s.chipCount}>{count}</span>}
    </button>
  );
}

// ── Card ──────────────────────────────────────────────────────────────────────
export function Card({ title, meta, actions, children, className, bodyClassName, style, flush, id }: {
  title?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  bodyClassName?: string;
  style?: CSSProperties;
  flush?: boolean;
  id?: string;
}) {
  return (
    <section className={cx(s.card, className)} style={style} id={id}>
      {(title || actions || meta) && (
        <header className={s.cardHead}>
          {title && <h3 className={s.cardTitle}>{title}</h3>}
          {meta && <span className={s.cardMeta}>{meta}</span>}
          {actions && <div className={s.cardActions}>{actions}</div>}
        </header>
      )}
      {flush ? children : <div className={cx(s.cardBody, bodyClassName)}>{children}</div>}
    </section>
  );
}

export function Eyebrow({ children, style, className }: { children: ReactNode; style?: CSSProperties; className?: string }) {
  return (
    <div className={cx(s.eyebrow, className)} style={style}>
      {children}
    </div>
  );
}

// ── Tabs (WAI-ARIA tablist with arrow-key navigation) ─────────────────────────
export interface TabItem<K extends string> {
  key: K;
  label: ReactNode;
  badge?: string | number;
}

export function Tabs<K extends string>({ items, value, onChange, label, className }: { items: TabItem<K>[]; value: K; onChange: (k: K) => void; label: string; className?: string }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: KeyboardEvent, i: number) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    e.preventDefault();
    const next = (i + (e.key === 'ArrowRight' ? 1 : items.length - 1)) % items.length;
    onChange(items[next]!.key);
    refs.current[next]?.focus();
  };
  return (
    <div role="tablist" aria-label={label} className={cx(s.tabs, className)}>
      {items.map((t, i) => (
        <button
          key={t.key}
          ref={(el) => {
            refs.current[i] = el;
          }}
          role="tab"
          type="button"
          aria-selected={t.key === value}
          tabIndex={t.key === value ? 0 : -1}
          className={s.tab}
          onClick={() => onChange(t.key)}
          onKeyDown={(e) => onKey(e, i)}
        >
          {t.label}
          {t.badge !== undefined && t.badge !== '' && <span className={s.tabBadge}>{t.badge}</span>}
        </button>
      ))}
    </div>
  );
}

export function Segmented<K extends string>({ items, value, onChange, label }: { items: { key: K; label: ReactNode }[]; value: K; onChange: (k: K) => void; label: string }) {
  return (
    <div className={s.seg} role="group" aria-label={label}>
      {items.map((i) => (
        <button key={i.key} type="button" aria-pressed={i.key === value} className={s.segBtn} onClick={() => onChange(i.key)}>
          {i.label}
        </button>
      ))}
    </div>
  );
}

export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} className={s.toggle} onClick={() => onChange(!checked)}>
      <span className={s.knob} />
    </button>
  );
}

// ── Small visuals ─────────────────────────────────────────────────────────────
export function Avatar({ initials, size = 26, fg = 'var(--text-2)', bg = '#f0efeb', title, ring }: { initials: string; size?: number; fg?: string; bg?: string; title?: string; ring?: string }) {
  return (
    <span className={s.avatar} title={title} aria-label={title} style={{ width: size, height: size, fontSize: Math.max(9, size * 0.38), color: fg, background: bg, border: ring ? `1px solid ${ring}` : undefined }}>
      {initials}
    </span>
  );
}

export function Dot({ color, pulse, size = 7 }: { color: string; pulse?: boolean; size?: number }) {
  return <span className={s.dot} aria-hidden style={{ background: color, width: size, height: size, animation: pulse ? 'breathe 1.8s ease-in-out infinite' : undefined }} />;
}

export function Meter({ pct, color, height = 5, label, delay }: { pct: number; color: string; height?: number; label?: string; delay?: number }) {
  const v = Math.max(0, Math.min(100, pct));
  return (
    <div className={s.meter} style={{ height }} role="meter" aria-valuenow={Math.round(v)} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
      <div className={s.meterFill} style={{ width: `${v}%`, background: color, animationDelay: delay ? `${delay}s` : undefined }} />
    </div>
  );
}

export function Spark({ values, color, height = 34, label }: { values: number[]; color: string; height?: number; label?: string }) {
  const max = Math.max(...values, 0.0001);
  return (
    <div className={s.spark} style={{ height }} role="img" aria-label={label ?? `Trend: ${values.join(', ')}`}>
      {values.map((v, i) => (
        <div key={i} className={s.sparkBar} style={{ height: `${Math.max(8, (v / max) * 100)}%`, background: color, animationDelay: `${i * 0.03}s` }} />
      ))}
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="mono" style={{ fontSize: 10, color: 'var(--muted-2)', border: '1px solid var(--line)', borderRadius: 4, padding: '1px 5px', background: 'var(--surface)' }}>
      {children}
    </kbd>
  );
}

// ── Forms ─────────────────────────────────────────────────────────────────────
export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className={s.field}>
      <span className={s.fieldLabel}>{label}</span>
      {children}
      {hint && <span className={s.fieldLabel}>{hint}</span>}
    </label>
  );
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(s.input, props.className)} />;
}

export function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cx(s.input, s.textarea, props.className)} />;
}

// ── Overlays ──────────────────────────────────────────────────────────────────
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Focus trap + Escape + focus restore, shared by Modal and Drawer. */
function useDialog(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    const el = ref.current;
    const first = el?.querySelector<HTMLElement>('[data-autofocus]') ?? el?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== 'Tab' || !el) return;
      const nodes = Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!nodes.length) return;
      const firstN = nodes[0]!;
      const lastN = nodes[nodes.length - 1]!;
      if (e.shiftKey && document.activeElement === firstN) {
        e.preventDefault();
        lastN.focus();
      } else if (!e.shiftKey && document.activeElement === lastN) {
        e.preventDefault();
        firstN.focus();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      prev?.focus?.();
    };
  }, [open, onClose]);
  return ref;
}

export function Modal({ open, onClose, title, subtitle, children, footer, width, top, labelledBy }: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
  top?: boolean;
  labelledBy?: string;
}) {
  const ref = useDialog(open, onClose);
  const id = useId();
  if (!open) return null;
  return createPortal(
    <>
      <div className={s.scrim} onClick={onClose} aria-hidden />
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={labelledBy ?? (title ? id : undefined)} className={cx(s.modal, top && s.modalTop)} style={width ? { width: `min(${width}px, calc(100vw - 32px))` } : undefined}>
        {title && (
          <div className={s.modalHead}>
            <div style={{ minWidth: 0 }}>
              <h2 id={id} className={s.modalTitle}>
                {title}
              </h2>
              {subtitle && <p className={s.modalSub}>{subtitle}</p>}
            </div>
            <button type="button" className={s.close} onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
        )}
        <div className={title ? s.modalBody : undefined}>{children}</div>
        {footer && <div className={s.modalFoot}>{footer}</div>}
      </div>
    </>,
    document.body,
  );
}

export function Drawer({ open, onClose, title, subtitle, children, footer, width }: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
}) {
  const ref = useDialog(open, onClose);
  const id = useId();
  if (!open) return null;
  return createPortal(
    <>
      <div className={s.scrim} onClick={onClose} aria-hidden />
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={id} className={s.drawer} style={width ? { width: `min(${width}px, 100vw)` } : undefined}>
        <div className={s.modalHead}>
          <div style={{ minWidth: 0 }}>
            <h2 id={id} className={s.modalTitle}>
              {title}
            </h2>
            {subtitle && <div className={s.modalSub}>{subtitle}</div>}
          </div>
          <button type="button" className={s.close} onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className={s.drawerBody}>{children}</div>
        {footer && <div className={s.modalFoot}>{footer}</div>}
      </div>
    </>,
    document.body,
  );
}

/** Anchored popover that closes on outside click and Escape. Position with `style`. */
export function Popover({ open, onClose, children, style, className, label }: { open: boolean; onClose: () => void; children: ReactNode; style?: CSSProperties; className?: string; label?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: globalThis.KeyboardEvent) => e.key === 'Escape' && onClose();
    const t = setTimeout(() => document.addEventListener('mousedown', onDown), 0);
    document.addEventListener('keydown', onKey);
    return () => {
      clearTimeout(t);
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div ref={ref} role="dialog" aria-label={label} className={cx(s.popover, className)} style={style}>
      {children}
    </div>
  );
}

export function MenuItem({ children, onClick, danger }: { children: ReactNode; onClick: () => void; danger?: boolean }) {
  return (
    <button type="button" role="menuitem" className={s.menuItem} onClick={onClick} style={danger ? { color: 'var(--bad-text)' } : undefined}>
      {children}
    </button>
  );
}

export function Toaster() {
  const [t, setT] = useState<ToastItem | null>(null);
  useEffect(() => {
    const unsub = toast.subscribe(setT);
    return () => {
      unsub();
    };
  }, []);
  return (
    <div aria-live="polite" role="status">
      {t && (
        <div key={t.id} className={cx(s.toast, t.tone === 'error' && s.toastError)}>
          <span className={s.toastDot} aria-hidden />
          <span>{t.message}</span>
          {t.action && (
            <button
              type="button"
              className={s.toastAction}
              onClick={() => {
                t.action!.run();
                toast.dismiss();
              }}
            >
              {t.action.label}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ── States & page scaffolding ────────────────────────────────────────────────
export function EmptyState({ title, text, action }: { title: string; text?: ReactNode; action?: ReactNode }) {
  return (
    <div className={s.empty}>
      <div className={s.emptyTitle}>{title}</div>
      {text && <div className={s.emptyText}>{text}</div>}
      {action}
    </div>
  );
}

export function Skeleton({ h = 14, w = '100%', style }: { h?: number; w?: number | string; style?: CSSProperties }) {
  return <div className={s.skel} style={{ height: h, width: w, ...style }} aria-hidden />;
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  // Permission and not-found answers won't change on retry: say so plainly instead of offering "Try again".
  if (error instanceof ApiError && (error.status === 403 || error.status === 404)) {
    return (
      <EmptyState
        title={error.status === 403 ? 'Not available for your role' : 'Not found'}
        text={error.problem.detail ?? error.problem.title}
      />
    );
  }
  const msg = error instanceof Error ? error.message : 'Something went wrong.';
  return (
    <div className={s.errorBox} role="alert">
      <span style={{ flex: 1 }}>{msg}</span>
      {onRetry && (
        <Button size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}

export function Page({ children, narrow }: { children: ReactNode; narrow?: boolean }) {
  return (
    <div className={s.page}>
      <div className={narrow ? s.pageNarrow : s.pageInner}>{children}</div>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className={s.pageHead}>
      <div style={{ minWidth: 0 }}>
        <h1 className={s.pageTitle}>{title}</h1>
        {subtitle && <p className={s.pageSub}>{subtitle}</p>}
      </div>
      {actions && <div className={s.pageActions}>{actions}</div>}
    </div>
  );
}

/** Loading / error / data switch for query results. */
export function Loadable<T>({ query, skeleton, children }: { query: { data: T | undefined; isLoading: boolean; error: unknown; refetch: () => unknown }; skeleton?: ReactNode; children: (data: T) => ReactNode }) {
  if (query.error) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  if (query.isLoading || query.data === undefined)
    return (
      <>
        {skeleton ?? (
          <div style={{ display: 'grid', gap: 10 }}>
            <Skeleton h={22} w="40%" />
            <Skeleton h={120} />
            <Skeleton h={120} />
          </div>
        )}
      </>
    );
  return <>{children(query.data)}</>;
}
