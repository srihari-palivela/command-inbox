import type { MeDTO, TicketDetailDTO } from '@ci/contracts';
import { useEffect, useRef, useState } from 'react';
import { newIdempotencyKey } from '../../lib/api';
import { clockTime, formatMinutes, minutesLeftLabel } from '../../lib/format';
import { pref } from '../../lib/prefs';
import { PRIORITY_TONE, SLA_TONE } from '../../lib/presentation';
import { Avatar, Button, Card, Eyebrow, Tabs, cx } from '../../ui';
import { offerRecall, useTicketActions, type ApproveResult } from './actions';
import { CallOverlay } from './CallOverlay';
import { AmendModal, Gateway } from './Gateway';
import s from './Inbox.module.css';
import { TicketFields } from './TicketFields';
import { Trace } from './Trace';
import { Triage } from './Triage';
import { BriefCard, DraftCard, FilledAction, Progress } from './Work';

export type DetailTab = 'conversation' | 'fields' | 'trace';

export interface DetailHandle {
  approve: () => void;
  reply: () => void;
  edit: () => void;
}

/**
 * One ticket: the thread, the AI's reasoning and pre-work, and the gateway. `openedEvidence` records
 * whether the approver actually looked at the work before approving (review W1: the approve-without-open
 * canary) — it turns true when the work card has been on screen for a moment, or the person opened the
 * reasoning, the fields or the trace.
 */
