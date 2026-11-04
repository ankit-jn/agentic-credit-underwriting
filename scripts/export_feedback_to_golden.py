"""Close the feedback loop: turn reviewer decisions into labelled eval cases (human decision = ground truth).
Usage: python scripts/export_feedback_to_golden.py <name-prefix> > candidates.jsonl
Candidates are reviewed by Model Risk before being merged into backend/agent/evals/golden_cases.jsonl."""
import json
import sys

import boto3

prefix = sys.argv[1]
ddb = boto3.resource("dynamodb")
for row in ddb.Table(f"{prefix}-feedback").scan()["Items"]:
    if row["human_decision"] == "REQUEST_INFO":
        continue
    print(json.dumps({
        "id": row["application_id"], "source": "reviewer-feedback", "human_decision": row["human_decision"],
        "reason_codes": row.get("reason_codes", []), "agent_confidence": float(row["confidence"]),
        "agent_recommendation": row["agent_recommendation"], "agreement": row["agreement"],
        "todo": "attach canned tool outputs + expected tier before merging",
    }))
