# Model inventory (model risk management, SR 11-7 style)

| Model | Role | Where it runs | Output use | Validation | Monitoring |
|---|---|---|---|---|---|
| System 1 decision engine (open-weight, e.g. Qwen3-4B on vLLM/llama.cpp) | Categorises mail, answers hard-stop questions, scores priority, from log-probabilities over lettered options | The bank's stack | Routing and lanes (lane decided by deterministic policy code, never by a model) | Per deployment version: eval run on a frozen labelled dataset; gates: hard-stop recall 1.0, macro-F1, accuracy, ECE, selective accuracy, coverage; calibration (temperature) and conformal sets fitted on the calibration split | Monitoring screen: escalation rate, fallbacks; eval re-runs on changes |
| System 2 (Anthropic or OpenAI, per node) | Adjudicates when System 1 is unsure (among its candidates only), extracts fields, drafts replies from approved sources, briefs people | Provider API (masked input) | A draft or brief for a person; never sent without approval | Eval runs score adjudication and end-to-end accuracy and grounded-draft rate; provider comparisons on the same dataset | Fallback rate, cost, latency per node and version; edit distance and rejection reasons of drafts |
| Embedding model (bge-m3 or equivalent) | Retrieval of approved knowledge | The bank's stack | Which passages a draft may cite | Retrieval eval: recall@5 ≥ 0.85 and MRR on a labelled question set | Monitoring: most-cited documents, gaps |
| Heuristic fallbacks | Deterministic stand-ins when a model is unavailable | In process | Mail goes to a person when used | Unit tests | Fallback counters and alerts |

**Change control:** a model or prompt change is a deployment configuration change: new draft → evals on the
exact configuration hash → four-eyes publish → shadow → canary → published; rollback is one step.

**Limitations (documented to users):** drafts may be wrong or incomplete and are always reviewed; the AI
cannot act in bank systems (D5); unknown senders are never matched to customers by the model.
