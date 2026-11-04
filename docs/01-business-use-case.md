# Business Use Case — Agentic SME Credit Underwriting ("UW-Agent")

## 1. Context
A mid-size bank/NBFC underwrites small-business (SME) working-capital loans of 5 lakh–5 crore (≈ USD 6K–600K). Today an underwriter assembles each case by hand across five systems, reads internal policy PDFs, and writes a credit memo. Typical turnaround is **5–7 working days**, of which **~60 % is data gathering and reconciliation**, not judgement.

## 2. Problem statement
| Pain | Impact |
|---|---|
| Manual KYC/AML sanctions look-ups | Delays, inconsistent screening |
| Bank-statement review done in spreadsheets | Analyst time, errors |
| Credit policy lives in long documents | Inconsistent interpretation between underwriters |
| Credit memos written from scratch | Poor explainability, audit gaps |
| No institutional memory of prior reviews of the same applicant | Repeated work, missed signals |
| No structured record of *why* a human overrode a recommendation | Cannot learn from or audit judgement |
| Reviews can sit for days with nobody nudged, escalated or accountable | Missed SLAs, stale cases, customers waiting, no audit trail of who was told what and when |

## 3. Proposed solution
An **agentic** that receives a loan application and autonomously:
1. Screens the applicant against watchlists / checks KYC status (**Tool 1 – Lambda**).
2. Pulls the bureau score from the external credit bureau (**Tool 2 – REST API, OAuth-protected**).
3. Analyses 6–12 months of bank transactions and computes cash-flow and DSCR metrics (**Tool 3 – Lambda**).
4. Retrieves the relevant clauses of credit policy, AML policy and risk appetite (**Knowledge Base on S3**).
5. Reasons over the evidence, assigns a risk band, a **confidence score** and a **cited credit memo and recommendation**.
6. Decides — using deterministic rules, not the model — whether the case may proceed straight through or must go to a **human reviewer** (section 4).
7. Records the final decision with a full audit trail (**Tool 4 – Lambda → DynamoDB**), and remembers case history, including the reviewer's reasoning, for the next review (**AgentCore Memory**).
8. While a case waits for a person, a **durable approval workflow (AWS Step Functions)** keeps the clock: it reminds, escalates and finally expires the wait, and publishes every step as an **EventBridge event** for notifications, audit and downstream systems (section 4.7).

### 3.1 Autonomy model
| Level | Behaviour | Used for |
|---|---|---|
| L0 | Agent gathers data only, stops | Sanctions potential-match or screening unavailable (→ Compliance) |
| L1 | Agent recommends, **human decides** | Every decline, MEDIUM/HIGH risk, tickets above threshold, confidence below the auto bar |
| L2 | Agent decides, **human samples post-hoc** | APPROVE, LOW risk, ticket ≤ 250k, confidence ≥ 0.85 |
| — | Hard limits enforced by **AgentCore Policy** (Cedar) | No approval above 1,000,000; no approval of HIGH-risk cases, whatever the model outputs |

## 4. Human-in-the-loop operating model

### 4.1 Principles
1. **A person is accountable for every adverse or critical decision.** The agent can approve routine, low-risk cases; it can never finalise a decline.
2. **The agent never silently guesses.** Missing, conflicting or borderline evidence lowers its confidence and routes the case to a person, with the reasons shown.
3. **Confidence is earned from evidence, not self-reported.** The model's own confidence is only an input; deterministic checks (data completeness, borderline metrics, policy conflicts) are subtracted from it.
4. **Every human touch becomes data.** Reviewers give structured reasons, and these feed evaluation, calibration and memory.
5. **Two sets of eyes.** The person who submitted a case cannot decide it.

### 4.2 When a human is involved
| Trigger | Route | Reviewer | Target response |
|---|---|---|---|
| Potential sanctions match, or screening service unavailable | Workflow stops, case **referred** to Compliance; alert raised | Compliance officer | immediate alert |
| Ticket above the bank-wide hard cap (1,000,000) | **COMMITTEE** queue | Credit committee | 72 h |
| Agent recommends DECLINE or REFER | Standard/Senior queue | Underwriter | 24 h / 8 h |
| Risk band HIGH, or confidence < 0.60 | **SENIOR** queue | Senior underwriter | 8 h |
| Confidence 0.60–0.85, MEDIUM risk, or ticket above 250k | **STANDARD** queue | Underwriter | 24 h |
| APPROVE + LOW risk + ≤ 250k + confidence ≥ 0.85 | **AUTO** (straight-through) | none up-front | — |
| Random sample of AUTO approvals (10 % in production) | **QA** queue, non-blocking | Underwriter | 72 h |

