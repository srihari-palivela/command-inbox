import { Button } from '@web/ui';
import { useState } from 'react';
import { ApiError } from '../lib/api';
import { initials, OPERATOR_ROLE } from '../lib/presentation';
import { useAuthConfig, useDevLogin } from '../lib/queries';

/** The operator picker is compiled in only for development builds, whatever the API says. */
const DEV_SIGNIN = import.meta.env.DEV || import.meta.env.VITE_DEMO_SIGNIN === 'true';

/** What the SSO callback reports back as `?error=`, in words a person can act on. */
const SSO_ERRORS: Record<string, string> = {
  sso: 'Sign-in did not complete. Try again.',
  unauthorized: 'Sign-in did not complete. Try again.',
  not_an_operator: 'This account is not a platform operator. Ask a platform owner for access.',
  operator_disabled: 'Your operator account is disabled. Ask a platform owner to restore it.',
  identity_conflict: 'This email is linked to a different sign-in identity. Ask a platform owner.',
  email_unverified: 'Your account has no verified email address. Contact your IT team.',
};

/** Read `?error=` once and drop it from the address bar so a reload starts clean. */
function takeSsoError(): string | null {
  const params = new URLSearchParams(window.location.search);
  const code = params.get('error');
  if (!code) return null;
  params.delete('error');
  const rest = params.toString();
  window.history.replaceState(null, '', window.location.pathname + (rest ? `?${rest}` : ''));
  return SSO_ERRORS[code] ?? 'Sign-in did not complete. Try again.';
}

export function SignIn() {
  const config = useAuthConfig();
  const login = useDevLogin();
  const [notice] = useState<string | null>(takeSsoError);
  const [redirecting, setRedirecting] = useState(false);
  const sso = config.data?.sso ?? false;
  const operators = DEV_SIGNIN ? (config.data?.devOperators ?? []) : [];

  const startSso = () => {
    const next = window.location.pathname === '/' ? '/tenants' : window.location.pathname;
    setRedirecting(true);
    window.location.assign(`/v1/platform/auth/oidc/login?${new URLSearchParams({ next }).toString()}`);
  };
  const error =
    (login.error instanceof ApiError
      ? login.error.problem.title
      : login.error
        ? 'Could not sign in.'
        : null) ?? notice;

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'grid',
        gridTemplateColumns: 'minmax(0, 1.05fr) minmax(0, 1fr)',
        background: 'var(--surface)',
      }}
      className="signin-grid"
    >
      <style>{`@media (max-width: 860px) { .signin-grid { grid-template-columns: 1fr !important; } .signin-hero { display: none !important; } }`}</style>
      <div
        className="signin-hero"
        style={{
          background: 'var(--ink)',
          color: '#fff',
          padding: '46px 52px',
          display: 'flex',
          flexDirection: 'column',
          minWidth: 0,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div
            style={{
              width: 24,
              height: 24,
              borderRadius: 7,
              background: '#fff',
              display: 'grid',
              placeItems: 'center',
            }}
          >
            <div
              style={{
                width: 9,
                height: 9,
                borderRadius: 2,
                background: 'var(--ink)',
                animation: 'breathe 2.6s ease-in-out infinite',
              }}
            />
          </div>
          <span style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-0.01em' }}>
            Command Inbox <span style={{ color: '#a9abb2', fontWeight: 500 }}>· Platform console</span>
          </span>
        </div>
        <div style={{ marginTop: 'auto', marginBottom: 'auto', maxWidth: 460 }}>
          <h1
            style={{
              margin: '0 0 16px',
              fontSize: 38,
              fontWeight: 600,
              letterSpacing: '-0.03em',
              lineHeight: 1.12,
            }}
          >
            Set up each bank. Then step back.
          </h1>
          <p style={{ fontSize: 15, lineHeight: 1.6, color: '#a9abb2' }}>
            Create a tenant, watch it provision and invite the bank's first admin. From then on the bank runs
            its own workspace.
          </p>
        </div>
      </div>

      <div
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 46, minWidth: 0 }}
      >
        <div style={{ width: '100%', maxWidth: 400 }} className="rise">
          <h2 style={{ margin: '0 0 5px', fontSize: 22, fontWeight: 600, letterSpacing: '-0.02em' }}>
            Sign in to the console
          </h2>
          <p style={{ margin: '0 0 22px', fontSize: 13, color: 'var(--muted)' }}>
            For Command Inbox platform operators only. Bank staff sign in to their own workspace.
          </p>
          {error && (
            <div
              role="alert"
              style={{
                fontSize: 12,
                color: 'var(--bad-text)',
                background: 'var(--bad-bg-2)',
                border: '1px solid var(--bad-line)',
                borderRadius: 8,
                padding: '8px 10px',
                marginBottom: 9,
              }}
            >
              {error}
            </div>
          )}
          {sso && (
            <Button
              variant="dark"
              size="lg"
              loading={redirecting}
              onClick={startSso}
              style={{ width: '100%' }}
            >
              Continue with single sign-on
            </Button>
          )}
          {config.isSuccess && !sso && !operators.length && (
            <p style={{ fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.5 }}>
              Single sign-on is not set up for the console yet. Ask a platform owner.
            </p>
          )}

          {operators.length > 0 && (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 11, margin: '22px 0 12px' }}>
                <div style={{ flex: 1, height: 1, background: 'var(--line-faint)' }} />
                <span style={{ fontSize: 11, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                  Development only · sign in as
                </span>
                <div style={{ flex: 1, height: 1, background: 'var(--line-faint)' }} />
              </div>
              <div style={{ display: 'grid', gap: 7 }}>
                {operators.map((o) => (
                  <button
                    key={o.email}
                    type="button"
                    disabled={login.isPending}
                    onClick={() => login.mutate(o.email)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 11,
                      width: '100%',
                      textAlign: 'left',
                      border: '1px solid var(--line)',
                      background: 'var(--surface)',
                      borderRadius: 10,
                      padding: '10px 12px',
                    }}
                  >
                    <span
                      style={{
                        width: 30,
                        height: 30,
                        borderRadius: 8,
                        background: 'var(--accent-bg)',
                        color: 'var(--accent)',
                        fontSize: 11,
                        fontWeight: 600,
                        display: 'grid',
                        placeItems: 'center',
                      }}
                    >
                      {initials(o.name)}
                    </span>
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <span style={{ display: 'block', fontSize: 13, fontWeight: 600 }}>{o.name}</span>
                      <span style={{ display: 'block', fontSize: 11.5, color: 'var(--muted)' }}>
                        {OPERATOR_ROLE[o.role]} · {o.email}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </>
          )}
          <p style={{ margin: '20px 0 0', fontSize: 11.5, color: 'var(--muted)', lineHeight: 1.5 }}>
            Every console action is recorded against your name in the platform audit log.
          </p>
        </div>
      </div>
    </div>
  );
}
