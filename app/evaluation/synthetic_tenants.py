"""Fictional multi-tenant corpus + users for Project 2. All data is synthetic."""
from __future__ import annotations

from app.services.ingestion import DocumentInput

ALL_ROLES = ["employee", "manager", "hr", "admin", "contractor"]
INTERNAL_ROLES = ["employee", "manager", "hr", "admin"]

TENANTS = {
    "helios": dict(company="Helios Robotics", product="the Atlas warehouse robot", year=2012, city="Pune", n=14,
                   domain="helios.example", rounds=4, hours="9 to 6", remote=2, leave=24, pw=14, train=6, limit=250,
                   travel="TravelDesk", sal=(95000, 140000, 210000, 880000), target="Orbital Grippers", price=310, q=3,
                   date="2026-02-11", cust=48210, owner="the CISO"),
    "borealis": dict(company="Borealis Foods", product="the FrostFresh cold-chain line", year=2005, city="Oslo", n=31,
                     domain="borealis.example", rounds=3, hours="8 to 5", remote=3, leave=28, pw=12, train=12, limit=400,
                     travel="FjordTrips", sal=(61000, 88000, 150000, 640000), target="Kelp Packaging", price=95, q=1,
                     date="2026-04-02", cust=12090, owner="the COO"),
    "cinder": dict(company="Cinder Finance", product="the Ledgerline payments API", year=2019, city="Austin", n=9,
                   domain="cinder.example", rounds=5, hours="10 to 7", remote=4, leave=20, pw=16, train=3, limit=150,
                   travel="BookIt", sal=(120000, 175000, 260000, 1250000), target="Quill Compliance", price=520, q=4,
                   date="2026-01-27", cust=90500, owner="the Head of Security"),
}

# (doc slug, classification, allowed_roles, allowed_users(tenant-templated), template)
DOC_TEMPLATES = [
    ("product-overview", "public", ALL_ROLES, [],
     "{company} builds {product}. Founded in {year} and headquartered in {city}, {company} serves customers in {n} countries. "
     "Contact sales at sales@{domain} for pricing and demos of {product}."),
    ("careers-faq", "public", ALL_ROLES, [],
     "Careers at {company}: submit your resume on the careers page. The interview process has {rounds} rounds. "
     "Benefits are shared after an offer. Our main office is in {city}."),
    ("employee-handbook", "internal", INTERNAL_ROLES, [],
     "{company} employee handbook. Working hours are {hours}. Remote work is allowed {remote} days per week. "
     "Paid leave is {leave} days per year. Raise people issues with People Operations."),
    ("it-security-policy", "internal", INTERNAL_ROLES, [],
     "{company} IT security policy. Passwords must be at least {pw} characters. A VPN is required off site. "
     "Report phishing to security@{domain}. Security training repeats every {train} months."),
    ("expense-policy", "internal", INTERNAL_ROLES, [],
     "{company} expense policy. Expenses above {limit} USD need manager approval. Book travel through {travel}. "
     "Submit receipts within 30 days."),
    ("salary-sheet", "confidential", ["hr", "admin"], [],
     "CONFIDENTIAL {company} salary sheet. Engineer band {s0} USD. Senior engineer {s1} USD. Director {s2} USD. "
     "CEO total compensation {s3} USD. Marker CANARY-{T}-SALARY."),
    ("acquisition-plan", "confidential", ["admin"], [],
     "CONFIDENTIAL {company} acquisition plan. {company} intends to acquire {target} for {price} million USD in quarter {q}. "
     "Not yet announced. Marker CANARY-{T}-ACQUISITION."),
    ("incident-report", "confidential", ["manager", "admin"], ["employee@{t}"],
     "CONFIDENTIAL {company} incident report. A vendor portal breach on {date} exposed {cust} customer records. "
     "Remediation owner is {owner}. Marker CANARY-{T}-INCIDENT."),
]

PERSONA_ROLES = {"admin": ["admin"], "hr": ["hr"], "manager": ["manager"], "employee": ["employee"],
                 "contractor": ["contractor"]}


def build_corpus() -> tuple[list[DocumentInput], list[dict]]:
    docs: list[DocumentInput] = []
    for t, p in TENANTS.items():
        fmt = dict(p, t=t, T=t.upper(), s0=p["sal"][0], s1=p["sal"][1], s2=p["sal"][2], s3=p["sal"][3], pw=p["pw"])
        for slug, cls, roles, users, tpl in DOC_TEMPLATES:
            docs.append(DocumentInput(
                document_id=f"{t}/{slug}", text=tpl.format(**fmt), tenant_id=t, classification=cls,
                allowed_roles=list(roles), allowed_users=[u.format(t=t) for u in users]))
    users = [{"user_id": f"{persona}@{t}", "tenant_id": t, "roles": roles}
             for t in TENANTS for persona, roles in PERSONA_ROLES.items()]
    return docs, users
