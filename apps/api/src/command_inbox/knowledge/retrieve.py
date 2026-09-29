"""Hybrid retrieval over approved, in-effect knowledge.

1. Full text (Postgres, English stemming; any of the message's meaningful terms, ranked by density) and
   vector similarity (pgvector cosine, HNSW) each return their top 40 chunks.
2. The two rankings are fused with reciprocal-rank fusion (k = 60). Chunks of the ticket's own department
   get a small boost; other departments' chunks stay eligible (a fee policy answers many teams' mail).
3. An optional cross-encoder reranker (Text Embeddings Inference `/rerank`) reorders the top 20.
4. When nothing matches in text and the best vector similarity is below the floor, the answer is "no source":
   the drafter says so and a knowledge gap is raised, instead of citing something unrelated.

Only chunks of documents that are `approved`, already effective and not expired are ever returned; the query
runs inside the tenant's transaction, so row-level security applies as well.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx
from pgvector.sqlalchemy import Vector
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.config import settings
from command_inbox.db.models import EMBEDDING_DIM
from command_inbox.knowledge.embed import embedder

RRF_K = 60
CANDIDATES = 40
DEPARTMENT_BOOST = 0.5 / (RRF_K + 1)

_ELIGIBLE = """d.org_id = :org and d.status = 'approved'
    and (d.effective_from is null or d.effective_from <= now())
    and (d.expires_at is null or d.expires_at > now())"""

_SQL = text(
    f"""
    with q as (select to_tsquery('english', :terms) as tq),
    fts as (
      select c.id, row_number() over (order by ts_rank_cd(c.tsv, q.tq) desc) as r
        from knowledge_chunks c join knowledge_docs d on d.org_id = c.org_id and d.id = c.doc_id, q
       where c.org_id = :org and c.tsv @@ q.tq and {_ELIGIBLE}
       order by ts_rank_cd(c.tsv, q.tq) desc
       limit {CANDIDATES}
    ),
    vec as (
      select c.id, row_number() over (order by c.embedding <=> :vec) as r, 1 - (c.embedding <=> :vec) as sim
        from knowledge_chunks c join knowledge_docs d on d.org_id = c.org_id and d.id = c.doc_id
       where c.org_id = :org and c.embedding is not null and {_ELIGIBLE}
       order by c.embedding <=> :vec
       limit {CANDIDATES}
    ),
    fused as (
      select id, sum(1.0 / ({RRF_K} + r)) as score, max(sim) as sim, bool_or(src = 'fts') as text_hit
        from (select id, r, null::float8 as sim, 'fts' as src from fts
              union all select id, r, sim, 'vec' from vec) x
       group by id
    )
    select c.id::text as chunk_id, d.id::text as doc_id, d.title, c.section_path, c.page, c.text,
           d.department_id::text as department_id, d.owner, d.approved_at, d.verified_at, d.version,
           f.score + case when cast(:dept as text) is not null and d.department_id::text = cast(:dept as text) then {DEPARTMENT_BOOST} else 0 end
             as score,
           coalesce(f.sim, 0) as sim, f.text_hit
      from fused f
      join knowledge_chunks c on c.id = f.id
      join knowledge_docs d on d.org_id = c.org_id and d.id = c.doc_id
     order by score desc
     limit :limit
    """  # noqa: S608 - only module constants are interpolated; every input is a bound parameter
).bindparams(bindparam("vec", type_=Vector(EMBEDDING_DIM)))


@dataclass(frozen=True, slots=True)
class Hit:
    chunk_id: str
    doc_id: str
    title: str
    section: str
    page: int | None
    text: str
    department_id: str | None
    owner: str
    verified_at: Any
    version: int
    score: float
    similarity: float
    text_hit: bool


_TERM = re.compile(r"[a-z0-9]{3,}")


def or_query(text: str, limit: int = 48) -> str:
    """A whole email as a full-text query: any of its content words (OR), the ranking does the rest.
    Terms are plain lower-case alphanumerics, so the tsquery syntax cannot be injected."""
    from command_inbox.knowledge.grounding import STOP

    seen: list[str] = []
    for w in _TERM.findall(text.lower()):
        if w not in STOP and w not in seen:
            seen.append(w)
        if len(seen) >= limit:
            break
    return " | ".join(seen)


async def _rerank(query: str, hits: list[Hit]) -> list[Hit]:
    if not settings.rerank_url or len(hits) < 2:
        return hits
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            settings.rerank_url.rstrip("/") + "/rerank",
            json={"query": query, "texts": [h.text for h in hits]},
        )
        r.raise_for_status()
    order = sorted(r.json(), key=lambda x: -float(x["score"]))
    return [hits[int(x["index"])] for x in order]


async def retrieve(
    tx: AsyncSession, org_id: str, query: str, *, department_id: str | None = None, k: int = 6
) -> list[Hit]:
    query = " ".join(query.split())[:2000]
    terms = or_query(query)
    if not query or not terms:
        return []
    [vec] = await embedder().embed([query])
    rows = (
        (
            await tx.execute(
                _SQL, {"org": org_id, "terms": terms, "vec": vec, "dept": department_id, "limit": max(k, 20)}
            )
        )
        .mappings()
        .all()
    )
    hits = [
        Hit(
            chunk_id=r["chunk_id"],
            doc_id=r["doc_id"],
            title=r["title"],
            section=r["section_path"],
            page=r["page"],
            text=r["text"],
            department_id=r["department_id"],
            owner=r["owner"],
            verified_at=r["approved_at"] or r["verified_at"],
            version=r["version"],
            score=float(r["score"]),
            similarity=float(r["sim"]),
            text_hit=bool(r["text_hit"]),
        )
        for r in rows
    ]
    if (
        not any(h.text_hit for h in hits)
        and max((h.similarity for h in hits), default=0.0) < settings.retrieval_min_similarity
    ):
        return []
    return (await _rerank(query, hits))[:k]
