/**
 * The organisation profile (legal name, contact, locale, currency, time zone) and the bank's own single
 * sign-on. The SSO client secret is sent once and sealed with the tenant's key; it is never shown again.
 */
import type { WorkspaceProfileDTO, WorkspaceSsoDTO } from '@ci/contracts';
import { WorkspaceProfileBody, WorkspaceSsoBody } from '@ci/contracts';
import { useQuery } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { api } from '../../lib/api';
import { keys } from '../../lib/queries';
import { Button, Card, Field, Input, Loadable, Page, PageHeader, Skeleton } from '../../ui';
import { isForbidden, NoAccess, Notice, ProblemAlert, Select, Tone, useInlineAction } from './bits';
import { ModelPolicyCard } from './ModelPolicyCard';
import { OperationsCard } from './OperationsCard';
import s from './workspace.module.css';

const SSO_TONE: Record<WorkspaceSsoDTO['state'], { label: string; fg: string; bg: string; line: string }> = {
  not_connected: { label: 'Not connected', fg: 'var(--muted)', bg: 'var(--surface)', line: 'var(--line)' },
  saved: { label: 'Saved, not applied', fg: 'var(--warn)', bg: 'var(--warn-bg)', line: 'var(--warn-line)' },
  connected: { label: 'Connected', fg: 'var(--ok)', bg: 'var(--ok-bg)', line: 'var(--ok-line)' },
  failed: { label: 'Failed', fg: 'var(--bad-text)', bg: 'var(--bad-bg-2)', line: 'var(--bad-line)' },
};

export default function OrganisationScreen() {
  const q = useQuery({
    queryKey: keys.workspaceProfile,
    queryFn: () => api.get<WorkspaceProfileDTO>('/v1/workspace/profile'),
  });
  return (
    <Page narrow>
      <div className="rise">
        <PageHeader
          title="Organisation"
          subtitle="Your organisation's details, how figures are shown, how your people sign in, and which AI models may be used."
        />
      </div>
      {isForbidden(q.error) ? (
        <NoAccess error={q.error} what="the organisation profile" who="The profile is managed by admins." />
      ) : (
        <Loadable query={q} skeleton={<Skeleton h={480} />}>
          {(p) => (
            <div style={{ display: 'grid', gap: 14 }}>
              <ProfileForm profile={p} />
              <SsoForm profile={p} />
              <ModelPolicyCard />
              <OperationsCard />
              <Card title="Set by Command Inbox">
                <dl className={s.facts}>
                  <dt>Region</dt>
                  <dd>{p.region || '—'}</dd>
                  <dt>Data residency</dt>
                  <dd>{p.dataResidency || '—'}</dd>
                  <dt>Email domains</dt>
                  <dd>{p.emailDomains.join(', ') || '—'}</dd>
                </dl>
              </Card>
            </div>
          )}
        </Loadable>
      )}
    </Page>
  );
}

