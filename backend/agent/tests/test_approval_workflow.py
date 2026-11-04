"""Durable approval workflow: schedule ladder, Lambda workers (moto), callback race-safety, ASL structure."""
import importlib.util
import json
import os
import pathlib
import re
import sys

import boto3
import pytest
from moto import mock_aws

ROOT = pathlib.Path(__file__).resolve().parents[2] / "tools"
ASL = pathlib.Path(__file__).resolve().parents[3] / "infra/terraform/modules/approval_workflow/asl.json.tftpl"


def load(name):
    """Import a tool Lambda fresh (module-level boto3 clients must be created inside the moto context)."""
    sys.path.insert(0, str(ROOT / name))
    sys.modules.pop("obs", None)
    spec = importlib.util.spec_from_file_location(f"{name}_mod", ROOT / name / "handler.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.path.remove(str(ROOT / name))
    return mod


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    for k, v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "x",
                 "REVIEWS_TABLE": "reviews", "FEEDBACK_TABLE": "feedback", "EVENT_BUS_NAME": "uw-bus"}.items():
        monkeypatch.setenv(k, v)


def make_tables():
    ddb = boto3.resource("dynamodb")
    ddb.create_table(
        TableName="reviews", BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "application_id", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in ("application_id", "sk", "status", "sla_due")],
        GlobalSecondaryIndexes=[{"IndexName": "status-sla", "Projection": {"ProjectionType": "ALL"},
                                 "KeySchema": [{"AttributeName": "status", "KeyType": "HASH"}, {"AttributeName": "sla_due", "KeyType": "RANGE"}]}])
    ddb.create_table(
        TableName="feedback", BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "application_id", "KeyType": "HASH"}, {"AttributeName": "feedback_ts", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in ("application_id", "feedback_ts")])
    boto3.client("events").create_event_bus(Name="uw-bus")
    return ddb


class FakeSfn:
    def __init__(self):
        self.started, self.success, self.names = [], [], set()

    def start_execution(self, stateMachineArn, name, input):
        from botocore.exceptions import ClientError
        if name in self.names:
            raise ClientError({"Error": {"Code": "ExecutionAlreadyExists", "Message": "x"}}, "StartExecution")
        self.names.add(name)
        self.started.append((name, json.loads(input)))
        return {"executionArn": "arn:exec"}

    def send_task_success(self, taskToken, output):
        self.success.append((taskToken, json.loads(output)))


def task(app="A1", tier="STANDARD", rnd=0, sla=24):
    return {"application_id": app, "tier": tier, "session_id": "sess-12345678", "round": rnd, "sla_hours": sla, "confidence": 0.7}


# ---------------- schedule ladder ----------------
def test_schedule_ladder_ends_in_expire_and_scales():
    with mock_aws():
        aa = load("approval_actions")
        s = aa.build_schedule("STANDARD", 24)
        assert [x["then"] for x in s] == ["REMIND", "ESCALATE", "REMIND", "ESCALATE", "EXPIRE"]
        assert s[0]["wait_seconds"] == 12 * 3600 and s[1]["wait_seconds"] == 12 * 3600       # 50% / 100% of 24h SLA
        assert s[2]["wait_seconds"] == 4 * 3600                                               # next tier (SENIOR) 8h SLA
        assert sum(x["wait_seconds"] for x in s) == (12 + 12 + 4 + 4 + 24) * 3600
        fast = aa.build_schedule("SENIOR", 8, scale=0.001)
        assert all(x["wait_seconds"] >= 1 for x in fast)                                      # Step Functions needs >= 1 s
        assert aa.build_schedule("COMMITTEE", 72)[-1]["then"] == "EXPIRE"


# ---------------- review_queue: start workflow, callback ----------------
@mock_aws
def test_task_created_starts_workflow_once_and_publishes_event(monkeypatch):
    ddb = make_tables()
    rq = load("review_queue")
    fake = FakeSfn()
    monkeypatch.setattr(rq, "_sfn", fake)
    monkeypatch.setattr(rq, "STATE_MACHINE", "arn:sm")
    ev = {"application_id": "A1", "event_type": "TASK_CREATED", "tier": "STANDARD", "sla_hours": 24, "session_id": "sess-12345678", "round": 0}
    assert rq.handler(ev)["workflow_started"] is True
    assert rq.handler(ev)["workflow_started"] is True          # retried tool call: idempotent, still one execution
    assert len(fake.started) == 1 and fake.started[0][1]["task"]["tier"] == "STANDARD"
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,80}", fake.started[0][0])
    row = ddb.Table("reviews").get_item(Key={"application_id": "A1", "sk": "TASK"})["Item"]
    assert row["status"] == "PENDING" and row["escalation_level"] == 0


