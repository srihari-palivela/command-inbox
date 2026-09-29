/**
 * Retention, the audit export and SIEM streaming. Retention removes customer mail text of long-closed
 * tickets (the record and the audit log stay); the export is a zip with a signed manifest; the SIEM stream
 * posts every new audit event to the bank's endpoint, signed with a shared secret.
 */
import type { OperationsBody, OperationsDTO } from '@ci/contracts';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../lib/api';
import { ago, num } from '../../lib/format';
import { keys } from '../../lib/queries';
import { Button, Card, Field, Input, Skeleton } from '../../ui';
import { Notice, ProblemAlert, useInlineAction } from './bits';
import s from './workspace.module.css';

export function OperationsCard() {
  const q = useQuery({
    queryKey: keys.operations,
    queryFn: () => api.get<OperationsDTO>('/v1/workspace/operations'),
  });
  if (!q.data) return q.isPending ? <Skeleton h={260} /> : <ProblemAlert error={q.error} />;
  return <OperationsForm key={JSON.stringify(q.data)} ops={q.data} />;
}

const days = (v: string) => (v.trim() === '' ? null : Number(v));

function OperationsForm({ ops }: { ops: OperationsDTO }) {
  const [mail, setMail] = useState(ops.retentionMailDays == null ? '' : String(ops.retentionMailDays));
  const [traces, setTraces] = useState(ops.retentionTraceDays == null ? '' : String(ops.retentionTraceDays));
  const [url, setUrl] = useState(ops.siem.url ?? '');
  const [secret, setSecret] = useState('');
  const save = useInlineAction(
    (body: OperationsBody) => api.put<OperationsDTO>('/v1/workspace/operations', body),
    { invalidate: [keys.operations], success: 'Retention and SIEM settings saved.' },
  );
  const submit = () =>
    save.mutate({
      retentionMailDays: days(mail),
      retentionTraceDays: days(traces),
      siemUrl: url.trim() || null,
      ...(secret ? { siemSecret: secret } : {}),
    });
  return (
    <Card title="Retention and audit">
      <div style={{ display: 'grid', gap: 12 }}>
        <p className={s.lede} style={{ margin: 0 }}>
          Retention removes the customer's mail text from tickets closed longer ago than you choose; the
          ticket, its decisions and the audit log stay. The audit log ({num(ops.auditEvents)} events) is
          hash-chained and never removed.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <Field label="Keep mail text for (days)" hint="Blank keeps it. At least 30.">
            <Input
              inputMode="numeric"
              value={mail}
              disabled={!ops.canEdit}
              onChange={(e) => setMail(e.target.value)}
            />
          </Field>
          <Field label="Keep model traces for (days)" hint="Blank keeps them. At least 7.">
            <Input
              inputMode="numeric"
              value={traces}
              disabled={!ops.canEdit}
              onChange={(e) => setTraces(e.target.value)}
            />
          </Field>
        </div>
        <Field label="SIEM endpoint (HTTPS)" hint="New audit events are posted here every minute, in order.">
          <Input
            value={url}
            placeholder="https://siem.yourbank.example/ingest"
            disabled={!ops.canEdit}
            onChange={(e) => setUrl(e.target.value)}
          />
        </Field>
        <Field
          label="Signing secret"
          hint={
            ops.siem.hasSecret
              ? 'Stored. Enter a new one to replace it. Each batch carries X-CI-Signature: sha256=HMAC(secret, body).'
              : 'At least 16 characters. Each batch carries X-CI-Signature: sha256=HMAC(secret, body).'
          }
        >
          <Input
            type="password"
            autoComplete="new-password"
            value={secret}
            disabled={!ops.canEdit}
            onChange={(e) => setSecret(e.target.value)}
          />
        </Field>
        {ops.siem.url && (
          <div className={s.subtle}>
            Delivered up to event {num(ops.siem.deliveredSeq)} · {num(ops.siem.pending)} waiting
            {ops.siem.lastOkAt && ` · last delivery ${ago(ops.siem.lastOkAt)}`}
          </div>
        )}
        {ops.siem.lastError && (
          <Notice tone="warn">Delivery failing: {ops.siem.lastError}. Events are kept and retried.</Notice>
        )}
        <ProblemAlert error={save.error} />
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {ops.canEdit && (
            <Button variant="dark" loading={save.isPending} onClick={submit}>
              Save
            </Button>
          )}
          <a className={s.subtle} href="/v1/audit/export" download style={{ marginLeft: 'auto' }}>
            Download the audit log (signed zip)
          </a>
        </div>
      </div>
    </Card>
  );
}
