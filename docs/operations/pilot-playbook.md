# Pilot playbook (bank #1)

How we run the pilot at a bank, stage by stage, with the product's own gates as the evidence. Everything here is
visible to the bank under **Administration → Pilot** (`/admin/pilot`) and recorded in the workspace's audit
log.

## Stages

| Stage | What happens | What is sent from Command Inbox |
|---|---|---|
| **Onboarding** | Single sign-on, mailbox, knowledge, a deployment that passes its evaluation, the reply-time baseline. | Nothing |
| **Shadow** (≥ 14 days) | The AI triages and drafts every real mail. The team keeps working in its usual mail client and labels a sample in the labelling queue. | Nothing. Every send is refused with "Nothing is sent from Command Inbox during shadow mode". |
| **Assisted** (≥ 28 days) | The team works in Command Inbox. The AI's drafts go out only after a person approves them. | Replies a person approved |
| **Live** | The pilot's KPIs held. The whole team works this way. | Replies a person approved. The automatic lane stays off in this release (D5). |

**Moving forward:**
1. An admin asks for the next stage, with a reason. The request can only be made when every gate is met, and
   the gates at that moment are stored with it as evidence.
2. A second person signs it off: anyone except the requester, and a named **Risk approver** when the
   workspace has named any (Pilot → Targets, baseline and sign-off). The gates are checked again at sign-off.

**Stepping back** happens at once. Any admin can do it with a reason and no sign-off, because less autonomy is
always allowed. A waiting request is withdrawn when the pilot steps back.

## Gates

Targets are set per workspace. The defaults are the agreed pilot KPIs:

| To | Gate | Default |
|---|---|---|
| Shadow | A deployment passed its evaluation (hard-stop recall 100% is part of the eval gates) | passing run |
| | A mailbox is connected | — |
| | A reply-time baseline is recorded | — |
| Assisted | Days in shadow | ≥ 14 |
| | Mail labelled by people during shadow | ≥ 100 |
| | AI and people agree on the category (labelled mail) | ≥ 85% |
| | AI and people agree on the lane (all triaged mail; a person's label, else the lane after overrides) | ≥ 85% |
| | Hard-stop misses (labelled mail a person marked as a hard stop that the AI did not stop, plus incidents logged as a hard-stop miss) | 0 |
| | P1 incidents in the stage | 0 |
| Live | Days assisted | ≥ 28 |
| | Drafts decided (sent or discarded) | ≥ 200 |
| | Drafts sent unedited or lightly edited (edit distance ≤ 20%) | ≥ 70% |
| | Hard-stop misses | 0 |
| | Share of mail answered on time, compared with the baseline | ≥ baseline |
| | Median first reply, compared with the baseline (when the baseline has it) | ≤ baseline |
| | P1 incidents in the stage | 0 |

A gate reads **Waiting** until there are enough records to judge it: fewer days, labels or drafts than the
target.

## Week by week

**Week 0 (onboarding):**
- Confirm the named contacts and the Risk approver (§16.1 of the plan).
- Record the baseline. Either measure it from records (Pilot → Measure from records, for example 30 days of
  mail handled before the pilot), or enter the bank's own figures.
- Label about 300 historical mails and run the evaluation until the deployment passes.
- Ask for shadow. Risk signs it off.

**Shadow (weeks 1–2+):**
- Leads label at least 100 real mails in the labelling queue (Evals → dataset → Label real mail). Spread them
  across categories.
- Review **AI compared with your people** weekly:
  - lane mix-ups (AI lane against final lane);
  - per-category agreement;
  - every disagreement, starting with hard-stop misses.
- Fix what the review finds in the studio. Examples: category descriptions and examples, hard-stop keywords,
  knowledge gaps. Publish new versions through evals and four-eyes as usual.
- When every gate is met, ask for assisted. Risk signs it off.

**Assisted (weeks 3–6+):**
- The team approves or edits every draft. The discard reasons and edit distance show up in Monitoring and in
  the pilot KPIs.
- Weekly pilot review with the bank's operations lead and Risk:
  - the KPIs;
  - the incident log;
  - Monitoring: deadlines, fallbacks, spend.
- When every gate is met, ask for live. Risk signs it off.

## Incidents

Anyone who works mail can log an incident on the Pilot screen: severity, kind, what happened, and optionally the
ticket number.

| Severity | Meaning | Response |
|---|---|---|
| P1 | Customer harm or regulatory exposure: a hard stop missed and a reply sent, data sent to the wrong person, a wrong commitment made | Admins are alerted at once (in-app alert and email). Step the pilot back until it is understood. Tell the bank's Risk contact the same day. |
| P2 | A serious error caught before it reached the customer; an outage longer than an hour | Fix within the week; review at the weekly pilot review |
| P3 | A wrong category or lane, a poor draft | Batch into the next studio iteration |
| P4 | Cosmetic, a question | As convenient |

A P1 in a stage fails that stage's gate, so the pilot cannot move forward until the stage has run clean. An
incident is closed by an admin, with what was done. Incident history stays in the log and in the audit chain.

## Exit report

At the end of the pilot, share with the bank:
- The Pilot screen's KPIs and each stage decision (who asked, who signed off, the evidence).
- The shadow comparison.
- The incident log.
- A signed audit export covering the pilot's dates (Organisation → Operations).
