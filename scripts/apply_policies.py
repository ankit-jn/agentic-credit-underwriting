"""Apply Cedar policies in /policies to the AgentCore Policy engine (idempotent: create or update by name).
Usage: python scripts/apply_policies.py <policy_engine_id> <gateway_arn> <region>
NOTE: bedrock-agentcore-control Policy APIs are new - confirm operation/field names against your boto3 version."""
import pathlib
import sys

import boto3

engine_id, gateway_arn, region = sys.argv[1:4]
client = boto3.client("bedrock-agentcore-control", region_name=region)
existing = {p["name"]: p["policyId"] for p in client.list_policies(policyEngineId=engine_id).get("policies", [])}

for f in sorted(pathlib.Path(__file__).parent.parent.joinpath("policies").glob("*.cedar")):
    name = f.stem.replace("-", "_")
    statement = f.read_text().replace("${GATEWAY_ARN}", gateway_arn)
    definition = {"cedar": {"statement": statement}}
    if name in existing:
        client.update_policy(policyEngineId=engine_id, policyId=existing[name], definition=definition)
        print("updated", name)
    else:
        client.create_policy(policyEngineId=engine_id, name=name, definition=definition, validationMode="FAIL_ON_ANY_FINDINGS")
        print("created", name)
