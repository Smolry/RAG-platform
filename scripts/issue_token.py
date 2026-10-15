"""DEV ONLY: mint a JWT for a seeded user. (Real deployments would use an identity provider.)
python -m scripts.issue_token --user hr@helios"""
import argparse
import json
from pathlib import Path

from app.core.config import get_settings
from app.security.auth import Principal, issue_token

ap = argparse.ArgumentParser()
ap.add_argument("--user", required=True)
ap.add_argument("--users-file", default="data/synthetic/users.json")
a = ap.parse_args()
s = get_settings()
u = next(x for x in json.loads(Path(a.users_file).read_text()) if x["user_id"] == a.user)
print(issue_token(Principal(u["user_id"], u["tenant_id"], tuple(u["roles"])), s.jwt_secret, s.jwt_algorithm, s.jwt_ttl_minutes))