function ProfileForm({ profile }: { profile: WorkspaceProfileDTO }) {
  const [v, setV] = useState({
    legalName: profile.legalName,
    supportEmail: profile.supportEmail,
    locale: profile.locale,
    currency: profile.currency,
    timeZone: profile.timeZone,
  });
  const [invalid, setInvalid] = useState<string | null>(null);
  const save = useInlineAction((body: WorkspaceProfileBody) => api.put('/v1/workspace/profile', body), {
    invalidate: [keys.workspaceProfile, keys.me, keys.onboarding],
    success: 'Organisation profile saved.',
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const parsed = WorkspaceProfileBody.safeParse(v);
    if (!parsed.success) {
      setInvalid(parsed.error.issues.map((i) => `${String(i.path[0])}: ${i.message}`).join('; '));
      return;
    }
    setInvalid(null);
    save.mutate(parsed.data);
  };
  const set = (k: keyof typeof v) => (e: { target: { value: string } }) =>
    setV({ ...v, [k]: e.target.value });
  return (
    <Card title="Profile">
      <form onSubmit={submit}>
        <div className={s.form}>
          <div className={s.full}>
            <Field label="Legal name">
              <Input value={v.legalName} onChange={set('legalName')} required />
            </Field>
          </div>
          <Field label="Support email" hint="Shown to your people when something needs a human.">
            <Input type="email" value={v.supportEmail} onChange={set('supportEmail')} />
          </Field>
          <Field label="Time zone" hint="IANA name, e.g. Europe/London or Asia/Kolkata.">
            <Input value={v.timeZone} onChange={set('timeZone')} required />
          </Field>
          <Field label="Locale" hint="How numbers and dates read, e.g. en-GB or en-IN.">
            <Input value={v.locale} onChange={set('locale')} required />
          </Field>
          <Field label="Currency" hint="ISO 4217 code, e.g. GBP, INR, USD.">
            <Input
              value={v.currency}
              onChange={(e) => setV({ ...v, currency: e.target.value.toUpperCase() })}
              required
            />
          </Field>
        </div>
        <div className={s.formFoot}>
          <Button type="submit" variant="dark" loading={save.isPending}>
            Save profile
          </Button>
          {invalid && <span style={{ fontSize: 12, color: 'var(--bad-text)' }}>{invalid}</span>}
        </div>
        <div style={{ padding: '0 16px 16px' }}>
          <ProblemAlert error={save.error} />
        </div>
      </form>
    </Card>
  );
}

function SsoForm({ profile }: { profile: WorkspaceProfileDTO }) {
  const sso = profile.sso;
  const [v, setV] = useState({
    provider: (sso.provider ?? 'entra') as 'entra' | 'google',
    directoryId: sso.directoryId,
    clientId: sso.clientId,
    clientSecret: '',
  });
  const [invalid, setInvalid] = useState<string | null>(null);
  const connect = useInlineAction(
    (body: WorkspaceSsoBody) => api.put<WorkspaceProfileDTO>('/v1/workspace/sso', body),
    {
      invalidate: [keys.workspaceProfile, keys.onboarding],
      success: (r) =>
        r.sso.state === 'connected'
          ? 'Single sign-on is connected. Ask a colleague to sign in with it.'
          : r.sso.state === 'saved'
            ? 'Saved. It takes effect once your platform operator enables it.'
            : 'The connection failed; see the details.',
    },
  );
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const parsed = WorkspaceSsoBody.safeParse({ ...v, clientSecret: v.clientSecret || undefined });
    if (!parsed.success) {
      setInvalid(parsed.error.issues.map((i) => `${String(i.path[0])}: ${i.message}`).join('; '));
      return;
    }
    setInvalid(null);
    connect.mutate(parsed.data);
    setV({ ...v, clientSecret: '' });
  };
  const entra = v.provider === 'entra';
  return (
    <Card title="Single sign-on" actions={<Tone tone={SSO_TONE[sso.state]} />}>
      <form onSubmit={submit}>
        <div className={s.form}>
          <div className={s.full}>
            <Notice>
              {entra
                ? 'In your Entra admin centre, register a single-tenant app with the redirect URI below, grant it openid, email and profile, and create a client secret. Then enter its values here.'
                : 'In your Google Cloud project, create an OAuth client (Web application, Internal) with the redirect URI below. Then enter its values and your Workspace domain here.'}
            </Notice>
          </div>
          <Field label="Provider">
            <Select
              value={v.provider}
              onChange={(e) => setV({ ...v, provider: e.target.value as 'entra' | 'google' })}
            >
              <option value="entra">Microsoft Entra ID</option>
              <option value="google">Google Workspace</option>
            </Select>
          </Field>
          <Field label={entra ? 'Directory (tenant) ID' : 'Workspace domain'}>
            <Input
              value={v.directoryId}
              onChange={(e) => setV({ ...v, directoryId: e.target.value })}
              required
            />
          </Field>
          <Field label={entra ? 'Application (client) ID' : 'Client ID'}>
            <Input value={v.clientId} onChange={(e) => setV({ ...v, clientId: e.target.value })} required />
          </Field>
          <Field label="Client secret" hint={sso.hasSecret ? 'Stored. Leave empty to keep it.' : undefined}>
            <Input
              type="password"
              autoComplete="off"
              value={v.clientSecret}
              onChange={(e) => setV({ ...v, clientSecret: e.target.value })}
              placeholder={sso.hasSecret ? '••••••••' : ''}
            />
          </Field>
          {sso.redirectUri && (
            <div className={s.full}>
              <Field label="Redirect URI to register">
                <div className={s.copy}>
                  {sso.redirectUri.replace(/-(entra|google)\/endpoint$/, `-${v.provider}/endpoint`)}
                </div>
              </Field>
            </div>
          )}
        </div>
        <div className={s.formFoot}>
          <Button type="submit" variant="dark" loading={connect.isPending}>
            {sso.state === 'not_connected' ? 'Connect' : 'Update'}
          </Button>
          <span style={{ fontSize: 12, color: 'var(--muted)' }}>
            {invalid ?? (sso.detail || `${sso.ssoMembers} of your people have signed in with SSO.`)}
          </span>
        </div>
        <div style={{ padding: '0 16px 16px' }}>
          <ProblemAlert error={connect.error} />
        </div>
      </form>
    </Card>
  );
}
