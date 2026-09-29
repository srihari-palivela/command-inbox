import type { KnowledgeDocumentDTO, KnowledgeDocStatus, KnowledgeParseStatus } from '@ci/contracts';
import { useState, type FormEvent } from 'react';
import { api, ApiError } from '../../../lib/api';
import { shortDate } from '../../../lib/format';
import type { Tone } from '../../../lib/presentation';
import {
  keys,
  useAction,
  useKnowledgeDocument,
  useKnowledgeDocuments,
  useTaxonomy,
} from '../../../lib/queries';
import { Button, Card, Drawer, EmptyState, Field, Input, Modal, Pill, Skeleton, TextArea } from '../../../ui';
import { ProblemAlert, Select } from '../../admin/bits';
import s from './Knowledge.module.css';

const STATUS_TONE: Record<KnowledgeDocStatus, Tone & { label: string }> = {
  pending: { fg: 'var(--warn)', bg: 'var(--warn-bg)', label: 'Awaiting approval' },
  approved: { fg: 'var(--ok)', bg: 'var(--ok-bg-2)', label: 'Approved · citable' },
  rejected: { fg: 'var(--bad-text)', bg: 'var(--bad-bg)', label: 'Rejected' },
  retired: { fg: 'var(--muted)', bg: 'var(--surface-3)', label: 'Retired' },
  stale: { fg: 'var(--warn)', bg: 'var(--warn-bg)', label: 'Expired' },
};

const PARSE_LABEL: Record<KnowledgeParseStatus, string> = {
  none: 'Not processed',
  queued: 'Queued',
  scanning: 'Virus scan…',
  parsing: 'Reading…',
  ready: 'Ready',
  failed: 'Could not read',
  infected: 'Blocked by virus scan',
};

const ACCEPT = '.pdf,.docx,.xlsx,.html,.htm,.md,.txt';
const NO_EDIT = 'Only an Admin can upload knowledge documents.';

