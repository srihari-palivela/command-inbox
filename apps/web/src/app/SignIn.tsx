import { useState, type FormEvent } from 'react';
import { ApiError } from '../lib/api';
import { TEAM_ROLE_LABEL } from '../lib/presentation';
import { useDemo, useLogin } from '../lib/queries';
import { Button, Input } from '../ui';

export function SignIn() {
  const demo = useDemo();
  const login = useLogin();
  const [email, setEmail] = useState('p.sharma@bank.example');
  const submit = (e?: FormEvent, as?: string) => {
    e?.preventDefault();
    login.mutate(as ?? email.trim());
  };
  const error =
    login.error instanceof ApiError ? login.error.problem.title : login.error ? 'Could not sign in.' : null;

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
          <span style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-0.01em' }}>Command Inbox</span>
        </div>
        <div style={{ marginTop: 'auto', maxWidth: 460 }}>
          <h1
            style={{
              margin: '0 0 16px',
              fontSize: 38,
              fontWeight: 600,
              letterSpacing: '-0.03em',
              lineHeight: 1.12,
            }}
          >
            The AI addresses the query. You stay accountable.
          </h1>
          <p style={{ fontSize: 15, lineHeight: 1.6, color: '#a9abb2' }}>
            Customer mail is read, sorted and answered against your own approved material. Every reply and
            every action carries a named human approver.
          </p>
        </div>
        <div style={{ marginTop: 'auto', display: 'flex', gap: 34, paddingTop: 40 }}>
          {[
            ['−72%', 'time to resolve'],
            ['3.6×', 'queries per person'],
            ['100%', 'decisions traced'],
          ].map(([n, l]) => (
            <div key={l}>
              <div className="mono" style={{ fontSize: 22, fontWeight: 600 }}>
                {n}
              </div>
              <div style={{ fontSize: 12, color: '#a9abb2', marginTop: 3 }}>{l}</div>
            </div>
          ))}
        </div>
      </div>

      <div
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 46, minWidth: 0 }}
      >
        <form onSubmit={submit} style={{ width: '100%', maxWidth: 400 }} className="rise">
          <h2 style={{ margin: '0 0 5px', fontSize: 22, fontWeight: 600, letterSpacing: '-0.02em' }}>
            Sign in
          </h2>
          <p style={{ margin: '0 0 22px', fontSize: 13, color: 'var(--muted)' }}>
            Use your bank account. Access is granted by your administrator.
          </p>
          <label style={{ display: 'grid', gap: 5, marginBottom: 9 }}>
            <span style={{ fontSize: 11.5, color: 'var(--muted)' }}>Work email</span>
            <Input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              required
              style={{ height: 40 }}
            />
          </label>
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
          <Button
            type="submit"
            variant="dark"
            size="lg"
            loading={login.isPending}
            style={{ width: '100%', marginBottom: 9 }}
          >
            Continue with Microsoft Entra ID
          </Button>
          <Button
            type="submit"
            variant="secondary"
            size="lg"
            style={{ width: '100%', fontWeight: 500 }}
            disabled={login.isPending}
          >
            Use a one-time passcode instead
          </Button>

          {demo.data?.demoMode && (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 11, margin: '22px 0 12px' }}>
                <div style={{ flex: 1, height: 1, background: 'var(--line-faint)' }} />
                <span style={{ fontSize: 11, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                  Demo workspace · sign in as
                </span>
                <div style={{ flex: 1, height: 1, background: 'var(--line-faint)' }} />
              </div>
              <div style={{ display: 'grid', gap: 7 }}>
                {demo.data.users
                  .filter((u) =>
                    ['p.sharma@bank.example', 'r.menon@bank.example', 'a.kapoor@bank.example'].includes(
                      u.email,
                    ),
                  )
                  .map((u) => (
                    <button
                      key={u.email}
                      type="button"
                      onClick={() => {
                        setEmail(u.email);
                        submit(undefined, u.email);
                      }}
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
                        {u.name
                          .split(/[ .]+/)
                          .filter(Boolean)
                          .map((p) => p[0])
                          .join('')
                          .slice(0, 2)}
                      </span>
                      <span style={{ flex: 1, minWidth: 0 }}>
                        <span style={{ display: 'block', fontSize: 13, fontWeight: 600 }}>{u.name}</span>
                        <span style={{ display: 'block', fontSize: 11.5, color: 'var(--muted)' }}>
                          {TEAM_ROLE_LABEL[u.role]} · {u.title}
                        </span>
                      </span>
                    </button>
                  ))}
              </div>
            </>
          )}
          <p style={{ margin: '20px 0 0', fontSize: 11.5, color: 'var(--muted)', lineHeight: 1.5 }}>
            By signing in you accept that every action you approve is recorded against your name in the audit
            log.
          </p>
        </form>
      </div>
    </div>
  );
}
