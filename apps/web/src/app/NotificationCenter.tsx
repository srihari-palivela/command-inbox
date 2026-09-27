import { api } from '../lib/api';
import { ago } from '../lib/format';
import { keys, useAction, useLearning } from '../lib/queries';
import { Popover } from '../ui';
import { useUi } from './ui-context';

export function NotificationCenter({ open, onClose, canSend }: { open: boolean; onClose: () => void; canSend: boolean }) {
  const ui = useUi();
  const learning = useLearning();
  const markRead = useAction((v: { ids?: string[]; all?: boolean }) => api.post('/v1/notifications/read', v), { invalidate: [keys.learning, keys.me] });
  const items = learning.data?.notifications ?? [];

  return (
    <Popover open={open} onClose={onClose} label="Notifications" style={{ top: 42, right: 90, width: 384, maxHeight: 540, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '12px 14px', borderBottom: '1px solid var(--line-soft)' }}>
        <h3 style={{ fontSize: 13, fontWeight: 600 }}>Notifications</h3>
        <button type="button" onClick={() => markRead.mutate({ all: true })} style={{ marginLeft: 'auto', border: 0, background: 'transparent', color: 'var(--accent)', fontSize: 11.5, padding: 0 }}>
          Mark all read
        </button>
        {canSend && (
          <button
            type="button"
            onClick={() => {
              onClose();
              ui.openCompose();
            }}
            style={{ border: '1px solid var(--line)', background: 'var(--surface)', color: 'var(--text-2)', borderRadius: 6, padding: '4px 10px', fontSize: 11.5, fontWeight: 500 }}
          >
            + Send update
          </button>
        )}
      </div>
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {items.length === 0 && <div style={{ padding: 26, textAlign: 'center', color: 'var(--muted)', fontSize: 12.5 }}>You are all caught up.</div>}
        {items.map((n) => (
          <div key={n.id} style={{ padding: '12px 14px', borderBottom: '1px solid #f4f3ef' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 4 }}>
              <span aria-hidden style={{ width: 6, height: 6, borderRadius: '50%', background: n.read ? 'transparent' : n.urgent ? 'var(--bad)' : 'var(--accent)', flex: '0 0 auto' }} />
              <span style={{ fontSize: 12.5, fontWeight: n.read ? 500 : 600, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n.title}</span>
              <span className="mono" style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--muted)', flex: '0 0 auto' }}>
                {ago(n.at)}
              </span>
            </div>
            <div style={{ fontSize: 12, lineHeight: 1.5, color: 'var(--muted)', marginBottom: 8 }}>{n.body}</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
              <span style={{ fontSize: 10, fontWeight: 600, borderRadius: 3, padding: '1px 6px', color: n.kind === 'learning' ? 'var(--accent)' : 'var(--text-2)', background: n.kind === 'learning' ? 'var(--accent-bg)' : 'var(--surface-3)' }}>
                {n.kind === 'learning' ? 'Learning card' : 'Message'}
              </span>
              <span style={{ fontSize: 10.5, color: 'var(--muted)' }}>{n.source}</span>
              {(n.kind === 'learning' && n.courseId) || !n.read ? (
                <button
                  type="button"
                  onClick={() => {
                    markRead.mutate({ ids: [n.id] });
                    if (n.kind === 'learning' && n.courseId) {
                      onClose();
                      ui.openLearn(n.courseId);
                    }
                  }}
                  style={{ marginLeft: 'auto', border: '1px solid #e4e4f8', background: 'var(--accent-bg-2)', color: 'var(--accent)', borderRadius: 5, padding: '3px 9px', fontSize: 11, fontWeight: 500, whiteSpace: 'nowrap' }}
                >
                  {n.kind === 'learning' ? 'Start · quiz at the end' : 'Mark read'}
                </button>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </Popover>
  );
}
