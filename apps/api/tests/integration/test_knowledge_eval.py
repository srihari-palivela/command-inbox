"""The retrieval gate on a labelled set: recall@5 >= 0.85 (production plan, Phase 3 exit).

The corpus and questions are synthetic and ship with the tests. With the development (lexical) embedder this
measures the pipeline: parsing, chunking, full text, fusion and filtering. The bank's own gate runs the same
code on its own labelled questions with the real embedding model (`command-inbox-evals retrieval`).
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from tests.integration.admin_support import drain
from tests.integration.test_mail_graph import bank  # noqa: F401

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "knowledge"


async def test_retrieval_recall_at_5_on_the_labelled_set(bank):  # noqa: F811
    from command_inbox.db.engine import tenant_tx
    from command_inbox.knowledge.evaluate import evaluate

    admin, tid, _ = bank
    ids = []
    for f in sorted(FIXTURES.glob("*.md")):
        title = f.read_text().splitlines()[0].lstrip("# ").strip().title()
        r = await admin.http.post(
            "/v1/knowledge/documents",
            files={"file": (f.name, io.BytesIO(f.read_bytes()), "text/markdown")},
            data={"title": title},
            headers={"x-csrf-token": admin.me["csrfToken"]},
        )
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    await drain()
    for doc_id in ids:
        r = await admin.send("POST", f"/v1/knowledge/documents/{doc_id}/approve", None)
        assert r.status_code == 200, r.text

    cases = [
        json.loads(line) for line in (FIXTURES / "questions.jsonl").read_text().splitlines() if line.strip()
    ]
    async with tenant_tx(tid) as tx:
        report = await evaluate(tx, tid, cases, k=5)
    misses = [(m.question, m.top) for m in report.misses()]
    assert report.recall >= 0.85, (report.recall, misses)
    assert report.mrr >= 0.7, (report.mrr, misses)
