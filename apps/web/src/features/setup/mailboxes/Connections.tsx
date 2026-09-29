/**
 * Live mailbox connections: add the bank's mailbox, sign in as it (Microsoft 365 or Google Workspace),
 * watch its health, run the test-mail round trip, and only then allow replies to be sent from it.
 * Every state shown here is computed by the server from what actually happened.
 */
import type { HealthLevel, MailboxConnectionDTO, MailConnection, MailConnectorsDTO } from '@ci/contracts';
import { CreateMailboxBody } from '@ci/contracts';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState, type FormEvent } from 'react';
import { api } from '../../../lib/api';
import { ago } from '../../../lib/format';
import { keys } from '../../../lib/queries';
import { toast } from '../../../lib/toast';
import { Button, Card, EmptyState, Field, Input, Loadable, Skeleton } from '../../../ui';
import { Confirm, Notice, ProblemAlert, Select, Tone, useInlineAction } from '../../admin/bits';
import s from './Mailboxes.module.css';

type ToneDef = { label: string; fg: string; bg: string; line: string };

const LEVEL: Record<HealthLevel, ToneDef> = {
  healthy: { label: 'Healthy', fg: 'var(--ok)', bg: 'var(--ok-bg-2)', line: 'var(--ok-line)' },
  degraded: { label: 'Degraded', fg: 'var(--warn)', bg: 'var(--warn-bg)', line: 'var(--warn-line)' },
  down: { label: 'Down', fg: 'var(--bad-text)', bg: 'var(--bad-bg-2)', line: 'var(--bad-line)' },
  unknown: { label: 'Not yet known', fg: 'var(--muted)', bg: 'var(--surface)', line: 'var(--line)' },
};

const CONNECTION_WORD: Record<MailConnection, string> = {
  not_connected: 'Not connected',
  connecting: 'Connecting…',
  syncing: 'Catching up…',
  live: 'Connected',
  degraded: 'Connected, with problems',
  reauth_required: 'Sign-in expired: reconnect',
  disconnected: 'Disconnected',
};

const PROVIDER_WORD: Record<string, string> = {
  microsoft: 'Microsoft 365',
  google: 'Google Workspace',
  imap: 'IMAP',
  dev: 'Development',
};

export function MailboxConnectionsCard({ canEdit }: { canEdit: boolean }) {
  const q = useQuery({
    queryKey: keys.mailboxConnections,
    queryFn: () => api.get<MailConnectorsDTO>('/v1/mailbox-connections'),
    // Poll while something is in flight (connecting, a test waiting for its round trip).
    refetchInterval: (query) => {
      const d = query.state.data;
      const busy = d?.mailboxes.some(
        (m) =>
          m.connection === 'connecting' ||
          m.connection === 'syncing' ||
          (m.lastTestAt && (!m.lastTestOkAt || m.lastTestOkAt < m.lastTestAt)),
      );
      return busy ? 3000 : 30_000;
    },
  });

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('connected')) {
      toast.show('Signed in as the mailbox. Connecting now; this takes a few seconds.');
      params.delete('connected');
      const rest = params.toString();
      window.history.replaceState(null, '', window.location.pathname + (rest ? `?${rest}` : ''));
    }
  }, []);

  return (
    <Card
      title="Mailbox"
      actions={<span className={s.hint}>One sign-in as the mailbox · replies always need a person</span>}
      flush
      className={s.rise}
    >
      <Loadable query={q} skeleton={<Skeleton h={220} />}>
        {(d) => (
          <>
            {d.mailboxes.length === 0 && (
              <EmptyState
                title="No mailbox yet"
                text="Add the shared customer mailbox, then sign in as it once. From then on new mail becomes tickets."
              />
            )}
            {d.mailboxes.map((m) => (
              <Connection key={m.id} m={m} canEdit={canEdit} />
            ))}
            {canEdit && d.mailboxes.length < d.mailboxLimit && <AddMailbox d={d} />}
            {!d.webhooks && d.mailboxes.length > 0 && (
              <div className={s.foot}>
                Providers cannot reach this installation directly, so new mail is checked every minute. With a
                public webhook address it arrives within seconds.
              </div>
            )}
          </>
        )}
      </Loadable>
    </Card>
  );
}

function AddMailbox({ d }: { d: MailConnectorsDTO }) {
  const [address, setAddress] = useState('');
  const [provider, setProvider] = useState<'microsoft' | 'google'>(
    d.providers.microsoft ? 'microsoft' : 'google',
  );
  const [invalid, setInvalid] = useState<string | null>(null);
  const add = useInlineAction(
    (body: CreateMailboxBody) => api.post<MailboxConnectionDTO>('/v1/mailbox-connections', body),
    { invalidate: [keys.mailboxConnections, keys.onboarding], success: 'Mailbox added. Now sign in as it.' },
  );
  const configured = d.providers[provider];
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const parsed = CreateMailboxBody.safeParse({ address, provider });
    if (!parsed.success) return setInvalid('Enter the mailbox email address.');
    setInvalid(null);
    add.mutate(parsed.data, { onSuccess: () => setAddress('') });
  };
  return (
    <form onSubmit={submit} className={s.addForm}>
      <Field label="Mailbox address">
        <Input
          type="email"
          placeholder="customercare@yourbank.com"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
        />
      </Field>
      <Field label="Provider">
        <Select value={provider} onChange={(e) => setProvider(e.target.value as 'microsoft' | 'google')}>
          <option value="microsoft">Microsoft 365</option>
          <option value="google">Google Workspace</option>
        </Select>
      </Field>
      <Button type="submit" variant="dark" loading={add.isPending} disabled={!configured}>
        Add mailbox
      </Button>
      {!configured && (
        <div className={s.full}>
          <Notice tone="warn">
            {PROVIDER_WORD[provider]} is not set up for this installation yet: your platform operator needs
            the app registration your IT team creates for it.
          </Notice>
        </div>
      )}
      {invalid && (
        <div className={s.full} style={{ color: 'var(--bad-text)', fontSize: 12 }}>
          {invalid}
        </div>
      )}
      <div className={s.full}>
        <ProblemAlert error={add.error} />
      </div>
    </form>
  );
}

