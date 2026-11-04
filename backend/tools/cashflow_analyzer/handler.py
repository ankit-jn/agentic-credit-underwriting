"""Tool 2 (Lambda): bank-statement cash-flow analytics and DSCR proxy."""
import os
import statistics
from collections import defaultdict

import boto3
from boto3.dynamodb.conditions import Key
from obs import observed

_ddb = boto3.resource("dynamodb")
TXNS = os.environ.get("TRANSACTIONS_TABLE", "transactions")


def emi(principal: float, annual_rate: float, months: int) -> float:
    """Monthly instalment for the requested amount and tenor (reducing balance), used for the DSCR proxy."""
    r = annual_rate / 12
    if r == 0:
        return principal / months
    return principal * r * (1 + r) ** months / ((1 + r) ** months - 1)


@observed("cashflow_analyzer")
def handler(event, context=None):
    """Gateway tool entrypoint: compute inflow/outflow, cash-flow metrics and DSCR proxy from synthetic transactions.
    """
    applicant_id = event["applicant_id"]
    requested = float(event.get("requested_amount", 0))
    tenor = int(event.get("tenor_months", 12))
    rate = float(event.get("annual_rate", 0.12))

    items, kwargs = [], {"KeyConditionExpression": Key("applicant_id").eq(applicant_id)}
    while True:
        page = _ddb.Table(TXNS).query(**kwargs)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    monthly = defaultdict(lambda: {"in": 0.0, "out": 0.0})
    for t in items:
        monthly[t["txn_ts"][:7]]["in" if t["type"] == "CREDIT" else "out"] += float(t["amount"])

    if not monthly:
        return {"applicant_id": applicant_id, "data_available": False}

    nets = [m["in"] - m["out"] for m in monthly.values()]
    avg_in = statistics.mean(m["in"] for m in monthly.values())
    avg_out = statistics.mean(m["out"] for m in monthly.values())
    avg_net = statistics.mean(nets)
    payment = emi(requested, rate, tenor) if requested else 0.0
    dscr = (avg_net / payment) if payment else None
    return {
        "applicant_id": applicant_id,
        "data_available": True,
        "months_analysed": len(monthly),
        "avg_monthly_inflow": round(avg_in, 2),
        "avg_monthly_outflow": round(avg_out, 2),
        "avg_monthly_net": round(avg_net, 2),
        "net_volatility": round(statistics.pstdev(nets) if len(nets) > 1 else 0.0, 2),
        "negative_months": sum(1 for n in nets if n < 0),
        "proposed_monthly_payment": round(payment, 2),
        "dscr_proxy": round(dscr, 2) if dscr is not None else None,
    }
