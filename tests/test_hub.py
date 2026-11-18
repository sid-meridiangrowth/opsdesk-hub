"""Hub: sign-in (admins only, brute-force limit, TOTP), overview from release records (AWS and HTTP faked, nothing leaves)."""
import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.update(HUB_ADMINS="admin@meridian.test", HUB_SESSION_KEY="k" * 40)
from hub import app as hubapp  # noqa: E402
from hub import status  # noqa: E402

os.environ["HUB_PASSWORD_HASH"] = hubapp.hash_password("Corr3ct-horse")
FAIL = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    c or FAIL.append(m)


# fakes: release record, CloudWatch errors, /healthz, core tags, tenant.lock
REL = {"current": {"version": "v1.31", "commit": "92d56a6abc", "at": "2026-11-16T10:00:00Z"}, "previous": {"version": "v1.30"}}
status.release = lambda k, sn, st: {"ok": True, **REL} if sn == "production" else {"ok": False, "error": "NoSuchKey: none"}
status.errors_24h = lambda k, sn, st: 0 if sn == "production" else None
status.health = lambda st: "healthy" if st.get("url") else "no URL"
status.core_versions = lambda repo: ["core-v1.1.0", "core-v1.0.0"]
status.pinned_core = lambda repo: "1.0.0"

from fastapi.testclient import TestClient  # noqa: E402
c = TestClient(hubapp.app)
check(c.get("/", follow_redirects=False).headers["location"] == "/login", "signed out: sent to sign-in")
r = c.post("/login", data={"email": "intruder@evil.test", "password": "Corr3ct-horse"}, follow_redirects=False)
check("Wrong" in r.headers["location"], "a non-admin email is refused even with the password")
r = c.post("/login", data={"email": "admin@meridian.test", "password": "Corr3ct-horse"}, follow_redirects=False)
check(r.headers["location"] == "/", "an admin signs in")
h = c.get("/").text
check("Amp Fitness" in h and "v1.31" in h and "v1.30" in h and "healthy" in h, "overview: tenant, version, recovery copy, health")
check("1 behind" in h and "core-v1.1.0" in h, "pinned core 1.0.0 shows 1 release behind")
check("No release record" in h, "a stage without a release record says so")
r = c.get("/")
check("Content-Security-Policy" in r.headers and r.headers.get("X-Frame-Options") == "DENY", "security headers")
# brute force: 5 failures per address, then refused even with the right password
b = TestClient(hubapp.app)
hubapp._fails.clear()
for i in range(5):
    b.post("/login", data={"email": "admin@meridian.test", "password": f"guess{i}"}, follow_redirects=False)
r = b.post("/login", data={"email": "admin@meridian.test", "password": "Corr3ct-horse"}, follow_redirects=False)
check("Too+many" in r.headers["location"], "after 5 failures the address is locked out for 15 minutes")
# TOTP
hubapp._fails.clear()
secret = base64.b32encode(b"12345678901234567890").decode()
os.environ["HUB_TOTP_SECRET"] = secret
t = TestClient(hubapp.app)
r = t.post("/login", data={"email": "admin@meridian.test", "password": "Corr3ct-horse", "code": "000000"}, follow_redirects=False)
check("Wrong" in r.headers["location"], "with MFA on, a wrong code is refused")
k = base64.b32decode(secret); hm = hmac.new(k, struct.pack(">Q", int(time.time() // 30)), hashlib.sha1).digest(); o = hm[-1] & 15
code = f"{(struct.unpack('>I', hm[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"
r = t.post("/login", data={"email": "admin@meridian.test", "password": "Corr3ct-horse", "code": code}, follow_redirects=False)
check(r.headers["location"] == "/", "with MFA on, the right code signs in")
print(f"\n{'FAILURES: ' + str(FAIL) if FAIL else 'ALL CHECKS PASSED'}")
sys.exit(1 if FAIL else 0)
