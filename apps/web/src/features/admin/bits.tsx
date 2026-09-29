/** Small building blocks shared by the administration screens. */
import type { Capability } from '@ci/contracts';
import { useMutation, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { type ReactNode, type SelectHTMLAttributes } from 'react';
import { ApiError } from '../../lib/api';
import { invalidate, useMe } from '../../lib/queries';
import { toast } from '../../lib/toast';
import { Button, cx, EmptyState, Modal, Pill } from '../../ui';
import s from './admin.module.css';

export function useCaps() {
  const caps = new Set<Capability>(useMe().data?.capabilities ?? []);
  return (c: Capability) => caps.has(c);
}

export const isForbidden = (err: unknown) => err instanceof ApiError && err.status === 403;

/** A 403 explained, instead of a retry box: retrying won't help. */
export function NoAccess({ error, what, who }: { error: unknown; what: string; who: string }) {
  const detail = error instanceof ApiError ? error.problem.title : undefined;
  return (
    <EmptyState
      title={`You can’t view ${what}`}
      text={`${detail ? `${detail} ` : ''}${who} Ask an Admin if you need a change here.`}
    />
  );
}

export function Tone({
  tone,
  children,
  title,
}: {
  tone: { label: string; fg: string; bg: string; line: string };
  children?: ReactNode;
  title?: string;
}) {
  return (
    <Pill fg={tone.fg} bg={tone.bg} line={tone.line} title={title}>
      {children ?? tone.label}
    </Pill>
  );
}

export function LockIcon({ size = 11 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 12 12" fill="none" aria-hidden className={s.lock}>
      <rect x="2" y="5.2" width="8" height="5.6" rx="1.3" stroke="currentColor" strokeWidth="1.2" />
      <path d="M4 5.2V3.8a2 2 0 0 1 4 0v1.4" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  );
}

/** A server problem shown where it happened: its title, and its detail when there is one. */
export function ProblemAlert({ error, children }: { error: unknown; children?: ReactNode }) {
  if (!error) return null;
  const title =
    error instanceof ApiError ? error.problem.title : error instanceof Error ? error.message : String(error);
  const detail = error instanceof ApiError ? error.problem.detail : undefined;
  return (
    <div className={s.alert} role="alert">
      <div className={s.alertTitle}>{title}</div>
      {detail && <div className={s.alertText}>{detail}</div>}
      {children}
    </div>
  );
}

export function Notice({ tone = 'info', children }: { tone?: 'info' | 'warn' | 'ok'; children: ReactNode }) {
  return (
    <div className={cx(s.notice, tone === 'warn' && s.noticeWarn, tone === 'ok' && s.noticeOk)}>
      {children}
    </div>
  );
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cx(s.select, props.className)} />;
}

/** A labelled checkbox (native, so it is keyboard- and screen-reader-friendly as is). */
export function Check({
  checked,
  onChange,
  children,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  children: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label className={cx(s.check, disabled && s.checkOff)}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>{children}</span>
    </label>
  );
}

/** Ask before a change that is hard to take back; server errors stay in the dialog. */
export function Confirm({
  open,
  title,
  children,
  confirmLabel,
  danger,
  onConfirm,
  onClose,
  pending,
  error,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onClose: () => void;
  pending?: boolean;
  error?: unknown;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      width={460}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant={danger ? 'danger' : 'dark'} onClick={onConfirm} loading={pending}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className={s.stack}>
        <div className={s.body}>{children}</div>
        <ProblemAlert error={error} />
      </div>
    </Modal>
  );
}

/**
 * Like `useAction`, but the server's problem stays on the mutation (`.error`) for the screen to show next
 * to the control that caused it, instead of a toast that disappears.
 */
export function useInlineAction<TVars, TResult = unknown>(
  fn: (vars: TVars) => Promise<TResult>,
  opts: { invalidate?: QueryKey[]; success?: string | ((r: TResult, v: TVars) => string | null) } = {},
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onError: () => undefined,
    onSuccess: (r, v) => {
      invalidate(qc, opts.invalidate ?? []);
      const msg = typeof opts.success === 'function' ? opts.success(r, v) : opts.success;
      if (msg) toast.show(msg);
    },
  });
}
