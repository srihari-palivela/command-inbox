import { useMe, useTaxonomy } from '../../lib/queries';
import { EmptyState, Loadable, Page, PageHeader, Skeleton } from '../../ui';
import s from './ownership/Ownership.module.css';
import { ContractCard, DepartmentColumn } from './ownership/parts';
import { TeamsAdmin } from './ownership/TeamsAdmin';

export default function OwnershipScreen() {
  const q = useTaxonomy();
  const me = useMe().data;
  const canEdit = !!me?.capabilities.includes('setup.edit');

  return (
    <Page>
      <PageHeader
        title="Who owns what"
        subtitle="Each type of query has exactly one accountable team. Anything unowned stays manual."
        actions={
          q.data && (
            <span className={s.coverage}>
              <span className="mono">{q.data.owned}</span> of <span className="mono">{q.data.total}</span>{' '}
              query types have an owner
            </span>
          )
        }
      />
      <Loadable
        query={q}
        skeleton={
          <div className={s.cols}>
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} h={340} />
            ))}
          </div>
        }
      >
        {(t) => (
          <>
            {t.departments.length === 0 ? (
              <EmptyState
                title="No query types yet"
                text="Query types appear here once the classifier has seen mail for a team."
              />
            ) : (
              <div className={s.cols}>
                {t.departments.map((d, i) => (
                  <DepartmentColumn key={d.id} d={d} index={i} canEdit={canEdit} />
                ))}
              </div>
            )}
            <ContractCard contract={t.contract} />
            {canEdit && <TeamsAdmin />}
          </>
        )}
      </Loadable>
    </Page>
  );
}