@mock_aws
def test_qa_task_and_missing_state_machine_do_not_start_workflow(monkeypatch):
    make_tables()
    rq = load("review_queue")
    fake = FakeSfn()
    monkeypatch.setattr(rq, "_sfn", fake)
    monkeypatch.setattr(rq, "STATE_MACHINE", "arn:sm")
    assert rq.handler({"application_id": "Q1", "event_type": "TASK_CREATED", "tier": "QA", "sla_hours": 72})["workflow_started"] is False
    monkeypatch.setattr(rq, "STATE_MACHINE", "")
    assert rq.handler({"application_id": "A2", "event_type": "TASK_CREATED", "tier": "SENIOR"})["workflow_started"] is False
    assert fake.started == []


@mock_aws
def test_workflow_start_failure_never_fails_the_tool(monkeypatch):
    ddb = make_tables()
    rq = load("review_queue")

    class Broken(FakeSfn):
        def start_execution(self, **_):
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "x"}}, "StartExecution")

    monkeypatch.setattr(rq, "_sfn", Broken())
    monkeypatch.setattr(rq, "STATE_MACHINE", "arn:sm")
    out = rq.handler({"application_id": "A3", "event_type": "TASK_CREATED", "tier": "STANDARD"})
    assert out["recorded"] is True and out["workflow_started"] is False          # row exists; monitor alarms on it
    assert ddb.Table("reviews").get_item(Key={"application_id": "A3", "sk": "TASK"})["Item"]["status"] == "PENDING"


@mock_aws
def test_reviewer_decision_completes_the_wait_and_hides_token(monkeypatch):
    ddb = make_tables()
    rq = load("review_queue")
    fake = FakeSfn()
    monkeypatch.setattr(rq, "_sfn", fake)
    monkeypatch.setattr(rq, "STATE_MACHINE", "arn:sm")
    rq.handler({"application_id": "A1", "event_type": "TASK_CREATED", "tier": "STANDARD", "session_id": "sess-12345678"})
    ddb.Table("reviews").update_item(Key={"application_id": "A1", "sk": "TASK"}, UpdateExpression="SET task_token = :t",
                                     ExpressionAttributeValues={":t": "TOKEN-1"})
    out = rq.handler({"application_id": "A1", "event_type": "FEEDBACK", "reviewer": "bob", "agent_recommendation": "APPROVE",
                      "human_decision": "DECLINE", "reason_codes": "DSCR_TOO_LOW", "confidence": 0.7, "tier": "STANDARD"})
    assert out["override"] is True and out["late"] is False
    assert fake.success == [("TOKEN-1", {"action": "DECLINE", "reviewer": "bob", "reason_codes": ["DSCR_TOO_LOW"],
                                          "completed_at": fake.success[0][1]["completed_at"]})]
    row = ddb.Table("reviews").get_item(Key={"application_id": "A1", "sk": "TASK"})["Item"]
    assert row["status"] == "COMPLETED" and "task_token" not in row and row["outcome"]["action"] == "DECLINE"


@mock_aws
def test_expired_is_not_a_human_label(monkeypatch):
    ddb = make_tables()
    rq = load("review_queue")
    monkeypatch.setattr(rq, "STATE_MACHINE", "")
    rq.handler({"application_id": "A1", "event_type": "TASK_CREATED", "tier": "STANDARD"})
    rq.handler({"application_id": "A1", "event_type": "EXPIRED"})
    assert ddb.Table("reviews").get_item(Key={"application_id": "A1", "sk": "TASK"})["Item"]["status"] == "EXPIRED"
    assert ddb.Table("feedback").scan()["Items"] == []        # no label: would poison agreement / override metrics


# ---------------- approval_actions workers ----------------
def _seed(ddb, tier="STANDARD", rnd=0, **extra):
    ddb.Table("reviews").put_item(Item={
        "application_id": "A1", "sk": "TASK", "status": "PENDING", "tier": tier, "review_round": rnd,
        "sla_due": "2020-01-01T00:00:00+00:00", "created_at": "2020-01-01T00:00:00+00:00", "escalation_level": 0, **extra})


@mock_aws
def test_register_token_stores_token_when_pending(monkeypatch):
    ddb = make_tables()
    aa = load("approval_actions")
    fake = FakeSfn()
    monkeypatch.setattr(aa, "_sfn", fake)
    _seed(ddb)
    assert aa.handler({"action": "REGISTER_TOKEN", "task": task(), "token": "T1", "execution_id": "arn:exec", "idx": 0}) == {"registered": True}
    row = ddb.Table("reviews").get_item(Key={"application_id": "A1", "sk": "TASK"})["Item"]
    assert row["task_token"] == "T1" and row["workflow_state"] == "WAITING" and fake.success == []


