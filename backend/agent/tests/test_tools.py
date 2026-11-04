"""Tool Lambda tests against moto-mocked DynamoDB: KYC, cash flow, idempotent decision write, review task/feedback, shared obs helper copies."""
import importlib.util
import os
import pathlib

import boto3
import pytest
from moto import mock_aws

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "tools")


def load(name):
    import sys
    sys.path.insert(0, os.path.join(ROOT, name))  # tool dirs ship their own obs.py
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, name, "handler.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    for k, v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "x"}.items():
        monkeypatch.setenv(k, v)


def make_table(name, hash_key, range_key=None):
    ddb = boto3.resource("dynamodb")
    schema = [{"AttributeName": hash_key, "KeyType": "HASH"}]
    attrs = [{"AttributeName": hash_key, "AttributeType": "S"}]
    if range_key:
        schema.append({"AttributeName": range_key, "KeyType": "RANGE"})
        attrs.append({"AttributeName": range_key, "AttributeType": "S"})
    return ddb.create_table(TableName=name, KeySchema=schema, AttributeDefinitions=attrs, BillingMode="PAY_PER_REQUEST")


@mock_aws
def test_kyc_flags_watchlist_match():
    make_table("applicants", "applicant_id").put_item(Item={"applicant_id": "A1", "kyc_status": "VERIFIED"})
    make_table("watchlist", "name_key").put_item(Item={"name_key": "blocked trading co", "list": "OFAC", "reason": "test"})
    out = load("kyc_screening").handler({"applicant_id": "A1", "legal_name": "Blocked Trading Co."})
    assert out["screening_status"] == "POTENTIAL_MATCH"


@mock_aws
def test_cashflow_metrics():
    t = make_table("transactions", "applicant_id", "txn_ts")
    for i, (typ, amt) in enumerate([("CREDIT", 1000), ("DEBIT", 400), ("CREDIT", 1000), ("DEBIT", 400)]):
        month = "2026-01" if i < 2 else "2026-02"
        t.put_item(Item={"applicant_id": "A1", "txn_ts": f"{month}-0{i+1}T00:00:00Z", "type": typ, "amount": amt})
    out = load("cashflow_analyzer").handler({"applicant_id": "A1", "requested_amount": 6000, "tenor_months": 12})
    assert out["months_analysed"] == 2 and out["avg_monthly_net"] == 600.0 and out["dscr_proxy"] > 0


@mock_aws
def test_decision_is_idempotent():
    make_table("applications", "application_id", "sk")
    h = load("decision_recorder").handler
    ev = {"application_id": "P1", "applicant_id": "A1", "decision": "APPROVED", "amount": 1000}
    assert h(ev)["recorded"] is True
    assert h(ev)["recorded"] is False


@mock_aws
def test_review_task_upsert_and_feedback():
    ddb = boto3.resource("dynamodb")
    ddb.create_table(
        TableName="reviews", BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "application_id", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in ("application_id", "sk", "status", "sla_due")],
        GlobalSecondaryIndexes=[{"IndexName": "status-sla", "Projection": {"ProjectionType": "ALL"},
                                 "KeySchema": [{"AttributeName": "status", "KeyType": "HASH"}, {"AttributeName": "sla_due", "KeyType": "RANGE"}]}],
    )
    make_table("feedback", "application_id", "feedback_ts")
    h = load("review_queue").handler
    base = {"application_id": "P1", "event_type": "TASK_CREATED", "tier": "SENIOR", "priority": 1, "sla_hours": 8, "confidence": 0.4}
    h(base)
    h(base)  # idempotent
    item = ddb.Table("reviews").get_item(Key={"application_id": "P1", "sk": "TASK"})["Item"]
    assert item["status"] == "PENDING" and item["tier"] == "SENIOR"
    out = h({"application_id": "P1", "event_type": "FEEDBACK", "agent_recommendation": "APPROVE",
             "human_decision": "DECLINE", "reason_codes": "DSCR_TOO_LOW", "confidence": 0.4})
    assert out["override"] is True and out["agreement"] is False
    assert ddb.Table("reviews").get_item(Key={"application_id": "P1", "sk": "TASK"})["Item"]["status"] == "COMPLETED"


def test_obs_helper_copies_are_identical():
    import hashlib
    digests = {d: hashlib.sha256(pathlib.Path(ROOT, d, "obs.py").read_bytes()).hexdigest()
               for d in os.listdir(ROOT) if os.path.exists(os.path.join(ROOT, d, "obs.py"))}
    assert len(set(digests.values())) == 1, f"obs.py copies differ: {digests}"
