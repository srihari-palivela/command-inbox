/** Shown instead of a retry box when the server refuses a setup screen (403): retrying won't help. */
import { ApiError } from '../../../lib/api';
import { EmptyState } from '../../../ui';

export const isForbidden = (err: unknown) => err instanceof ApiError && err.status === 403;

export function NoAccess({ error, what }: { error: unknown; what: string }) {
  const detail = error instanceof ApiError ? error.problem.title : undefined;
  return (
    <EmptyState
      title={`You can’t view ${what}`}
      text={
        detail ?? 'Setup screens are for Admin · Risk & Compliance. Ask an Admin if you need a change here.'
      }
    />
  );
}
