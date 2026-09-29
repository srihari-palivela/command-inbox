"""Retrieval evaluation: does the right document come back for a question?

A case is a question and the document(s) that answer it (by title or id). We report recall@k (the share of
questions whose answering document is in the top k passages) and MRR (how high it ranks). The gate for a
knowledge base is recall@5 ≥ 0.85 on a labelled set written from the bank's own mail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.knowledge.retrieve import retrieve


@dataclass(slots=True)
class CaseResult:
    question: str
    expected: list[str]
    found_at: int | None  # 1-based rank of the first answering passage, None when missing
    top: list[str]


@dataclass(slots=True)
class RetrievalReport:
    k: int
    recall: float
    mrr: float
    cases: list[CaseResult] = field(default_factory=list)

    def misses(self) -> list[CaseResult]:
        return [c for c in self.cases if c.found_at is None or c.found_at > self.k]


async def evaluate(
    tx: AsyncSession, org_id: str, cases: list[dict[str, Any]], *, k: int = 5
) -> RetrievalReport:
    results: list[CaseResult] = []
    for case in cases:
        expected = [str(e).lower() for e in case["expected"]]
        hits = await retrieve(
            tx, org_id, str(case["question"]), department_id=case.get("departmentId"), k=max(k, 10)
        )
        found = next(
            (
                i + 1
                for i, h in enumerate(hits)
                if h.title.lower() in expected or h.doc_id.lower() in expected
            ),
            None,
        )
        results.append(CaseResult(str(case["question"]), expected, found, [h.title for h in hits[:k]]))
    n = len(results) or 1
    recall = sum(1 for r in results if r.found_at is not None and r.found_at <= k) / n
    mrr = sum(1 / r.found_at for r in results if r.found_at is not None) / n
    return RetrievalReport(k=k, recall=round(recall, 4), mrr=round(mrr, 4), cases=results)
