/**
 * Click-to-call. The call runs on the server (telephony connector); the overlay polls it while live, and
 * after hang-up shows the AI's wrap-up for the person to save to the ticket or discard. Escape never
 * silently drops a live call (review C6): it asks to end it first.
 */
import { useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';
import { keys, useAction, useCall } from '../../lib/queries';
import { Avatar, Button, Dot, Eyebrow, Modal } from '../../ui';
import { TICKET_KEYS } from './actions';
import s from './Inbox.module.css';

const mmss = (sec: number) => `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;

export function CallOverlay({ callId, ticketNumber, onClose }: { callId: string | null; ticketNumber: string; onClose: () => void }) {
  const call = useCall(callId);
  const c = call.data;
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [muted, setMuted] = useState(false);
  const end = useAction(() => api.post(`/v1/calls/${callId}/end`), { invalidate: [keys.call(callId ?? '')] });
  const save = useAction((discard: boolean) => api.post(`/v1/calls/${callId}/save`, { discard }), {
    invalidate: [...TICKET_KEYS, keys.call(callId ?? '')],
    success: (_r, discard) => (discard ? 'Call discarded. Nothing was added to the ticket.' : `Call summary and transcript saved to ${ticketNumber}.`),
  });
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' });
  }, [c?.transcript.length]);

  const live = c?.state === 'live' || c?.state === 'dialing';
  const requestClose = () => {
    if (live) setConfirmEnd(true);
    else if (c?.state === 'wrap') setConfirmEnd(true);
    else onClose();
  };

  return (
    <Modal open={!!callId} onClose={requestClose} width={540} labelledBy="call-title">
      <div className={s.callHead}>
        <Avatar initials={c?.customerInitials ?? '··'} size={36} />
        <div style={{ minWidth: 0 }}>
          <div id="call-title" style={{ fontSize: 14, fontWeight: 700 }}>
            {c?.customerName ?? 'Connecting…'}
          </div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--muted)' }}>
            {c?.number ?? ''} · {ticketNumber}
          </div>
        </div>
        <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6 }} className="mono">
          <Dot color={live ? 'var(--ok-dot)' : 'var(--dot-idle)'} pulse={live} />
          <span style={{ fontSize: 12, fontWeight: 600, color: live ? 'var(--ok)' : 'var(--muted)' }}>{c ? (c.state === 'dialing' ? 'dialing' : mmss(c.durationSec)) : '—'}</span>
        </span>
      </div>

      {c && c.state !== 'wrap' && c.state !== 'saved' && c.state !== 'discarded' && (
        <>
          <div style={{ padding: '12px 16px 0' }}>
            <Eyebrow>Live transcript</Eyebrow>
          </div>
          <div className={s.transcript} ref={scroller} aria-live="polite">
            {c.transcript.map((line, i) => (
              <div key={i} className={`${s.bubble} ${line.who === 'You' ? s.bubbleYou : s.bubbleThem}`}>
                <div className={s.who}>{line.who}</div>
                {line.text}
              </div>
            ))}
            {c.transcript.length === 0 && <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>Ringing…</div>}
          </div>
          <div className={s.callFoot}>
            <Button size="sm" aria-pressed={muted} onClick={() => setMuted((m) => !m)}>
              {muted ? 'Unmute' : 'Mute'}
            </Button>
            <span style={{ fontSize: 11.5, color: 'var(--muted)' }}>Recording on · transcript saves to the ticket</span>
            <span style={{ flex: 1 }} />
            <Button variant="danger" onClick={() => end.mutate(undefined)} loading={end.isPending}>
              End call
            </Button>
          </div>
        </>
      )}

      {c?.state === 'wrap' && (
        <>
          <div style={{ padding: '14px 16px', display: 'grid', gap: 12 }}>
            <div>
              <Eyebrow>AI wrap-up</Eyebrow>
              <p style={{ fontSize: 13, lineHeight: 1.55, marginTop: 6 }}>{c.summary}</p>
            </div>
            {c.updates.length > 0 && (
              <div>
                <Eyebrow>Will be added to the ticket</Eyebrow>
                <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 12.5, lineHeight: 1.6 }}>
                  {c.updates.map((u, i) => (
                    <li key={i}>{u}</li>
                  ))}
                </ul>
              </div>
            )}
            {c.recording && (
              <div className="mono" style={{ fontSize: 11, color: 'var(--muted)' }}>
                Recording {c.recording} · {mmss(c.durationSec)}
              </div>
            )}
          </div>
          <div className={s.callFoot}>
            <Button onClick={() => save.mutate(true, { onSuccess: onClose })} loading={save.isPending && save.variables === true}>
              Discard
            </Button>
            <span style={{ flex: 1 }} />
            <Button variant="primary" onClick={() => save.mutate(false, { onSuccess: onClose })} loading={save.isPending && save.variables === false}>
              Save to ticket
            </Button>
          </div>
        </>
      )}

      {confirmEnd && (
        <div role="alertdialog" aria-label="Leave the call?" style={{ padding: '12px 16px', borderTop: '1px solid var(--line-soft)', background: 'var(--warn-bg)', display: 'flex', gap: 8, alignItems: 'center', fontSize: 12.5 }}>
          <span style={{ flex: 1, color: 'var(--warn-strong)' }}>{live ? 'The call is still live. End it first?' : 'Save or discard the call before closing.'}</span>
          <Button size="sm" onClick={() => setConfirmEnd(false)}>
            Stay
          </Button>
          {live && (
            <Button
              size="sm"
              variant="danger"
              onClick={() => {
                setConfirmEnd(false);
                end.mutate(undefined);
              }}
            >
              End call
            </Button>
          )}
        </div>
      )}
    </Modal>
  );
}
