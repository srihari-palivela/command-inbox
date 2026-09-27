import type { MeDTO, SessionDTO } from '@ci/contracts';
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { ago, clockTime } from '../../lib/format';
import { pref, type PrefKey } from '../../lib/prefs';
import { TEAM_ROLE_LABEL } from '../../lib/presentation';
import { invalidate, keys, useAction, useMe, useSessions } from '../../lib/queries';
import { Avatar, Button, Card, EmptyState, Loadable, Page, PageHeader, Pill, Skeleton, TextArea, Toggle } from '../../ui';
import s from './Settings.module.css';

const NOTIFY: { key: PrefKey; label: string; note: string }[] = [
  { key: 'digest', label: 'Daily summary email', note: 'One mail at 08:00 with what is waiting for you' },
  { key: 'breach', label: 'Deadline warnings', note: 'Ping me 30 minutes before a ticket runs late' },
  { key: 'mentions', label: 'Mentions and handovers', note: 'When someone assigns me a ticket or names me in a note' },
  { key: 'quiet', label: 'Quiet hours 19:00–08:00', note: 'Hold non-urgent notifications until the morning' },
];

const WORK: { key: PrefKey; label: string; note: string }[] = [
  { key: 'autoAdvance', label: 'Jump to the next ticket', note: 'After I close one, open the next automatically' },
  { key: 'keyboard', label: 'Keyboard shortcuts', note: 'J and K to move, A to approve' },
  { key: 'stream', label: 'Show the AI thinking as it writes', note: 'Stream the reasoning instead of showing it complete' },
  { key: 'dense', label: 'Compact rows', note: 'Fit more tickets on screen at once' },
];

/** Saves one preference at a time: the switch moves immediately, the server confirms behind it. */
function usePrefToggle() {
  const qc = useQueryClient();
  const save = useAction((prefs: Record<string, boolean>) => api.put('/v1/settings', { prefs }), { invalidate: [keys.me] });
  return (key: PrefKey, value: boolean) => {
    qc.setQueryData<MeDTO | null>(keys.me, (old) => (old ? { ...old, settings: { ...old.settings, prefs: { ...old.settings.prefs, [key]: value } } } : old));
    save.mutate({ [key]: value }, { onError: () => invalidate(qc, [keys.me]) });
  };
}

function ToggleRow({ me, k, label, note, onFlip }: { me: MeDTO; k: PrefKey; label: string; note: string; onFlip: (k: PrefKey, v: boolean) => void }) {
  const on = pref(me, k);
  return (
    <label className={s.row}>
      <span className={s.rowText}>
        <span className={s.rowLabel} style={{ display: 'block' }}>
          {label}
        </span>
        <span className={s.rowNote} style={{ display: 'block' }}>
          {note}
        </span>
      </span>
      <Toggle checked={on} label={label} onChange={(v) => onFlip(k, v)} />
    </label>
  );
}

function lastSignIn(sessions: SessionDTO[] | undefined): string {
  const cur = sessions?.find((x) => x.current);
  if (!cur) return '—';
  const c = clockTime(cur.lastSeenAt);
  const when = /^\d\d:\d\d$/.test(c) ? `Today ${c}` : c.replace(/^yesterday/, 'Yesterday');
  return `${when} from ${cur.location}`;
}

export default function SettingsScreen() {
  const meQ = useMe();
  return (
    <Page narrow>
      <PageHeader title="Settings" subtitle="Your account, how you are notified, and how the queue behaves for you." />
      <Loadable query={meQ} skeleton={<Skeleton h={420} />}>
        {(me) => (me ? <SettingsBody me={me} /> : <EmptyState title="You are signed out" />)}
      </Loadable>
    </Page>
  );
}

