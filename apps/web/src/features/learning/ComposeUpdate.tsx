import type { NotificationBody, NotificationKind } from '@ci/contracts';
import { useEffect, useId, useState } from 'react';
import { api } from '../../lib/api';
import { keys, useAction, useLearning } from '../../lib/queries';
import { Button, Field, Input, Modal, TextArea } from '../../ui';
import s from './Learning.module.css';

const KINDS: { key: NotificationKind; label: string }[] = [
  { key: 'message', label: 'Message' },
  { key: 'learning', label: 'Learning card + quiz' },
];

/** Manual update to the whole support team: a message, or a learning deck whose completion is tracked. */
export function ComposeUpdate({ open, onClose }: { open: boolean; onClose: () => void }) {
  const learning = useLearning();
  const courses = learning.data?.courses ?? [];
  const [title, setTitle] = useState('');
  const [kind, setKind] = useState<NotificationKind>('message');
  const [courseId, setCourseId] = useState('');
  const [body, setBody] = useState('');
  const [tried, setTried] = useState(false);
  const selectId = useId();

  useEffect(() => {
    if (!open) {
      setTitle('');
      setKind('message');
      setBody('');
      setTried(false);
    }
  }, [open]);
  const firstCourseId = courses[0]?.id;
  useEffect(() => {
    if (!courseId && firstCourseId) setCourseId(firstCourseId);
  }, [courseId, firstCourseId]);

  const send = useAction((b: NotificationBody) => api.post<{ recipients: number }>('/v1/notifications', b), {
    invalidate: [keys.learning, keys.me],
    success: (r, b) =>
      `Update sent to ${r.recipients} people${b.kind === 'learning' ? ' — completion will be tracked on the Learning screen.' : '.'}`,
  });

  const titleError = tried && !title.trim() ? 'Give the update a title.' : null;
  const submit = () => {
    setTried(true);
    if (!title.trim()) return;
    const b: NotificationBody = { title: title.trim(), kind };
    if (body.trim()) b.body = body.trim();
    if (kind === 'learning' && courseId) b.courseId = courseId;
    send.mutate(b, { onSuccess: onClose });
  };
  const team = learning.data?.teamSize;

  return (
    <Modal
      open={open}
      onClose={onClose}
      width={500}
      title="Send an update to the team"
      subtitle="Goes to every customer support person in this workspace. Learning cards track completion."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="dark" style={{ marginLeft: 'auto' }} loading={send.isPending} onClick={submit}>
            {team ? `Send to ${team} people` : 'Send'}
          </Button>
        </>
      }
    >
      <form
        className={s.form}
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        noValidate
      >
        <Field label="Title" hint={titleError ? <span className={s.error}>{titleError}</span> : undefined}>
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Locker rent waiver — new sign-off rule"
            maxLength={160}
            aria-invalid={!!titleError}
            data-autofocus
          />
        </Field>
        <div>
          <div className={s.groupLabel} id={`${selectId}-fmt`}>
            Format
          </div>
          <div className={s.kinds} role="group" aria-labelledby={`${selectId}-fmt`}>
            {KINDS.map((k) => (
              <button
                key={k.key}
                type="button"
                className={s.kindBtn}
                aria-pressed={kind === k.key}
                onClick={() => setKind(k.key)}
              >
                {k.label}
              </button>
            ))}
          </div>
        </div>
        {kind === 'learning' && (
          <div>
            <label className={s.groupLabel} htmlFor={selectId} style={{ display: 'block' }}>
              Course to attach
            </label>
            <select
              id={selectId}
              className={s.select}
              value={courseId}
              onChange={(e) => setCourseId(e.target.value)}
            >
              {courses.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </select>
          </div>
        )}
        <Field label="Message (optional)">
          <TextArea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            maxLength={2000}
            rows={3}
            placeholder="What changed, and what people should do differently."
          />
        </Field>
      </form>
    </Modal>
  );
}
