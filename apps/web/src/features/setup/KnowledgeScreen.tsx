import { useState } from 'react';
import { useKnowledge, useMe } from '../../lib/queries';
import { Button, Loadable, Page, PageHeader, Skeleton } from '../../ui';
import s from './knowledge/Knowledge.module.css';
import { GapsCard, HonestCard, ReadinessCard, SourcesCard } from './knowledge/sections';
import { ConnectSourceModal, UploadModal } from './knowledge/SourceModals';

const NO_EDIT = 'Only an Admin can connect a knowledge source.';

export default function KnowledgeScreen() {
  const q = useKnowledge();
  const me = useMe().data;
  const canEdit = !!me?.capabilities.includes('setup.edit');
  const [modal, setModal] = useState<'connect' | 'upload' | null>(null);

  return (
    <Page>
      <PageHeader
        title="What the AI knows"
        subtitle="It may only quote content a knowledge manager has approved. Where that is missing, it says so instead of guessing."
        actions={
          <>
            <Button
              disabled={!canEdit}
              title={canEdit ? undefined : NO_EDIT}
              onClick={() => setModal('upload')}
            >
              Upload documents
            </Button>
            <Button
              variant="dark"
              disabled={!canEdit}
              title={canEdit ? undefined : NO_EDIT}
              onClick={() => setModal('connect')}
            >
              + Connect a source
            </Button>
          </>
        }
      />
      <Loadable
        query={q}
        skeleton={
          <div style={{ display: 'grid', gap: 13 }}>
            <Skeleton h={190} />
            <div className={s.grid} style={{ marginTop: 0 }}>
              <Skeleton h={320} />
              <Skeleton h={480} />
            </div>
          </div>
        }
      >
        {(k) => (
          <>
            <SourcesCard sources={k.sources} canEdit={canEdit} />
            <div className={s.grid}>
              <div className={s.stack}>
                <ReadinessCard readiness={k.readiness} />
                <HonestCard honest={k.honest} />
              </div>
              <GapsCard gaps={k.gaps} open={k.honest.openGaps} canEdit={canEdit} />
            </div>
          </>
        )}
      </Loadable>
      <ConnectSourceModal open={modal === 'connect'} onClose={() => setModal(null)} />
      <UploadModal open={modal === 'upload'} onClose={() => setModal(null)} />
    </Page>
  );
}
