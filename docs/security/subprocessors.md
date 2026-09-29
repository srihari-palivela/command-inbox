# Sub-processors

Per installation the bank chooses which of these are used; unused ones receive nothing.

| Sub-processor | What it processes | When | Location | Safeguards |
|---|---|---|---|---|
| Cloud hosting (AWS / Azure / GCP) | All stored data (encrypted) | Always | The bank's chosen region | Customer-managed keys (BYOK), private networking |
| Anthropic | Masked mail text and approved knowledge passages for drafting, summaries, second-opinion classification | When allowed by the workspace's model policy | Per agreement (US/global today; regional via Bedrock/Vertex in a later release) | Zero data retention agreement; masking; per-mail and monthly budgets |
| OpenAI | As above | When allowed by the workspace's model policy | Per agreement (regional residency where eligible) | Zero data retention agreement; `store=false`; masking |
| Microsoft / Google | The bank's own mailbox (the bank's existing contract) | When a mailbox is connected | The bank's tenant | Delegated OAuth; least-privilege scopes |
| Email delivery (SES / Postmark, or the bank's relay) | Staff invitations and alerts (names, emails, alert text) | Transactional email | Per provider | TLS; SPF/DKIM/DMARC |

System 1 (classification) and the embedding model run inside the bank's stack; they are not sub-processors.