function SettingsBody({ me }: { me: MeDTO }) {
  const flip = usePrefToggle();
  const sessions = useSessions();
  const joined = new Date(me.user.joinedAt).toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });

  return (
    <>
      <Card className={s.rise} style={{ animationDelay: '.04s' }}>
        <div className={s.profile}>
          <Avatar initials={me.user.initials} size={52} fg="var(--accent)" bg="var(--accent-bg)" />
          <div className={s.who}>
            <div className={s.name}>{me.user.name}</div>
            <div className={s.line}>
              {me.user.title} · {me.user.pod}
            </div>
            <div className={s.lineSm}>
              {me.user.email} · Joined {joined}
            </div>
          </div>
          <div className={s.roleCol}>
            <Pill fg="var(--accent)" bg="var(--accent-bg)" line="var(--accent-line)">
              {TEAM_ROLE_LABEL[me.role]}
            </Pill>
            <span className={s.org}>in {me.org.name}</span>
          </div>
        </div>
      </Card>

      <div className={s.two}>
        <Card title="Notifications" flush className={s.rise} style={{ animationDelay: '.08s' }}>
          {NOTIFY.map((r) => (
            <ToggleRow key={r.key} me={me} k={r.key} label={r.label} note={r.note} onFlip={flip} />
          ))}
        </Card>
        <Card title="How you work" flush className={s.rise} style={{ animationDelay: '.12s' }}>
          {WORK.map((r) => (
            <ToggleRow key={r.key} me={me} k={r.key} label={r.label} note={r.note} onFlip={flip} />
          ))}
        </Card>
      </div>

      <div className={s.two}>
        <SignatureCard me={me} onFlip={flip} />
        <Card title="Security" flush className={s.rise} style={{ animationDelay: '.2s' }}>
          <div className={s.sec}>
            {[
              { l: 'Two-factor authentication', v: 'On · authenticator app', c: 'var(--ok)' },
              { l: 'Single sign-on', v: 'Microsoft Entra ID', c: 'var(--ink)' },
              { l: 'Password', v: 'Managed by your bank', c: 'var(--muted)' },
              { l: 'Last sign-in', v: lastSignIn(sessions.data), c: 'var(--ink)' },
            ].map((r) => (
              <div key={r.l} className={s.secCell}>
                <div className={s.secLabel}>{r.l}</div>
                <div className={s.secValue} style={{ color: r.c }}>
                  {r.v}
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <SessionsCard query={sessions} />
    </>
  );
}

function SignatureCard({ me, onFlip }: { me: MeDTO; onFlip: (k: PrefKey, v: boolean) => void }) {
  const saved = me.settings.signature;
  const [draft, setDraft] = useState(saved);
  useEffect(() => setDraft(saved), [saved]);
  const on = pref(me, 'signature');
  const save = useAction((signature: string) => api.put('/v1/settings', { signature }), { invalidate: [keys.me], success: 'Signature saved.' });
  const dirty = draft !== saved;

  return (
    <Card
      title="Email signature"
      flush
      className={s.rise}
      style={{ animationDelay: '.16s' }}
      actions={
        <>
          <span className={s.sigState}>{on ? 'On' : 'Off'}</span>
          <Toggle checked={on} label="Add my signature to replies" onChange={(v) => onFlip('signature', v)} />
        </>
      }
    >
      <div style={{ padding: '13px 16px 15px' }}>
        <label htmlFor="sig" className="sr-only">
          Email signature
        </label>
        <TextArea
          id="sig"
          value={draft}
          rows={4}
          maxLength={2000}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={`${me.user.name}\nCustomer Service · ${me.org.name}\nThis mailbox is monitored 08:00–20:00 IST.`}
          style={{ opacity: on ? 1 : 0.6 }}
        />
        <div className={s.sigActions}>
          <Button disabled={!dirty} loading={save.isPending} onClick={() => save.mutate(draft)}>
            Save signature
          </Button>
          {dirty && (
            <Button variant="ghost" onClick={() => setDraft(saved)}>
              Discard
            </Button>
          )}
          <span className={s.sigHint}>{on ? 'Added under every reply you approve.' : 'Not added to replies.'}</span>
        </div>
      </div>
    </Card>
  );
}

const SHOW = 5;

function SessionsCard({ query }: { query: ReturnType<typeof useSessions> }) {
  const [all, setAll] = useState(false);
  const revoke = useAction((x: SessionDTO) => api.del(`/v1/sessions/${x.id}`), { invalidate: [keys.sessions], success: (_r, x) => `Signed out of ${x.device}.` });
  const revokeOthers = useAction(() => api.post<{ revoked: number }>('/v1/sessions/revoke-others'), {
    invalidate: [keys.sessions],
    success: (r) => (r.revoked ? `Signed out of ${r.revoked} other ${r.revoked === 1 ? 'device' : 'devices'}.` : 'No other devices were signed in.'),
  });

  return (
    <Card title="Where you are signed in" flush className={s.rise} style={{ marginTop: 13, animationDelay: '.24s' }}>
      <Loadable query={query} skeleton={<div style={{ padding: 16 }}><Skeleton h={80} /></div>}>
        {(list) => {
          const sorted = list.slice().sort((a, b) => Number(b.current) - Number(a.current));
          const shown = all ? sorted : sorted.slice(0, SHOW);
          const others = list.filter((x) => !x.current).length;
          return (
            <>
              {shown.map((x) => {
                const recent = x.current || Date.now() - new Date(x.lastSeenAt).getTime() < 5 * 60_000;
                return (
                  <div key={x.id} className={s.session}>
                    <div className={s.sessionMain}>
                      <div className={s.device} title={x.device}>
                        {x.device} · {x.location}
                      </div>
                      <div className={s.when}>{recent ? 'Active now' : `Last used ${ago(x.lastSeenAt)}`}</div>
                    </div>
                    {x.current ? (
                      <Pill fg="var(--ok)" bg="var(--ok-bg)">
                        This device
                      </Pill>
                    ) : (
                      <Button size="sm" variant="ghost" className={s.signOut} loading={revoke.isPending && revoke.variables?.id === x.id} onClick={() => revoke.mutate(x)}>
                        Sign out
                      </Button>
                    )}
                  </div>
                );
              })}
              <div className={s.sessionsFoot}>
                {sorted.length > SHOW && (
                  <button type="button" className={s.more} onClick={() => setAll(!all)}>
                    {all ? 'Show fewer' : `Show all ${sorted.length}`}
                  </button>
                )}
                <Button className={s.dangerBtn} disabled={others === 0} loading={revokeOthers.isPending} onClick={() => revokeOthers.mutate(undefined)}>
                  Sign out of every other device
                </Button>
              </div>
            </>
          );
        }}
      </Loadable>
    </Card>
  );
}