@mock_aws
def test_register_token_completes_immediately_if_decided_during_the_gap(monkeypatch):
    """Reviewer decides while a REMIND/ESCALATE step runs (no token registered): the next wait must not hang."""
    ddb = make_tables()
    aa = load("approval_actions")
    fake = FakeSfn()
    monkeypatch.setattr(aa, "_sfn", fake)
    _seed(ddb)
    ddb.Table("reviews").update_item(Key={"application_id": "A1", "sk": "TASK"}, UpdateExpression="SET #s = :c, outcome = :o",
                                     ExpressionAttributeNames={"#s": "status"},
                                     ExpressionAttributeValues={":c": "COMPLETED", ":o": {"action": "APPROVE", "reviewer": "bob"}})
    assert aa.handler({"action": "REGISTER_TOKEN", "task": task(), "token": "T2", "execution_id": "e", "idx": 2}) == {"completed_early": True}
    assert fake.success == [("T2", {"action": "APPROVE", "reviewer": "bob"})]


@mock_aws
def test_remind_is_idempotent_and_publishes(monkeypatch):
    ddb = make_tables()
    aa = load("approval_actions")
    sent = []
    monkeypatch.setattr(aa, "put_event", lambda t, d: sent.append((t, d)))
    _seed(ddb)
    for _ in range(2):                                         # Step Functions retry of the same step
        assert aa.handler({"action": "REMIND", "task": task(), "idx": 0}) == {"sent": True}
    row = ddb.Table("reviews").get_item(Key={"application_id": "A1", "sk": "TASK"})["Item"]
    assert int(row["reminders_sent"]) == 1                     # counted once
    assert [t for t, _ in sent] == ["ReviewReminder", "ReviewReminder"]  # event is at-least-once; consumers dedupe


@mock_aws
def test_escalate_moves_standard_to_senior_once(monkeypatch):
    ddb = make_tables()
    aa = load("approval_actions")
    sent = []
    monkeypatch.setattr(aa, "put_event", lambda t, d: sent.append((t, d)))
    _seed(ddb)
    first = aa.handler({"action": "ESCALATE", "task": task(), "idx": 1})
    again = aa.handler({"action": "ESCALATE", "task": task(), "idx": 1})   # retry
    assert first == {"escalated": True, "level": 1} and again == {"escalated": True, "level": 1}
    row = ddb.Table("reviews").get_item(Key={"application_id": "A1", "sk": "TASK"})["Item"]
    assert row["tier"] == "SENIOR" and row["original_tier"] == "STANDARD" and int(row["priority"]) == 1
    assert sent[0][1]["audience"] == "tier-lead" and sent[0][1]["from_tier"] == "STANDARD" and sent[0][1]["to_tier"] == "SENIOR"
    second = aa.handler({"action": "ESCALATE", "task": task(tier="SENIOR"), "idx": 3})
    assert second["level"] == 2 and sent[-1][1]["audience"] == "head-of-credit"


@mock_aws
def test_steps_do_nothing_for_decided_or_superseded_tasks(monkeypatch):
    ddb = make_tables()
    aa = load("approval_actions")
    monkeypatch.setattr(aa, "put_event", lambda *a: pytest.fail("must not notify for a closed task"))
    _seed(ddb, rnd=1)                                           # a newer round (REQUEST_INFO loop) owns the row now
    assert aa.handler({"action": "REMIND", "task": task(rnd=0), "idx": 0}) == {"sent": False}
    assert aa.handler({"action": "ESCALATE", "task": task(rnd=0), "idx": 1}) == {"escalated": False}


# ---------------- workflow_resume (expiry) ----------------
@mock_aws
def test_expiry_claims_then_resumes_agent_as_system(monkeypatch):
    ddb = make_tables()
    wr = load("workflow_resume")
    calls = []
    monkeypatch.setattr(wr, "_invoke_runtime", lambda sid, payload: calls.append((sid, payload)) or {"decision": "REFERRED", "status": "REFERRED"})
    _seed(ddb, session_id="sess-1", escalation_level=2)
    out = wr.handler({"task": task()})
    assert out == {"skipped": False, "decision": "REFERRED", "status": "REFERRED"}
    assert calls[0][0] == "sess-1" and calls[0][1]["action_type"] == "EXPIRE" and "decision" not in calls[0][1]
    assert ddb.Table("reviews").get_item(Key={"application_id": "A1", "sk": "TASK"})["Item"]["status"] == "EXPIRING"


