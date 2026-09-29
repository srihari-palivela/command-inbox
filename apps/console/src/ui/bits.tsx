/** Console building blocks on top of the web UI kit (`@web/ui`). */
import { cx, Pill } from '@web/ui';
import type { ReactNode, SelectHTMLAttributes } from 'react';
import { ApiError } from '../lib/api';
import type { Tone } from '../lib/presentation';
import s from './console.module.css';

export { s as css };

export function ToneTag({ tone, children, title }: { tone: Tone; children?: ReactNode; title?: string }) {
  return (
    <Pill fg={tone.fg} bg={tone.bg} line={tone.line} title={title}>
      {children ?? tone.label}
    </Pill>
  );
}

/** A server problem shown where it happened: its title, and its detail when there is one. */
export function ProblemAlert({ error }: { error: unknown }) {
  if (!error) return null;
  const title =
    error instanceof ApiError ? error.problem.title : error instanceof Error ? error.message : String(error);
  const detail = error instanceof ApiError ? error.problem.detail : undefined;
  return (
    <div className={s.alert} role="alert">
      <div className={s.alertTitle}>{title}</div>
      {detail && detail !== title && <div className={s.alertText}>{detail}</div>}
    </div>
  );
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cx(s.select, props.className)} />;
}

/** A labelled form field with an optional hint and error, wired up for screen readers. */
export function FormField({
  id,
  label,
  hint,
  error,
  wide,
  children,
}: {
  id: string;
  label: string;
  hint?: ReactNode;
  error?: string;
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={cx(s.field, wide && s.wide)}>
      <label htmlFor={id} className={s.fieldLabel}>
        {label}
      </label>
      {children}
      {error ? (
        <span id={`${id}-error`} className={s.fieldError}>
          {error}
        </span>
      ) : (
        hint && (
          <span id={`${id}-hint`} className={s.fieldHint}>
            {hint}
          </span>
        )
      )}
    </div>
  );
}