### 4.3 What lowers confidence (shown to the reviewer in plain language)
Tool failure or timeout · bureau score or bank data missing · fewer than 6 months of transactions · no policy excerpts found · memo cites no policy · KYC not fully clear · DSCR within 10 % of the 1.25 floor · bureau score within 20 points of the 650 floor · a recommendation that contradicts a policy threshold.

### 4.4 What a reviewer can do
| Action | Effect |
|---|---|
| **Approve / Decline** | Decision recorded. Must select at least one **reason code** (e.g. `DSCR_TOO_LOW`, `STRONG_MITIGANTS`, `AGENT_MISREAD_EVIDENCE`) and may add a comment. |
| **Request info** | Reviewer states what is missing or what the agent should consider; the agent re-assesses with that input and returns for review. After 2 rounds the case is referred to the next level rather than looping. |
| *(blocked)* Decide own case | Four-eyes control rejects it and raises a metric. |

### 4.5 The feedback loop
```
Reviewer decision + reason codes ──► feedback table ──┬─► Agent memory (next review of this applicant starts informed)
                                                      ├─► Evaluation set (labelled by humans, reviewed by Model Risk)
                                                      ├─► Confidence calibration (is "0.9" really right 9 times in 10?)
                                                      ├─► Policy / knowledge-base fixes (recurring reason codes = unclear policy)
                                                      └─► Prompt and routing-rule changes (via pull request, never automatic)
```
**Governance:** nothing in production changes itself. Thresholds, prompts and policy documents are changed through reviewed pull requests, backed by the agreement data, and must pass the regression evaluation before release. Model Risk reviews calibration monthly.

### 4.6 Guarding against reviewer over-reliance
Automation bias (reviewers rubber-stamping the agent) is as risky as agent error. Controls: mandatory reason codes; QA sampling of auto-approvals; tracking of approve-without-comment rate and time-to-decision per reviewer; periodic "blind" cases where the recommendation is hidden; overrides of the agent are visible and trended rather than discouraged.

### 4.7 Long-running approvals: waiting, reminders, escalation, expiry

#### Principles
1. **A waiting case is never forgotten.** Every review that blocks a decision has a timer owned by a workflow, not by a person's memory or a polling job.
2. **Silence never approves or declines.** If nobody answers, the case is escalated, and at the end *referred*. The workflow has no authority to decide.
3. **Everyone is told through events.** Each step (requested, reminder, escalation, decision, expiry) is a business event that people, chat tools and other systems can subscribe to without changing the agent.
4. **The decision stays with accountable humans.** Reviewer authorisation, four-eyes and reason codes are unchanged; the workflow only waits and nudges.

#### Escalation ladder (default; waits scale with the tier's SLA)
| Step | When (STANDARD, 24 h SLA) | What happens | Who is told |
|---|---|---|---|
| Requested | t = 0 | Task in queue, workflow starts | Reviewer distribution list |
| Reminder 1 | 50 % of SLA (12 h) | Nudge | Reviewers |
| **Escalation L1** | SLA breached (24 h) | Tier raised (STANDARD → SENIOR), priority 1 | Tier lead |
| Reminder 2 | + 50 % of next tier's SLA (4 h) | Nudge | Escalated tier |
| **Escalation L2** | + 4 h | Highest urgency | Head of credit (page) |
| **Expiry** | + 24 h grace (≈ 68 h total) | Case closed as **REFERRED**, alert raised | Head of credit, on-call |

A reviewer decision at any point ends the wait immediately. COMMITTEE cases (72 h SLA) follow the same ladder with longer waits. QA samples are non-blocking and do not use the workflow.

#### What a reviewer or auditor sees
A per-case **review timeline**: requested, each reminder, each escalation (who was notified), the decision or expiry. The queue shows escalation level and the tier the task started in.

#### Rules
* Expiry never approves or declines. It records **REFERRED** with decider `system:sla-expiry`, raises an alert, and does not count as reviewer feedback (it would distort agreement and override metrics).
* A decision that arrives in the same instant as expiry wins; a late decision is counted (`ReviewLateDecisions`) for review.
* If the workflow cannot start, the case still enters the queue and a monitor fallback escalates it, with an alarm.
* Workflow events are retained and replayable for 90 days.

## 5. Personas
- **Relationship Manager** – submits application, tracks status.
- **Credit Underwriter / Senior Underwriter** – works the review queue, approves, declines or asks for more information (group `underwriters`).
- **Credit committee** – decisions above the hard cap.
- **Compliance Officer** – receives sanctions escalations.
- **Model-Risk / Internal Audit** – reads traces, calibration, evaluations and the decision log.
- **Tier lead / Head of credit** – receives escalations and expiry alerts; reassigns or decides.
- **Platform / SRE** – runs the service from dashboards and alarms.

