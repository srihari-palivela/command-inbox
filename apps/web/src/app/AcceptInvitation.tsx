import type { InvitationPreviewDTO, MeDTO } from '@ci/contracts';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError, setCsrfToken } from '../lib/api';
import { keys } from '../lib/queries';
import { Button, Skeleton } from '../ui';

const ROLE_WORD: Record<string, string> = {
  admin: 'an admin',
  lead: 'a team lead',
  staff: 'a member of staff',
};

/**
 * The link in an invitation email. Shows who invited whom to which workspace, then signs the invitee in:
 * through their organisation's SSO (the invited email must match), or directly in development.
 */
export function AcceptInvitation() {
  const token = new URLSearchParams(window.location.search).get('token') ?? '';
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [redirecting, setRedirecting] = useState(false);
  const preview = useQuery({
    queryKey: ['invitation', token],
    queryFn: () => api.get<InvitationPreviewDTO>(`/v1/auth/invitation?token=${encodeURIComponent(token)}`),
    enabled: token.length >= 20,
    retry: false,
  });
  const accept = useMutation({
    mutationFn: () => api.post<MeDTO>('/v1/auth/invitation/accept', { token }),
    onSuccess: (me) => {
      setCsrfToken(me.csrfToken);
      qc.clear();
      qc.setQueryData(keys.me, me);
      navigate('/', { replace: true });
    },
  });

  const setup = useMutation({
    mutationFn: () => api.post<{ message: string }>('/v1/auth/invitation/setup', { token }),
  });

  const go = (p: InvitationPreviewDTO) => {
    if (p.signIn === 'direct') return accept.mutate();
    const params = new URLSearchParams({ invite: token, login_hint: p.email, next: '/' });
    setRedirecting(true);
    window.location.assign(`/v1/auth/oidc/login?${params.toString()}`);
  };

  const p = preview.data;
  const failure =
    token.length < 20 || (preview.error instanceof ApiError && preview.error.status === 404)
      ? 'This invitation link is not valid. Check that you opened the whole link from the email.'
      : preview.error
        ? 'The invitation could not be loaded. Try again in a moment.'
        : accept.error instanceof ApiError
          ? accept.error.problem.title
          : setup.error instanceof ApiError
            ? setup.error.problem.title
            : null;

  return (
    <main
      style={{
        minHeight: '100vh',
        display: 'grid',
        placeItems: 'center',
        padding: 24,
        background: 'var(--surface-2, var(--surface))',
      }}
    >
      <section
        aria-label="Invitation"
        className="rise"
        style={{
          width: '100%',
          maxWidth: 440,
          background: 'var(--surface)',
          border: '1px solid var(--line)',
          borderRadius: 14,
          padding: '30px 30px 26px',
        }}
      >
        <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 14 }}>Command Inbox</div>
        {preview.isLoading ? (
          <div style={{ display: 'grid', gap: 10 }}>
            <Skeleton h={26} w="70%" />
            <Skeleton h={14} />
            <Skeleton h={40} />
          </div>
        ) : p && p.state === 'pending' ? (
          <>
            <h1 style={{ margin: '0 0 8px', fontSize: 22, fontWeight: 600, letterSpacing: '-0.02em' }}>
              Join {p.tenantName}
            </h1>
            <p style={{ margin: '0 0 18px', fontSize: 13.5, color: 'var(--muted)', lineHeight: 1.55 }}>
              {p.invitedBy || 'Your administrator'} invited <strong>{p.email}</strong> to join as{' '}
              {ROLE_WORD[p.role] ?? p.role}.{' '}
              {p.signIn === 'sso'
                ? 'You will sign in with your organisation’s account for that address.'
                : 'This development workspace signs you in directly.'}
            </p>
            {failure && <Alert text={failure} />}
            <Button
              variant="dark"
              size="lg"
              style={{ width: '100%' }}
              loading={accept.isPending || redirecting}
              onClick={() => go(p)}
            >
              Accept and sign in
            </Button>
            {p.canBootstrap && (
              <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--line-faint)' }}>
                <p style={{ margin: '0 0 10px', fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.5 }}>
                  First time here, and your organisation’s single sign-on is not connected yet? Set up a
                  sign-in with a password and an authenticator app. You will get an email with the link.
                </p>
                {setup.data ? (
                  <div role="status" style={{ fontSize: 12.5, color: 'var(--ok)' }}>
                    {setup.data.message}
                  </div>
                ) : (
                  <Button variant="secondary" loading={setup.isPending} onClick={() => setup.mutate()}>
                    Set up my sign-in
                  </Button>
                )}
              </div>
            )}
            <p style={{ margin: '14px 0 0', fontSize: 11.5, color: 'var(--muted)' }}>
              The invitation expires {new Date(p.expiresAt).toLocaleString()}.
            </p>
          </>
        ) : p ? (
          <>
            <h1 style={{ margin: '0 0 8px', fontSize: 20, fontWeight: 600 }}>
              This invitation was {p.state}
            </h1>
            <p style={{ margin: '0 0 18px', fontSize: 13.5, color: 'var(--muted)', lineHeight: 1.55 }}>
              {p.state === 'accepted'
                ? 'It has already been used. Sign in to continue.'
                : `Ask ${p.invitedBy || 'your administrator'} to send you a new one.`}
            </p>
            <Button variant="secondary" onClick={() => navigate('/', { replace: true })}>
              Go to sign-in
            </Button>
          </>
        ) : (
          <Alert text={failure ?? 'This invitation link is not valid.'} />
        )}
      </section>
    </main>
  );
}

function Alert({ text }: { text: string }) {
  return (
    <div
      role="alert"
      style={{
        fontSize: 12.5,
        color: 'var(--bad-text)',
        background: 'var(--bad-bg-2)',
        border: '1px solid var(--bad-line)',
        borderRadius: 8,
        padding: '8px 10px',
        marginBottom: 12,
      }}
    >
      {text}
    </div>
  );
}