const kb = (n: number) =>
  n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1048576).toFixed(1)} MB`;
const day = (iso: string | null) => (iso ? shortDate(iso) : '—');
/** A date input's value as the start of that day, UTC. */
const startOfDay = (d: string) => (d ? `${d}T00:00:00Z` : '');

function StatusPill({ status }: { status: KnowledgeDocStatus }) {
  const t = STATUS_TONE[status];
  return (
    <Pill fg={t.fg} bg={t.bg}>
      {t.label}
    </Pill>
  );
}

function Processing({ d }: { d: KnowledgeDocumentDTO }) {
  const bad = d.parseStatus === 'failed' || d.parseStatus === 'infected';
  return (
    <span
      style={{ color: bad ? 'var(--bad-text)' : 'var(--muted)', fontSize: 11.5 }}
      title={d.parseError || undefined}
    >
      {PARSE_LABEL[d.parseStatus]}
      {d.parseStatus === 'ready' && ` · ${d.chunkCount} passages`}
      {d.avStatus === 'not_scanned' && d.parseStatus === 'ready' && ' · not virus-scanned'}
    </span>
  );
}

export function DocumentsCard({
  canEdit,
  onUpload,
  onOpen,
}: {
  canEdit: boolean;
  onUpload: () => void;
  onOpen: (id: string) => void;
}) {
  const q = useKnowledgeDocuments();
  const [filter, setFilter] = useState<'all' | KnowledgeDocStatus>('all');
  const docs = (q.data ?? []).filter((d) => filter === 'all' || d.status === filter);
  const pending = (q.data ?? []).filter((d) => d.status === 'pending' && d.parseStatus === 'ready').length;
  return (
    <Card
      title="Documents"
      meta={pending ? `${pending} awaiting approval` : undefined}
      actions={
        <Select
          aria-label="Filter by status"
          value={filter}
          onChange={(e) => setFilter(e.target.value as typeof filter)}
        >
          <option value="all">All</option>
          {(Object.keys(STATUS_TONE) as KnowledgeDocStatus[]).map((k) => (
            <option key={k} value={k}>
              {STATUS_TONE[k].label}
            </option>
          ))}
        </Select>
      }
      flush
      className={s.rise}
      style={{ marginTop: 13 }}
    >
      {q.isPending ? (
        <div style={{ padding: 14 }}>
          <Skeleton h={120} />
        </div>
      ) : q.error ? (
        <div style={{ padding: 14 }}>
          <ProblemAlert error={q.error} />
        </div>
      ) : docs.length === 0 ? (
        <EmptyState
          title={filter === 'all' ? 'No documents yet' : 'Nothing with this status'}
          text="Upload a policy, product sheet or procedure. It is virus-scanned and read inside your tenancy, then waits for a knowledge manager's approval before the AI may quote it."
          action={
            filter === 'all' && (
              <Button
                variant="dark"
                disabled={!canEdit}
                title={canEdit ? undefined : NO_EDIT}
                onClick={onUpload}
              >
                Upload a document
              </Button>
            )
          }
        />
      ) : (
        <div className={s.docTable} role="table" aria-label="Knowledge documents">
          <div className={s.docHead} role="row">
            <span role="columnheader">Document</span>
            <span role="columnheader">Department</span>
            <span role="columnheader">Status</span>
            <span role="columnheader">Processing</span>
            <span role="columnheader">In force</span>
          </div>
          {docs.map((d) => (
            <button key={d.id} type="button" role="row" className={s.docRow} onClick={() => onOpen(d.id)}>
              <span role="cell" style={{ minWidth: 0 }}>
                <span className={s.docTitle}>{d.title}</span>
                <span className={s.docSub}>
                  {d.filename} · v{d.version} · {kb(d.size)}
                </span>
              </span>
              <span role="cell" className={s.docSub}>
                {d.department ?? 'All departments'}
              </span>
              <span role="cell">
                <StatusPill status={d.status} />
              </span>
              <span role="cell">
                <Processing d={d} />
              </span>
              <span role="cell" className={s.docSub}>
                {d.effectiveFrom ? day(d.effectiveFrom) : 'Now'} →{' '}
                {d.expiresAt ? day(d.expiresAt) : 'no expiry'}
              </span>
            </button>
          ))}
        </div>
      )}
    </Card>
  );
}

export function DocumentDrawer({
  id,
  onClose,
  onReplace,
  canEdit,
}: {
  id: string | null;
  onClose: () => void;
  onReplace: (d: KnowledgeDocumentDTO) => void;
  canEdit: boolean;
}) {
  const q = useKnowledgeDocument(id);
  const [reason, setReason] = useState('');
  const invalidate = [keys.knowledgeDocs, keys.knowledge];
  const review = useAction(
    ({ verb, why }: { verb: 'approve' | 'reject' | 'retire'; why: string }) =>
      api.post<KnowledgeDocumentDTO>(
        `/v1/knowledge/documents/${id}/${verb}`,
        verb === 'approve' ? {} : { reason: why },
      ),
    {
      invalidate,
      success: (_r, v) =>
        v.verb === 'approve'
          ? 'Approved — the AI may now cite this document.'
          : v.verb === 'reject'
            ? 'Rejected — it will not be cited.'
            : 'Retired — it is no longer cited.',
    },
  );
  const d = q.data;
  const act = (verb: 'approve' | 'reject' | 'retire') =>
    review.mutate({ verb, why: reason }, { onSuccess: () => setReason('') });
  const reviewable = d?.status === 'pending' && d.parseStatus === 'ready';
  const retirable = d?.status === 'approved' || d?.status === 'stale';

  return (
    <Drawer
      open={!!id}
      onClose={onClose}
      width={620}
      title={d?.title ?? 'Document'}
      subtitle={d && <StatusPill status={d.status} />}
      footer={
        d && (
          <>
            {reviewable && (
              <>
                <Button
                  disabled={!d.canApprove}
                  title={d.canApprove ? undefined : 'Approving needs approve clearance for this department.'}
                  loading={review.isPending && review.variables?.verb === 'reject'}
                  onClick={() => act('reject')}
                >
                  Reject
                </Button>
                <Button
                  variant="dark"
                  style={{ marginLeft: 'auto' }}
                  disabled={!d.canApprove}
                  title={d.canApprove ? undefined : 'Approving needs approve clearance for this department.'}
                  loading={review.isPending && review.variables?.verb === 'approve'}
                  onClick={() => act('approve')}
                >
                  Approve for citation
                </Button>
              </>
            )}
            {retirable && (
              <>
                <Button
                  disabled={!canEdit}
                  title={canEdit ? undefined : NO_EDIT}
                  onClick={() => onReplace(d)}
                >
                  Upload a new version
                </Button>
                <Button
                  style={{ marginLeft: 'auto' }}
                  disabled={!d.canApprove}
                  loading={review.isPending && review.variables?.verb === 'retire'}
                  onClick={() => act('retire')}
                >
                  Retire
                </Button>
              </>
            )}
          </>
        )
      }
    >
      {q.isPending && id ? (
        <Skeleton h={260} />
      ) : q.error ? (
        <ProblemAlert error={q.error} />
      ) : d ? (
        <div style={{ display: 'grid', gap: 14 }}>
          <dl className={s.meta}>
            <dt>File</dt>
            <dd>
              {d.filename} · v{d.version} · {kb(d.size)}
            </dd>
            <dt>Department</dt>
            <dd>{d.department ?? 'All departments'}</dd>
            <dt>Processing</dt>
            <dd>
              <Processing d={d} />
            </dd>
            <dt>In force</dt>
            <dd>
              {d.effectiveFrom ? day(d.effectiveFrom) : 'From approval'} →{' '}
              {d.expiresAt ? day(d.expiresAt) : 'no expiry'}
            </dd>
            <dt>Uploaded</dt>
            <dd>
              {day(d.createdAt)}
              {d.uploadedBy && ` by ${d.uploadedBy}`}
            </dd>
            {d.approvedAt && (
              <>
                <dt>Approved</dt>
                <dd>
                  {day(d.approvedAt)}
                  {d.approvedBy && ` by ${d.approvedBy}`}
                </dd>
              </>
            )}
          </dl>
          {d.parseError && (
            <div className={s.parseError} role="alert">
              {d.parseError}
            </div>
          )}
          <ProblemAlert error={review.error} />
          {(reviewable || retirable) && d.canApprove && (
            <Field label="Reason (recorded in the audit log for a rejection or retirement)">
              <TextArea rows={2} value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} />
            </Field>
          )}
          <div>
            <div className={s.chunkHead}>
              What the AI will read · {d.chunks.length} passage{d.chunks.length === 1 ? '' : 's'}
            </div>
            {d.chunks.length === 0 ? (
              <p className={s.docSub}>No text has been extracted yet.</p>
            ) : (
              <ol className={s.chunks}>
                {d.chunks.map((c) => (
                  <li key={c.id} className={s.chunk}>
                    <div className={s.chunkMeta}>
                      #{c.ordinal + 1}
                      {c.section && ` · ${c.section}`}
                      {c.page != null && ` · page ${c.page}`} · {c.tokens} tokens
                    </div>
                    <div className={s.chunkText}>{c.text}</div>
                  </li>
                ))}
              </ol>
            )}
          </div>
        </div>
      ) : null}
    </Drawer>
  );
}

export function UploadDocumentModal({
  open,
  replaces,
  onClose,
}: {
  open: boolean;
  replaces: KnowledgeDocumentDTO | null;
  onClose: () => void;
}) {
  const departments = useTaxonomy().data?.departments ?? [];
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [dept, setDept] = useState('');
  const [from, setFrom] = useState('');
  const [until, setUntil] = useState('');
  const reset = () => {
    setFile(null);
    setTitle('');
    setDept('');
    setFrom('');
    setUntil('');
  };
  const upload = useAction(
    (form: FormData) => api.upload<KnowledgeDocumentDTO>('/v1/knowledge/documents', form),
    {
      invalidate: [keys.knowledgeDocs, keys.knowledge],
      success: (d) => `${d.title} uploaded — scanning and reading it now. It is not citable until approved.`,
    },
  );
  const close = () => {
    reset();
    upload.reset();
    onClose();
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    if (title.trim()) form.append('title', title.trim());
    const department = replaces ? replaces.departmentId : dept;
    if (department) form.append('departmentId', department);
    if (from) form.append('effectiveFrom', startOfDay(from));
    if (until) form.append('expiresAt', startOfDay(until));
    if (replaces) form.append('replacesId', replaces.id);
    upload.mutate(form, { onSuccess: close });
  };
  const tooEarly = !!from && !!until && until <= from;

  return (
    <Modal
      open={open}
      onClose={close}
      width={500}
      title={replaces ? `New version of ${replaces.title}` : 'Upload a document'}
      subtitle="PDF, Word, Excel, HTML, Markdown or text. It is virus-scanned and read inside your tenancy, and waits for approval before the AI may quote it."
    >
      <form onSubmit={submit} style={{ display: 'grid', gap: 12 }}>
        <Field label="File">
          <Input
            type="file"
            accept={ACCEPT}
            required
            data-autofocus
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </Field>
        <Field label="Title" hint="Defaults to the file name.">
          <Input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        {!replaces && (
          <Field label="Department" hint="Who may approve it, and whose queries it is preferred for.">
            <Select value={dept} onChange={(e) => setDept(e.target.value)}>
              <option value="">All departments</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </Select>
          </Field>
        )}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          <Field label="In force from" hint="Blank: from approval.">
            <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </Field>
          <Field label="Expires" hint="Blank: no expiry.">
            <Input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
          </Field>
        </div>
        {replaces && (
          <p className={s.modalText} style={{ margin: 0 }}>
            Version {replaces.version} stays citable until this one is approved; approving it retires the old
            one.
          </p>
        )}
        {tooEarly && <div className={s.parseError}>The expiry must be after the start date.</div>}
        <ProblemAlert error={upload.error instanceof ApiError ? upload.error : null} />
        <div style={{ display: 'flex', gap: 8 }}>
          <Button type="button" onClick={close}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="dark"
            style={{ marginLeft: 'auto' }}
            disabled={!file || tooEarly}
            loading={upload.isPending}
          >
            Upload
          </Button>
        </div>
      </form>
    </Modal>
  );
}