@mock_aws
def test_expiry_retry_is_reentrant_and_agent_refusal_raises(monkeypatch):
    ddb = make_tables()
    wr = load("workflow_resume")
    _seed(ddb, session_id="sess-1")
    monkeypatch.setattr(wr, "_invoke_runtime", lambda *_: {"error": "no review pending for this session"})
    with pytest.raises(RuntimeError):
        wr.handler({"task": task()})                            # Step Functions will retry, then alert (PublishExpireFailed)
    monkeypatch.setattr(wr, "_invoke_runtime", lambda *_: {"decision": "REFERRED", "status": "REFERRED"})
    assert wr.handler({"task": task()})["skipped"] is False     # row is EXPIRING: the retry may proceed


@mock_aws
def test_expiry_loses_to_a_decision_that_arrived_first(monkeypatch):
    ddb = make_tables()
    wr = load("workflow_resume")
    monkeypatch.setattr(wr, "_invoke_runtime", lambda *_: pytest.fail("must not resume a decided case"))
    _seed(ddb, session_id="sess-1")
    ddb.Table("reviews").update_item(Key={"application_id": "A1", "sk": "TASK"}, UpdateExpression="SET #s = :c, outcome = :o",
                                     ExpressionAttributeNames={"#s": "status"},
                                     ExpressionAttributeValues={":c": "COMPLETED", ":o": {"action": "DECLINE", "reviewer": "bob"}})
    out = wr.handler({"task": task()})
    assert out["skipped"] is True and out["outcome"]["action"] == "DECLINE"


# ---------------- event_recorder + sparse index ----------------
@mock_aws
def test_event_recorder_is_idempotent_and_stays_out_of_the_queue_index():
    ddb = make_tables()
    er = load("event_recorder")
    ev = {"id": "abcdef12-0000", "time": "2026-11-03T10:00:00Z", "detail-type": "ReviewReminder", "detail": {"application_id": "A1", "level": 0}}
    assert er.handler(ev) == {"recorded": True}
    assert er.handler(ev) == {"recorded": False, "reason": "duplicate"}
    from boto3.dynamodb.conditions import Key
    assert ddb.Table("reviews").query(IndexName="status-sla", KeyConditionExpression=Key("status").eq("PENDING"))["Items"] == []


# ---------------- ASL structure ----------------
def _asl():
    text = ASL.read_text().replace("${actions_arn}", "arn:aws:lambda:::function:a").replace("${resume_arn}", "arn:aws:lambda:::function:r") \
        .replace("${bus_name}", "bus")
    return json.loads(text)


def test_asl_is_well_formed_and_every_path_terminates():
    d = _asl()
    states = d["States"]
    assert d["StartAt"] in states
    targets = set()
    for name, st in states.items():
        for key in ("Next", "Default"):
            if key in st:
                targets.add(st[key])
        for c in st.get("Choices", []) + st.get("Catch", []):
            targets.add(c["Next"])
        assert st["Type"] in ("Fail", "Succeed", "Choice") or st.get("End") or "Next" in st, f"{name} is a dead end"
    assert targets <= set(states), targets - set(states)
    assert {n for n, s in states.items() if s["Type"] == "Fail"} == {"ExpireFailed", "WorkflowFailed", "UnknownSegment"}


def test_asl_waits_with_task_token_and_never_decides():
    d = _asl()["States"]
    wait = d["WaitForReviewer"]
    assert wait["Resource"].endswith("lambda:invoke.waitForTaskToken") and wait["Parameters"]["Payload"]["token.$"] == "$$.Task.Token"
    assert wait["TimeoutSecondsPath"] == "$.segment.wait_seconds"
    assert any(c["ErrorEquals"] == ["States.Timeout"] and c["Next"] == "AfterTimeout" for c in wait["Catch"])
    # the only thing the workflow can do to a case at the end of the ladder is expire it (refer); there is no approve/decline state
    assert not [n for n in d if re.search(r"approve|decline", n, re.IGNORECASE)]
    # the ladder loops back through NextSegment, so a REMIND/ESCALATE returns to waiting
    assert d["SendReminder"]["Next"] == d["Escalate"]["Next"] == "NextSegment" and d["NextSegment"]["Next"] == "SelectSegment"


def test_asl_events_use_the_documented_detail_types():
    d = _asl()["States"]
    published = {st["Parameters"]["Entries"][0]["DetailType"] for st in d.values() if st.get("Resource", "").endswith("events:putEvents")}
    assert published == {"ReviewCompleted", "ReviewExpired", "ReviewExpireFailed", "ReviewWorkflowFailed"}


def test_asl_never_logs_task_tokens():
    tf = (ASL.parent / "main.tf").read_text()
    assert "include_execution_data = false" in tf
    assert os.path.exists(ASL)