function Connection({ m, canEdit }: { m: MailboxConnectionDTO; canEdit: boolean }) {
  const [confirming, setConfirming] = useState(false);
  const invalidate = [keys.mailboxConnections, keys.onboarding];
  const connect = useInlineAction(() => api.post<{ authorizeUrl: string }>(`/v1/mailboxes/${m.id}/connect`), {
    success: () => null,
  });
  const test = useInlineAction(() => api.post(`/v1/mailboxes/${m.id}/test`), {
    invalidate,
    success: 'Test mail sent. It should come back within a minute.',
  });
  const sync = useInlineAction(() => api.post(`/v1/mailboxes/${m.id}/sync`), {
    invalidate,
    success: 'Checking for new mail.',
  });
  const sending = useInlineAction(
    (enabled: boolean) => api.put<MailboxConnectionDTO>(`/v1/mailboxes/${m.id}/sending`, { enabled }),
    {
      invalidate,
      success: (r) =>
        r.sendEnabled
          ? 'Approved replies now go out from this mailbox, in the customer’s thread.'
          : 'Sending is off. People reply from their own mail client.',
    },
  );
  const disconnect = useInlineAction(() => api.post(`/v1/mailboxes/${m.id}/disconnect`), {
    invalidate,
    success: 'Disconnected. Remove the app’s access in your admin centre as well.',
  });
  const signIn = () =>
    connect.mutate(undefined, { onSuccess: (r) => window.location.assign(r.authorizeUrl) });

  const connected = !['not_connected', 'disconnected', 'reauth_required'].includes(m.connection);
  const testPending = !!m.lastTestAt && (!m.lastTestOkAt || m.lastTestOkAt < m.lastTestAt);
  return (
    <section className={s.conn} aria-label={`Mailbox ${m.address}`}>
      <div className={s.connHead}>
        <div style={{ minWidth: 0 }}>
          <div className={s.title}>{m.address}</div>
          <div className={s.sub}>
            {PROVIDER_WORD[m.provider] ?? m.provider} · {CONNECTION_WORD[m.connection]}
            {m.mode ? ` · ${m.mode === 'notifications' ? 'notified instantly' : 'checked every minute'}` : ''}
            {m.lastMessageAt ? ` · last mail ${ago(m.lastMessageAt)}` : ''}
          </div>
        </div>
        <Tone tone={LEVEL[m.level]} />
      </div>

      {m.lastError && m.connection !== 'live' && (
        <div className={s.connError} role="alert">
          {m.lastError}
        </div>
      )}

      {connected && (
        <dl className={s.signals}>
          {m.signals.map((sig) => (
            <div key={sig.key} className={s.signal}>
              <dt>
                <span className={s.dot} style={{ background: LEVEL[sig.level].fg }} aria-hidden /> {sig.label}
              </dt>
              <dd>{sig.value}</dd>
            </div>
          ))}
        </dl>
      )}

      {canEdit && (
        <div className={s.connActions}>
          {!connected && (
            <Button variant="dark" loading={connect.isPending} onClick={signIn}>
              {m.connection === 'reauth_required' ? 'Reconnect' : 'Sign in as the mailbox'}
            </Button>
          )}
          {connected && (
            <>
              <Button onClick={() => test.mutate(undefined)} loading={test.isPending || testPending}>
                {testPending ? 'Waiting for the test mail…' : 'Send a test mail'}
              </Button>
              <Button onClick={() => sync.mutate(undefined)} loading={sync.isPending}>
                Check now
              </Button>
              <label className={s.toggle}>
                <input
                  type="checkbox"
                  checked={m.sendEnabled}
                  disabled={sending.isPending || (!m.sendEnabled && !m.lastTestOkAt)}
                  onChange={(e) => sending.mutate(e.target.checked)}
                />
                Send approved replies from this mailbox
              </label>
              <Button variant="secondary" onClick={() => setConfirming(true)} style={{ marginLeft: 'auto' }}>
                Disconnect
              </Button>
            </>
          )}
        </div>
      )}
      {connected && !m.lastTestOkAt && (
        <div className={s.connNote}>
          Send a test mail before turning sending on: it proves this installation can both send from and
          receive into the mailbox. It never becomes a ticket.
        </div>
      )}
      <div style={{ padding: '0 15px' }}>
        <ProblemAlert
          error={connect.error ?? test.error ?? sync.error ?? sending.error ?? disconnect.error}
        />
      </div>

      {m.events.length > 0 && (
        <details className={s.events}>
          <summary>Recent activity</summary>
          <ol>
            {m.events.map((e, i) => (
              <li key={i} className={e.ok ? undefined : s.eventBad}>
                <time dateTime={e.at}>{ago(e.at)}</time> {e.summary}
              </li>
            ))}
          </ol>
        </details>
      )}

      <Confirm
        open={confirming}
        title={`Disconnect ${m.address}?`}
        confirmLabel="Disconnect"
        danger
        pending={disconnect.isPending}
        error={disconnect.error}
        onClose={() => setConfirming(false)}
        onConfirm={() => disconnect.mutate(undefined, { onSuccess: () => setConfirming(false) })}
      >
        New mail stops becoming tickets and approved replies stop going out from this mailbox. We delete our
        copy of the sign-in; also remove the app’s access in your Microsoft or Google admin centre.
      </Confirm>
    </section>
  );
}