## 6. Key user stories
1. As an RM I submit an application and see status, evidence and recommendation within minutes.
2. As an underwriter I open my queue ordered by SLA, see why the agent wasn't confident, see every number and policy clause it used, and respond with a structured decision.
3. As an underwriter I can send the case back with "here is what you missed" and see a revised recommendation.
4. As compliance I am never bypassed: a potential sanctions match always stops the workflow and alerts me.
5. As audit I can reconstruct any decision: inputs, tool calls, model and prompt version, policy version, confidence, reviewer, reason codes, trace id.
6. As model risk I can see whether the agent's confidence is trustworthy, and whether humans are overriding it more over time.
7. As an on-call engineer I am alerted before review SLAs are breached or tools fail, and I can follow one case end-to-end.

8. As a reviewer I get a reminder before my SLA runs out, so nothing expires by accident.
9. As a tier lead I am told automatically when a case breaches its SLA, and the case moves to the right queue.
10. As audit I can see, for any slow case, every reminder and escalation with timestamps, and that expiry only ever referred the case.
11. As compliance operations I can subscribe my case-management system to referral events without changing the agent.

## 7. Success metrics (targets – to be validated in pilot)
| KPI | Baseline | Target |
|---|---|---|
| Time-to-recommendation | 5–7 days | < 15 min |
| Underwriter touch-time / case | ~4 h | ≤ 1 h |
| Straight-through (AUTO) share of in-appetite cases | 0 % | 30–40 % |
| Policy-citation coverage in memo | n/a | 100 % |
| Agreement with senior-underwriter decision (golden set) | n/a | ≥ 90 % |
| Unauthorised-action incidents | n/a | 0 |
| **Review SLA met** (all tiers) | n/a | ≥ 95 % |
| **Agreement rate, high-confidence cases** | n/a | ≥ 90 % (below 85 % raises an alert) |
| **Override rate (human reverses agent)** | n/a | < 15 %, trended weekly |
| **Post-hoc QA defect rate on AUTO approvals** | n/a | < 2 % |
| **Cases sent back for more information** | n/a | < 20 %, falling as data collection improves |
| **Alert-to-acknowledge time** (platform incidents) | n/a | < 15 min |
| **Reviews answered after first reminder** | n/a | tracked; falling reminder count per case |
| **Reviews reaching escalation L2** | n/a | < 3 % |
| **Reviews expired unanswered** | n/a | < 0.5 %; every one root-caused |
| **Tasks without a running workflow** | n/a | 0 (alarm on first) |
| **Approvals/declines made by the workflow** | n/a | 0 (by construction) |

## 8. Risks and controls
| Risk | Control |
|---|---|
| Hallucinated figures | Numbers only from tool outputs; structured-output schema; golden-set regression evaluation |
| Over-confident wrong answers | Confidence = model score minus deterministic penalties; calibration tracked by bucket; alert if high-confidence cases are corrected |
| Biased / unfair outcome | Protected attributes never passed to the model; Guardrail blocks their inference; declines always human-reviewed; fairness review of overrides by segment |
| Excessive agent authority | Cedar policy at the Gateway; HITL interrupt in LangGraph; four-eyes; least-privilege IAM |
| Reviewer rubber-stamping | Reason codes, QA sampling, blind cases, reviewer analytics |
| Review backlog / missed SLA | Queue-depth and oldest-item alarms; automatic SLA escalation notice; tiered priorities |
| PII leakage | Bedrock Guardrails, KMS encryption, IDs-only logging, no free text in metrics |
| Silent degradation (model/prompt/data drift) | Metrics on routing mix, confidence, override rate and token use with anomaly alarms; regression evals gate every deployment |
| Regulatory (model-risk, explainability, audit) | Immutable audit items with model, prompt and policy versions and trace id; retained reviewer rationale |
| Case stalls because nobody responds | Step Functions timers: reminders, two escalation levels, expiry to REFERRED; alarms on L2 and expiry |
| Workflow abused to decide cases | Workflow principal can only send EXPIRE; Cedar lets it record only REFERRED; no approve/decline state exists |
| Lost or duplicated notifications | EventBridge archive and dead-letter queue; idempotent consumers; timeline rebuilt from events |
| Vendor outage | Tool timeouts and retries; failure lowers confidence and routes to a human instead of guessing |

## 9. Scope
**In:** SME working-capital loans, single applicant entity, sandbox bureau, review queue with tiers, structured feedback, dashboards and alerts. **Out (phase 2):** reviewer claim/reassignment and deadline-extension requests, Slack/Teams interactive approvals, collateral valuation, multi-guarantor structures, pricing/term-sheet generation, collections, compliance case-management integration, automatic threshold tuning.
