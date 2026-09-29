import type { KnowledgeKind } from '@ci/contracts';
import { api } from '../../../lib/api';
import { keys, useAction } from '../../../lib/queries';
import { Modal } from '../../../ui';
import s from './Knowledge.module.css';
import { SOURCE_KINDS } from './tones';

const NAME: Record<KnowledgeKind, string> = Object.fromEntries(
  SOURCE_KINDS.map((k) => [k.kind, k.name]),
) as Record<KnowledgeKind, string>;

function useConnect(onDone: () => void) {
  const m = useAction((kind: KnowledgeKind) => api.post('/v1/knowledge/sources', { kind }), {
    invalidate: [keys.knowledge, keys.me],
    success: (_r, kind) => `${NAME[kind]} connected — first sync queued, nothing is citable until approved.`,
  });
  return { ...m, connect: (kind: KnowledgeKind) => m.mutate(kind, { onSuccess: onDone }) };
}

export function ConnectSourceModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const connect = useConnect(onClose);
  return (
    <Modal
      open={open}
      onClose={onClose}
      width={520}
      title="Connect a knowledge source"
      subtitle="Read-only. Everything lands as pending — nothing is citable until a knowledge manager approves it."
    >
      <div className={s.kinds}>
        {SOURCE_KINDS.map((k) => (
          <button
            key={k.kind}
            type="button"
            className={s.kind}
            disabled={connect.isPending}
            onClick={() => connect.connect(k.kind)}
          >
            <span className={`${s.abbr} ${s.abbrLg}`} aria-hidden>
              {k.abbr}
            </span>
            <span style={{ minWidth: 0 }}>
              <span className={s.kindName} style={{ display: 'block' }}>
                {k.name}
              </span>
              <span className={s.kindNote}>{k.note}</span>
            </span>
            <span className={s.kindGo} aria-hidden>
              {connect.isPending && connect.variables === k.kind ? 'Connecting…' : 'Connect →'}
            </span>
          </button>
        ))}
      </div>
    </Modal>
  );
}
