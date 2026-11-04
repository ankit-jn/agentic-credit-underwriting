"""Seed synthetic demo data into DynamoDB. Usage: python scripts/seed_data.py <prefix>   e.g. uw-agent-dev"""
import random
import sys
from datetime import date, timedelta
from decimal import Decimal

import boto3

prefix = sys.argv[1]
ddb = boto3.resource("dynamodb")
T = lambda n: ddb.Table(f"{prefix}-{n}")

applicants = [
    ("APP-1001", "Sunrise Agro Processors Pvt Ltd", "VERIFIED", 3000, 2600),
    ("APP-1002", "Delta Logistics LLP", "VERIFIED", 1800, 1850),   # thin cash flow
    ("APP-1003", "Blocked Trading Co", "VERIFIED", 2500, 2000),    # watchlist hit
    ("APP-1004", "Nova MedCare Services", "KYC_PENDING", 2200, 1700),
]
with T("applicants").batch_writer() as b:
    for aid, name, kyc, _, _ in applicants:
        b.put_item(Item={"applicant_id": aid, "legal_name": name, "kyc_status": kyc, "country": "IN", "vintage_years": 4})

random.seed(7)
with T("transactions").batch_writer() as b:
    for aid, _, _, inflow, outflow in applicants:
        for m in range(12):
            d = date.today().replace(day=1) - timedelta(days=30 * m)
            for k, (typ, base) in enumerate([("CREDIT", inflow), ("DEBIT", outflow)]):
                amt = Decimal(str(round(base * 100 * random.uniform(0.85, 1.15), 2)))
                b.put_item(Item={"applicant_id": aid, "txn_ts": f"{d:%Y-%m}-{10 + k:02d}T10:00:00Z", "type": typ, "amount": amt})

T("watchlist").put_item(Item={"name_key": "blocked trading co", "list": "DEMO-SANCTIONS", "reason": "synthetic test entry"})
print("seeded")
