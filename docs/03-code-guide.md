# UW-Agent: Code Guide (what each piece is for)

## 1. Reading order
1. `backend/agent/app/graph.py` (the workflow), `state.py` (its data), `confidence.py` (who decides what).
2. `backend/agent/app/nodes/` (each step), `main.py` (the entrypoint and authorization).
3. `backend/tools/*` (the tools and the approval-workflow workers).
4. `infra/terraform/modules/approval_workflow/` (the durable wait), then the other Terraform modules.

## 2. Repository map
```
backend/agent/      the agent (LangGraph) that runs on AgentCore Runtime, its tests and evals
backend/tools/      Lambda functions: Gateway tools, workers for the approval workflow, scheduled monitor
backend/bff/        backend-for-frontend Lambda behind the HTTP API
frontend/           React single-page app for RMs and reviewers
infra/terraform/    all AWS infrastructure, one module per concern
policies/           Cedar rules applied at the AgentCore Gateway
kb_docs/            synthetic policy documents that feed the Knowledge Base
scripts/            operational helpers (state bucket, Cedar apply, seed data, feedback export)
docs/               use case, design, this guide
.github/workflows/  CI and deployment pipelines
```

## 3. Agent (`backend/agent/app`)
| File | Purpose | Must never |
|---|---|---|
| `main.py` | Runtime entrypoint (`/invocations`). Actions `submit`, `review`, `get`. Reads JWT claims, enforces who may do what, enforces four-eyes, resumes paused cases by session id | Trust a client-supplied identity; let anyone but the workflow send EXPIRE |
| `graph.py` | Builds the state graph: edges, parallel evidence fan-out, sanctions gate, the REQUEST_INFO loop, checkpointer choice | Contain business thresholds |
| `state.py` | The shared case state (inputs, evidence, assessment, human step, outcome, append-only `trace`/`errors`) | Hold secrets or applicant free text beyond the case |
| `config.py` | All settings and thresholds from environment variables (caps, confidence bars, floors, QA rate, workflow client id, version labels) | Be read from anywhere else |
| `confidence.py` | Deterministic confidence = model score minus explained penalties; `decide_tier` picks AUTO / STANDARD / SENIOR / COMMITTEE and sets SLA hours and priority | Let the model choose its own autonomy |
| `claims.py` | JWT claim helpers and the **review authorization matrix** (underwriters decide; the workflow may only EXPIRE; humans cannot EXPIRE) | Accept the workflow identity without both client id and scope |
| `llm.py` | Bedrock chat model factory with Guardrail | Use a non-zero temperature |
| `memory.py` | AgentCore Memory: recall prior reviews, remember this one (including reviewer reasoning) | Store protected attributes |
| `mcp_tools.py` | MCP client to AgentCore Gateway; propagates the caller's token; timeouts, retries, latency/error metrics | Use a shared service identity for user actions |
| `observability.py` | JSON logs, trace ids, EMF metrics, the `instrument` wrapper that gives every node timing and error handling (soft nodes degrade, hard nodes fail) | Log personal data |

### Nodes (`nodes/`)
| Node | File | Purpose |
|---|---|---|
| `load_memory` | evidence.py | Prior reviews of this applicant |
| `kyc_screen` | evidence.py | Tool 1: sanctions/KYC; drives the compliance gate |
| `bureau_check` | evidence.py | Tool 2: bureau score (parallel) |
| `cashflow_analysis` | evidence.py | Tool 3: cash flow and DSCR proxy (parallel) |
| `retrieve_policy` | evidence.py | Knowledge Base clauses with source URIs |
| `risk_assessment` | reasoning.py | Model produces a structured assessment from evidence only; code computes confidence and tier. Re-run after REQUEST_INFO |
| `escalate_compliance` | reasoning.py | Sanctions match or outage: stop, REFERRED, publish `ComplianceReferred` (best effort) |
| `auto_decide` | reasoning.py | Straight-through APPROVED; deterministic QA sampling |
| `create_review_task` | reasoning.py | Creates the queue task and starts the durable approval workflow. Separate node because nodes that pause re-run on resume |
| `human_approval` | reasoning.py | Pauses (interrupt) until a reviewer acts; handles APPROVE/DECLINE/REQUEST_INFO; **EXPIRE** (from the workflow) becomes REFERRED and writes no feedback label |
| `record_decision` | reasoning.py | Immutable audit write through the Gateway (Cedar enforces hard limits) |
| `save_memory` | reasoning.py | Outcome and reviewer rationale for next time |

## 4. Tool Lambdas (`backend/tools`)
Each directory has `handler.py` and a copy of `obs.py` (shared logging/metrics helper; a test fails if the copies differ).

| Function | Kind | Purpose |
|---|---|---|
| `kyc_screening` | Gateway tool 1 | KYC status and watchlist check |
| `credit_bureau_mock` | REST backend | Stands in for the external bureau (OAuth-protected) |
| `cashflow_analyzer` | Gateway tool 2 | Cash-flow metrics and DSCR proxy |
| `decision_recorder` | Gateway tool 3 | Idempotent decision write plus audit item (model, prompt, policy versions, trace id) |
| `review_queue` | Gateway tool 4 | TASK_CREATED (task row, **starts the workflow**, `ReviewRequested`), FEEDBACK (label row, closes task, **completes the workflow wait**), EXPIRED, COMPLIANCE_REFERRAL |
| `review_monitor` | Scheduled | Queue and quality metrics; fallback escalation only for tasks without a workflow |
| `approval_actions` | **Step Functions worker** | PLAN (ladder), REGISTER_TOKEN, REMIND, ESCALATE; idempotent |
| `workflow_resume` | **Step Functions worker** | Expire a stalled case: atomic claim, resume the agent as the workflow principal |
| `event_recorder` | **EventBridge consumer** | Writes each event to the per-case timeline; deduplicates |