export function Detail({
  t,
  me,
  onNext,
  onApproved,
  onOpenTicket,
  handle,
}: {
  t: TicketDetailDTO;
  me: MeDTO;
  onNext: () => void;
  onApproved: (r: ApproveResult) => void;
  onOpenTicket: (number: string) => void;
  handle: React.MutableRefObject<DetailHandle | null>;
}) {
  const actions = useTicketActions(t);
  const [tab, setTab] = useState<DetailTab>('conversation');
  const [showWhy, setShowWhy] = useState(true);
  const [opened, setOpened] = useState(false);
  const [amending, setAmending] = useState(false);
  const [editingDraft, setEditingDraft] = useState(false);
  const [replyOpen, setReplyOpen] = useState(false);
  const [callId, setCallId] = useState<string | null>(null);
  const workRef = useRef<HTMLElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const approveKey = useRef<{ scope: string; key: string } | null>(null);

  // Reset per-ticket UI when the selection changes.
  useEffect(() => {
    setTab('conversation');
    setOpened(false);
    setAmending(false);
    setEditingDraft(false);
    setReplyOpen(false);
    scrollRef.current?.scrollTo({ top: 0 });
  }, [t.id]);

  // Evidence seen: the work card was at least 60% visible for 1.2s.
  useEffect(() => {
    const el = workRef.current;
    if (!el || opened || typeof IntersectionObserver === 'undefined') return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const io = new IntersectionObserver(
      ([e]) => {
        clearTimeout(timer);
        if (e?.isIntersecting) timer = setTimeout(() => setOpened(true), 1200);
      },
      { threshold: 0.6 },
    );
    io.observe(el);
    return () => {
      clearTimeout(timer);
      io.disconnect();
    };
  }, [t.id, opened, tab]);

  const approve = () => {
    if (!t.gate.canApprove || actions.approve.isPending) return;
    // Same key for retries of the same decision on the same gate state; a new state gets a new key.
    const scope = `${t.id}:${t.gate.state}:${t.version}`;
    if (approveKey.current?.scope !== scope) approveKey.current = { scope, key: newIdempotencyKey() };
    actions.approve.mutate(
      { openedEvidence: opened, key: approveKey.current.key },
      { onSuccess: onApproved },
    );
  };
  const edit = () => {
    setOpened(true);
    setTab('conversation');
    if (t.gate.mode === 'draft') {
      setEditingDraft(true);
      setTimeout(() => workRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
    } else if (t.action) setAmending(true);
  };
  const reply = () => {
    setTab('conversation');
    setReplyOpen(true);
    setTimeout(
      () =>
        scrollRef.current
          ?.querySelector('#reply-body')
          ?.scrollIntoView({ behavior: 'smooth', block: 'center' }),
      50,
    );
  };
  handle.current = { approve, reply, edit };

  const call = () =>
    actions.startCall.mutate(undefined, {
      onSuccess: (c) => setCallId(c.id),
    });

  const pr = PRIORITY_TONE[t.priority];
  const sla = SLA_TONE[t.sla.tone];
  const stream = pref(me, 'stream');

  return (
    <section className={s.detail} aria-label={`Ticket ${t.number}`}>
      <div className={s.detailScroll} ref={scrollRef}>
        <div className={s.detailInner} key={t.id}>
          <header>
            <div className={s.metaLine}>
              <span className="mono">{t.number}</span>
              <span
                className="mono"
                style={{ color: pr.fg, background: pr.bg, borderRadius: 5, padding: '2px 7px', fontSize: 11 }}
                title={pr.note}
              >
                {pr.label.split(' · ')[1]} · {t.segment || 'Unmatched sender'}
              </span>
              {t.sla.tone !== 'closed' && t.sla.minutesLeft !== null && (
                <span style={{ color: sla.fg }}>
                  <b className="mono">{minutesLeftLabel(t.sla.minutesLeft)}</b>{' '}
                  {t.sla.minutesLeft >= 0 ? 'left' : ''} of {formatMinutes(t.sla.budgetMinutes)} · {sla.label}
                </span>
              )}
              <span style={{ marginLeft: 'auto' }} className="mono">
                {clockTime(t.receivedAt)}
              </span>
            </div>
            <h2 className={s.subject}>{t.subject}</h2>
            <div className={s.from}>
              <Avatar initials={t.fromInitials} size={24} />
              <b style={{ fontWeight: 500 }}>{t.fromName}</b>
              <span style={{ color: 'var(--muted)' }}>{t.fromEmail}</span>
              {t.customer && (
                <span className="mono" style={{ color: 'var(--muted)', fontSize: 11.5 }}>
                  {t.customer.account}
                </span>
              )}
            </div>
          </header>

          <div className={s.tabRow}>
            <Tabs<DetailTab>
              label="Ticket views"
              value={tab}
              onChange={(k) => {
                setTab(k);
                if (k !== 'conversation') setOpened(true);
              }}
              items={[
                { key: 'conversation', label: 'Conversation', badge: t.messages.length },
                { key: 'fields', label: 'Ticket fields' },
                { key: 'trace', label: 'AI trace', badge: t.trace?.spans.length ?? '' },
              ]}
            />
            <div className={s.tabActions}>
              {me.features.telephony && (
                <Button
                  size="sm"
                  className={s.callBtn}
                  onClick={call}
                  loading={actions.startCall.isPending}
                  disabled={!t.permissions.canWork}
                >
                  ● Call customer
                </Button>
              )}
              <Button size="sm" onClick={reply} disabled={!t.permissions.canReply} kbd="R">
                Reply to customer
              </Button>
            </div>
          </div>

          {tab === 'conversation' && (
            <>
              <Eyebrow>
                Original email{' '}
                <span
                  style={{
                    textTransform: 'none',
                    letterSpacing: 0,
                    fontFamily: 'var(--font-sans)',
                    fontWeight: 400,
                    marginLeft: 6,
                  }}
                >
                  {t.messages.length} message{t.messages.length === 1 ? '' : 's'} · received at {t.mailbox}
                </span>
              </Eyebrow>
              {t.messages.map((m, i) => (
                <article
                  key={m.id}
                  className={cx(
                    s.msg,
                    m.direction === 'note' && s.msgNote,
                    m.direction === 'outbound' && s.msgOut,
                  )}
                  style={{ animationDelay: `${i * 0.05}s` }}
                >
                  <div className={s.msgHead}>
                    <Avatar
                      initials={
                        m.direction === 'inbound'
                          ? t.fromInitials
                          : m.direction === 'note'
                            ? 'AI'
                            : me.user.initials
                      }
                      size={22}
                      fg={m.direction === 'note' ? 'var(--accent)' : undefined}
                      bg={m.direction === 'note' ? 'var(--accent-bg)' : undefined}
                    />
                    <b>{m.fromName}</b>
                    <span className={s.msgAddr}>{m.fromAddr}</span>
                    <span className={s.msgTime}>{clockTime(m.sentAt)}</span>
                  </div>
                  <div className={s.msgTo}>
                    To&nbsp;&nbsp;{m.direction === 'note' ? 'internal note — not sent' : m.toAddr}
                  </div>
                  <div className={s.msgBody}>{m.body}</div>
                </article>
              ))}

              {replyOpen && <Composer t={t} actions={actions} onClose={() => setReplyOpen(false)} />}

              <Triage
                t={t}
                actions={actions}
                stream={stream}
                showWhy={showWhy}
                onToggleWhy={() => {
                  setShowWhy((v) => !v);
                  setOpened(true);
                }}
                confidenceBar={me.org.confidenceBar}
              />

              {t.action && <FilledAction ref={workRef} a={t.action} />}
              {t.draft && (
                <DraftCard
                  ref={workRef}
                  d={t.draft}
                  t={t}
                  actions={actions}
                  editing={editingDraft}
                  onEditing={setEditingDraft}
                />
              )}
              {t.brief && t.lane === 'manual' && (
                <BriefCard ref={workRef} b={t.brief} t={t} actions={actions} />
              )}
              <Progress t={t} />
            </>
          )}
          {tab === 'fields' && <TicketFields t={t} actions={actions} onOpenLink={onOpenTicket} />}
          {tab === 'trace' && <Trace trace={t.trace} />}
        </div>
      </div>

      <Gateway
        t={t}
        me={me}
        actions={actions}
        onApprove={approve}
        approving={actions.approve.isPending}
        onEdit={edit}
        onReply={reply}
        onNext={onNext}
      />
      <AmendModal t={t} open={amending} onClose={() => setAmending(false)} actions={actions} />
      <CallOverlay callId={callId} ticketNumber={t.number} onClose={() => setCallId(null)} />
    </section>
  );
}

// ── Reply composer ────────────────────────────────────────────────────────────
const draftKey = (id: string) => `ci.reply.${id}`;
const loadSaved = (id: string) => {
  try {
    return sessionStorage.getItem(draftKey(id)) ?? '';
  } catch {
    return '';
  }
};

function Composer({
  t,
  actions,
  onClose,
}: {
  t: TicketDetailDTO;
  actions: ReturnType<typeof useTicketActions>;
  onClose: () => void;
}) {
  const [text, setText] = useState(() => loadSaved(t.id));
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  const persist = (v: string | null) => {
    try {
      if (v) sessionStorage.setItem(draftKey(t.id), v);
      else sessionStorage.removeItem(draftKey(t.id));
    } catch {
      /* storage unavailable: the draft just isn't kept */
    }
  };
  const send = () =>
    actions.reply.mutate(text.trim(), {
      onSuccess: (r) => {
        persist(null);
        offerRecall(actions, r.replyId, t.fromName);
        onClose();
      },
    });
  return (
    <Card
      title="Your reply"
      meta={`sent as ${t.fromEmail} · under your name`}
      actions={
        <>
          {t.draft ? (
            <Button size="sm" variant="soft" onClick={() => setText(t.draft!.currentBody)}>
              Pull in the AI draft
            </Button>
          ) : (
            <span
              style={{
                fontSize: 11.5,
                color: 'var(--accent)',
                background: 'var(--accent-bg-2)',
                border: '1px solid var(--accent-line)',
                borderRadius: 6,
                padding: '3px 8px',
              }}
            >
              No AI draft here
            </span>
          )}
          <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close the reply">
            ✕
          </Button>
        </>
      }
      className={s.composer}
    >
      <label className="sr-only" htmlFor="reply-body">
        Reply to {t.fromName}
      </label>
      <textarea
        id="reply-body"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Write your reply, or pull in the AI draft above…"
        autoFocus
      />
      <div className={s.composerFoot}>
        <span className={s.small} style={{ marginTop: 0 }}>
          {words ? `${words} words · recallable for 60s after send` : 'Empty'}
        </span>
        <span style={{ flex: 1 }} />
        <Button
          onClick={() => {
            persist(null);
            setText('');
            onClose();
          }}
        >
          Discard
        </Button>
        <Button onClick={() => persist(text)} disabled={!text.trim()}>
          Save draft
        </Button>
        <Button variant="primary" disabled={!text.trim()} loading={actions.reply.isPending} onClick={send}>
          Send reply
        </Button>
      </div>
    </Card>
  );
}
