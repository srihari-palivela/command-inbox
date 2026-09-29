import type { BoardDTO } from '@ci/contracts';
import { useState } from 'react';
import { api } from '../../lib/api';
import { keys, useAction } from '../../lib/queries';
import { Button, Field, Input, Modal } from '../../ui';
import s from './Boards.module.css';

type Provider = 'microsoft' | 'google' | 'imap';

const PROVIDERS: { key: Provider; mark: string; name: string; note: string; fg: string; bg: string }[] = [
  {
    key: 'microsoft',
    mark: 'M365',
    name: 'Microsoft 365',
    note: 'OAuth via Entra ID · recommended for your tenant',
    fg: 'var(--accent)',
    bg: 'var(--accent-bg)',
  },
  {
    key: 'google',
    mark: 'GW',
    name: 'Google Workspace',
    note: 'OAuth with a domain-wide delegation',
    fg: 'var(--ok)',
    bg: 'var(--ok-bg)',
  },
  {
    key: 'imap',
    mark: 'IMAP',
    name: 'IMAP / Exchange on-premise',
    note: 'Service account with an app password',
    fg: 'var(--text-2)',
    bg: 'var(--surface-3)',
  },
];

const STEPS = ['Choose a provider', 'Grant read access', 'Name the board'];
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function NewBoardWizard({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [step, setStep] = useState(0);
  const [provider, setProvider] = useState<Provider>('microsoft');
  const [name, setName] = useState('');
  const [mailbox, setMailbox] = useState('');
  const p = PROVIDERS.find((x) => x.key === provider)!;

  const close = () => {
    onClose();
    setStep(0);
    setName('');
    setMailbox('');
  };

  const create = useAction(
    (body: { name: string; provider: Provider; mailbox: string }) =>
      api.post<{ board: BoardDTO; authorizeUrl: string | null }>('/v1/boards', body),
    {
      invalidate: [keys.boards, keys.ticketsAll, keys.me],
      success: (r, v) =>
        r.authorizeUrl
          ? null
          : `${v.name} created · reading mail over ${PROVIDERS.find((x) => x.key === v.provider)!.name} (observe mode — the AI scores nothing until an admin promotes the board)`,
    },
  );

  const valid = name.trim().length > 0 && EMAIL.test(mailbox.trim());
  const submit = () => {
    if (!valid) return;
    create.mutate(
      { name: name.trim(), provider, mailbox: mailbox.trim().toLowerCase() },
      {
        onSuccess: (r) => {
          // With OAuth configured, the provider's consent screen grants the read scope.
          if (r.authorizeUrl) window.location.assign(r.authorizeUrl);
          else close();
        },
      },
    );
  };

  return (
    <Modal open={open} onClose={close} title="Connect a mailbox" width={560}>
      <ol className={s.steps} aria-label="Steps">
        {STEPS.map((label, i) => (
          <li key={label} className={s.stepItem} aria-current={i === step ? 'step' : undefined}>
            <span className={`${s.stepN} mono`} data-state={i < step ? 'done' : i === step ? 'now' : 'todo'}>
              {i < step ? '✓' : i + 1}
            </span>
            <span className={s.stepLabel} data-now={i === step || undefined}>
              {label}
            </span>
          </li>
        ))}
      </ol>

      {step === 0 && (
        <div className={s.providers}>
          {PROVIDERS.map((x) => (
            <button
              key={x.key}
              type="button"
              className={s.provider}
              aria-pressed={provider === x.key}
              onClick={() => {
                setProvider(x.key);
                setStep(1);
              }}
            >
              <span className={`${s.providerMark} mono`} style={{ color: x.fg, background: x.bg }}>
                {x.mark}
              </span>
              <span>
                <span className={s.providerName}>{x.name}</span>
                <span className={s.providerNote}>{x.note}</span>
              </span>
            </button>
          ))}
        </div>
      )}

      {step === 1 && (
        <div>
          <div className={s.grant}>
            <div className={s.grantHead}>
              {p.key === 'imap' ? 'The service account will be able to' : `${p.name} will ask you to grant`}
            </div>
            <ul className={s.scopes}>
              <li>
                <span className={s.yes} aria-hidden>
                  ✓
                </span>
                <span>
                  <b>Read mail</b> in the chosen shared mailbox only
                </span>
              </li>
              <li>
                <span className={s.yes} aria-hidden>
                  ✓
                </span>
                <span>
                  <b>Apply labels</b> so people can see what the AI has triaged
                </span>
              </li>
              <li className={s.denied}>
                <span className={s.no} aria-hidden>
                  ×
                </span>
                <span>
                  <b>Send mail</b> — used only for replies a named person approved, in the customer's thread,
                  and only after an admin turns sending on.
                </span>
              </li>
            </ul>
          </div>
          <div className={s.actions}>
            <Button onClick={() => setStep(0)}>Back</Button>
            <Button variant="dark" style={{ marginLeft: 'auto' }} onClick={() => setStep(2)}>
              Grant read access
            </Button>
          </div>
        </div>
      )}

      {step === 2 && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <div className={s.ok}>
            <span className={s.okMark} aria-hidden>
              ✓
            </span>
            <span>
              {p.key === 'imap'
                ? 'Read-only access over IMAP. No send permission is configured.'
                : `Read-only scope with ${p.name}. You confirm it on their consent screen after you create the board.`}
            </span>
          </div>
          <div className={s.fields}>
            <Field label="Board name">
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Cards support"
                maxLength={80}
                data-autofocus
                required
              />
            </Field>
            <Field label="Mailbox address">
              <Input
                type="email"
                value={mailbox}
                onChange={(e) => setMailbox(e.target.value)}
                placeholder="cards.support@bank.example"
                className="mono"
                required
              />
            </Field>
          </div>
          <p className={s.observe}>
            New boards start in observe mode: the AI reads and sorts, but nothing reaches a customer until an
            admin promotes the board.
          </p>
          <div className={s.actions}>
            <Button type="button" onClick={() => setStep(1)}>
              Back
            </Button>
            <Button
              type="submit"
              variant="dark"
              style={{ marginLeft: 'auto' }}
              disabled={!valid}
              loading={create.isPending}
            >
              Create board
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}