Workers are not Gateway tools: they have no Gateway target and the agent cannot call them.

## 5. BFF and frontend
| File | Purpose |
|---|---|
| `backend/bff/handler.py` | HTTP API router: forward agent actions with the user's token; list applications and reviews (allow-listed fields only, never the task token); `GET /reviews/{id}/timeline` |
| `frontend/src/main.tsx`, `App.tsx` | Entry point and main screen |
| `frontend/src/auth.ts`, `api.ts` | Cognito sign-in (PKCE); typed BFF client |
| `components/ApplicationForm.tsx` | Submit an application |
| `components/ReviewQueue.tsx` | Work queue by SLA, with tier, original tier and escalation level |
| `components/DecisionPanel.tsx` | Recommendation, memo, confidence reasons, evidence; approve/decline with reason codes or request info |
| `components/ReviewTimeline.tsx` | Reminders, escalations, decision or expiry for a case |
| `components/DecisionsTable.tsx` | Audit list of decisions |

## 6. Approval workflow (the durable wait)
| Piece | Purpose |
|---|---|
| `modules/approval_workflow/asl.json.tftpl` | The state machine: plan, wait for reviewer (task token with timeout), then remind / escalate / expire; publishes events. No state can approve or decline |
| `modules/approval_workflow/main.tf` | Event bus, archive, SNS topics, rules (notify, escalate, timeline, logs), DLQ, the three Lambdas, state machine, alarms |
| `approval_actions.build_schedule` | Defines the ladder; change waits here, not in the state machine |
| Cognito `workflow` client + scope `underwriting/system.resume` | The only identity allowed to EXPIRE |
| Cedar `06`, `07` | The workflow identity can record REFERRED only |

Lifecycle of one review: `TASK_CREATED` → execution starts → `REGISTER_TOKEN` (wait) → reminders/escalations on timeouts → either reviewer decision (`FEEDBACK` calls SendTaskSuccess) or `EXPIRE` (REFERRED). See design section 5.8 for the sequence diagram and race rules.

## 7. Cedar policies (`policies/`)
| File | Effect |
|---|---|
| `01-allow-read-tools` | `agent.invoke` may use read-only evidence tools |
| `02-allow-record-decision` | `decision.write` may record decisions up to the hard cap |
| `03-forbid-over-cap-approval` | Nobody approves above the cap (forbid beats permit) |
| `04-forbid-high-risk-approval` | HIGH risk can never be approved through the tool |
| `05-allow-review-events` | Review events allowed for `agent.invoke` and the workflow identity |
| `06-allow-system-referral` | Workflow identity may record REFERRED, any amount |
| `07-forbid-system-non-referral` | Workflow identity may record nothing else |

## 8. Infrastructure modules (`infra/terraform/modules`)
`kms` (one key for data, logs, secrets, topics, state machine) · `cognito` (users, scopes, SPA and machine clients) · `dynamodb` (decisions, audit, reviews, feedback) · `tool_lambdas` (tool functions, monitor, IAM, alarms) · `credit_bureau_api` (mock bureau REST API) · `knowledge_base` (policy retrieval) · `guardrail` (PII and protected-attribute controls) · `ecr` (agent image) · `agentcore` (Runtime, Gateway and targets, Memory, Policy engine) · **`approval_workflow`** · `bff` (HTTP API) · `frontend` (S3 + CloudFront) · `observability` (dashboard and alarms). Root `main.tf` wires them; computed ARNs for the workflow avoid a dependency cycle.

## 9. Tests and quality gates
| File | Proves |
|---|---|
| `tests/test_routing.py` | KYC gate, confidence penalties, tier routing, hard cap |
| `tests/test_graph_hitl.py` | Auto path, low-confidence pause then human decision, request-info loop and its round limit |
| `tests/test_tools.py` | Tool Lambdas on mocked DynamoDB, shared helper copies identical |
| `tests/test_approval_workflow.py` | Ladder, idempotent reminders/escalations, token and race handling, expiry claim, recorder, ASL structure |
| `tests/test_expiry_and_authz.py` | Who may EXPIRE; expiry refers and writes no feedback |
| `evals/run_evals.py`, `golden_cases.jsonl` | Regression gate with the real model and Knowledge Base |
| `scripts/export_feedback_to_golden.py` | Turns reviewer decisions into candidate golden cases (reviewed by Model Risk) |

## 10. Scripts and pipelines
`scripts/bootstrap_state.sh` (one-time state bucket) · `apply_policies.py` (loads Cedar into the policy engine) · `seed_data.py` (demo data) · `.github/workflows/ci.yml` (PR checks) · `deploy.yml` and `_deploy-env.yml` (dev then prod, with eval gate) · `Makefile` (shortcuts).

## 11. Conventions
* Business rules live in `config.py` and `confidence.py`; the model proposes, code and Cedar dispose.
* Side effects live in their own node or tool; steps are idempotent because retries happen.
* Logs and metrics carry ids only. Task tokens are capabilities: never logged, never sent to a browser.
* A changed threshold, prompt, policy or ladder is a reviewed pull request that passes the evals.
