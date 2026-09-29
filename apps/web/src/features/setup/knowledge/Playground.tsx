import type { KnowledgeSearchDTO } from '@ci/contracts';
import { useState, type FormEvent } from 'react';
import { api } from '../../../lib/api';
import { useAction, useTaxonomy } from '../../../lib/queries';
import { Button, Card, TextArea } from '../../../ui';
import { ProblemAlert, Select } from '../../admin/bits';
import s from './Knowledge.module.css';

/**
 * Paste a customer's question and see exactly which approved passages the AI would ground its draft on.
 * An empty result is the honest answer: the draft would say nothing covers it and hand it to a person.
 */
export function RetrievalCard({ onOpen }: { onOpen: (docId: string) => void }) {
  const departments = useTaxonomy().data?.departments ?? [];
  const [query, setQuery] = useState('');
  const [dept, setDept] = useState('');
  const search = useAction((body: { query: string; departmentId: string | null }) =>
    api.post<KnowledgeSearchDTO>('/v1/knowledge/search', body),
  );
  const submit = (e: FormEvent) => {
    e.preventDefault();
    search.mutate({ query: query.trim(), departmentId: dept || null });
  };
  const r = search.data;

  return (
    <Card
      title="Try retrieval"
      meta="Approved, in-force content only"
      className={s.rise}
      style={{ marginTop: 13 }}
    >
      <form onSubmit={submit} style={{ display: 'grid', gap: 8 }}>
        <TextArea
          rows={3}
          aria-label="Customer question"
          placeholder="Paste a customer's email or question…"
          value={query}
          maxLength={2000}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div style={{ display: 'flex', gap: 8 }}>
          <Select aria-label="Department" value={dept} onChange={(e) => setDept(e.target.value)}>
            <option value="">Any department</option>
            {departments.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </Select>
          <Button
            type="submit"
            variant="dark"
            style={{ marginLeft: 'auto' }}
            disabled={query.trim().length < 2}
            loading={search.isPending}
          >
            Find sources
          </Button>
        </div>
      </form>
      <ProblemAlert error={search.error} />
      {r && (
        <div className={s.hits} aria-live="polite">
          {r.hits.length === 0 ? (
            <p className={s.docSub}>
              No approved source answers this. A draft would say so and hand the query to a person.
            </p>
          ) : (
            r.hits.map((h, i) => (
              <button key={h.chunkId} type="button" className={s.hit} onClick={() => onOpen(h.docId)}>
                <div className={s.chunkMeta}>
                  <strong style={{ color: 'var(--text)' }}>
                    {i + 1}. {h.title}
                  </strong>
                  {h.section && ` · ${h.section}`}
                  {h.page != null && ` · page ${h.page}`} · similarity {h.similarity.toFixed(2)}
                  {h.textMatch && ' · keyword match'}
                </div>
                <div className={s.chunkText}>{h.text}</div>
              </button>
            ))
          )}
        </div>
      )}
    </Card>
  );
}
