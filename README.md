# UW-Agent — Agentic SME Credit Underwriting on AWS (Amazon Bedrock AgentCore + LangGraph)

* Business case: [`docs/01-business-use-case.md`](docs/01-business-use-case.md)
* Design: [`docs/02-design-document.md`](docs/02-design-document.md)

```
.github/workflows/   ci.yml · deploy.yml · _deploy-env.yml (OIDC, no static AWS keys)
backend/
  agent/             LangGraph agent for AgentCore Runtime (Dockerfile, app/, tests/)
  tools/             kyc_screening · cashflow_analyzer · decision_recorder · review_queue (Gateway Lambda tools)
                     credit_bureau_mock (REST backend) · review_monitor (scheduled queue/SLA/feedback metrics)
                     approval_actions · workflow_resume · event_recorder (approval workflow workers, not Gateway tools)
  bff/               Lambda behind API Gateway HTTP API; forwards user JWT to Runtime
frontend/            React + TS (Vite), Cognito Hosted UI (auth-code + PKCE)
infra/terraform/     root stack + modules: kms, cognito, dynamodb, knowledge_base, tool_lambdas,
                     credit_bureau_api, guardrail, ecr, agentcore, approval_workflow, bff, frontend, observability
policies/            Cedar policies for AgentCore Policy
kb_docs/             Synthetic credit/AML/risk policy docs -> S3 -> Bedrock Knowledge Base
scripts/             bootstrap_state.sh · seed_data.py · apply_policies.py · export_feedback_to_golden.py
```

## First deployment
1. `scripts/bootstrap_state.sh us-east-1` → note the state bucket.
2. Create an IAM role trusted by GitHub OIDC; add repo secrets `AWS_DEPLOY_ROLE_ARN`, `TF_STATE_BUCKET`; create GitHub environments `dev`, `prod` (add required reviewers on `prod`).
3. Edit `infra/terraform/envs/*.tfvars` (`bedrock_model_id` must be enabled in your account/region).
4. Push to `main` → dev deploys. Open the CloudFront URL from the job log.
5. Create a Cognito user, add to group `underwriters`, sign in.

Try the demo applicants: **APP-1001** (clean → human approval or auto), **APP-1002** (thin cash flow), **APP-1003** (sanctions hit → compliance referral), **APP-1004** (KYC pending).

Docs: `docs/01-business-use-case.md` (why), `docs/02-design-document.md` (how), `docs/03-code-guide.md` (what each file is for).

## Long-running approvals (Step Functions + EventBridge)
* Every blocking review starts a Step Functions workflow that waits for the reviewer, sends reminders, escalates twice (tier lead, head of credit) and finally **expires the case as REFERRED**; it can never approve or decline. See design doc 5.8 and use case 4.7.
* Lifecycle events (`ReviewRequested`, `ReviewReminder`, `ReviewEscalated`, `ReviewCompleted`, `ReviewExpired`, `ComplianceReferred`) go to an EventBridge bus: SNS notifications, a per-case timeline (`GET /reviews/{id}/timeline`), a 90-day archive and a dead-letter queue.
* Dev uses `workflow_time_scale = 0.01`: submit a case that needs review, do nothing, and watch reminder, escalation and expiry in about an hour. Set `reviewer_notify_email` / `alarm_email` to receive the notifications.
* New Lambdas: `approval_actions`, `workflow_resume`, `event_recorder`. New Cedar policies 06/07 and Cognito scope `underwriting/system.resume`.

## Human review and observability
* Cases the agent is not confident about (or that are declines, HIGH risk, above 250k, or above the 1M hard cap) go to a tiered review queue with SLAs. Reviewers approve, decline or request more information, with mandatory reason codes; submitters cannot decide their own case (`enforce_four_eyes`).
* Reviewer feedback is stored in the `feedback` table and feeds memory, the golden eval set and calibration. See design doc sections 5 and 6.
* After deploy open the CloudWatch dashboard `<prefix>-overview` (link printed by the pipeline). Logs are JSON; use the saved Logs Insights query to follow a case by `application_id`.
* Run the regression evals yourself: `cd backend/agent && python -m evals.run_evals` (needs AWS credentials, `BEDROCK_MODEL_ID`, `KNOWLEDGE_BASE_ID`).

## Local
`make install && make test` · `cd frontend && cp .env.example .env.local && npm run dev`
Set `CHECKPOINTER=memory` to run the graph without AgentCore Memory.

## Verify before production (new APIs)
Terraform AgentCore resources, Cedar policy APIs and the `AgentCoreMemorySaver` are recent: run `terraform validate`, check block names against your provider version, and confirm AgentCore/model availability in your region.
