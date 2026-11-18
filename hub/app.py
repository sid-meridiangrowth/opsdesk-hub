"""OpsDesk Hub: Meridian admins' read-only view over every tenant's OpsDesk and the core code.

    python -m hub.app            # http://127.0.0.1:8100 (local only)

Sign-in: HUB_ADMINS (comma-separated emails) and HUB_PASSWORD_HASH (pbkdf2, `python -m hub.app hash`) from the environment,
plus a TOTP code when HUB_TOTP_SECRET is set. Uses the admin's own AWS credentials, or each stage's opsdesk-hub-readonly role.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import os
import secrets
import struct
import sys
import time

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from . import status

app = FastAPI(title="OpsDesk Hub", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("HUB_SESSION_KEY") or secrets.token_urlsafe(32),
                   https_only=False, same_site="strict", max_age=8 * 3600)
_fails: dict[str, list[float]] = {}


def hash_password(pw: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 600_000)
    return f"pbkdf2${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, s, d = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), base64.b64decode(s), 600_000)
        return hmac.compare_digest(calc, base64.b64decode(d))
    except Exception:  # noqa: BLE001
        return False


def totp_ok(code: str, secret: str, now: float | None = None) -> bool:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    t = int((now or time.time()) // 30)
    for w in (-1, 0, 1):
        h = hmac.new(key, struct.pack(">Q", t + w), hashlib.sha1).digest()
        o = h[-1] & 0x0F
        if f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}" == (code or "").strip():
            return True
    return False


def admins() -> set[str]:
    return {e.strip().lower() for e in (os.environ.get("HUB_ADMINS") or "").split(",") if e.strip()}


def _limited(ip: str) -> bool:
    """Sign-in brute force: 5 failures per address in 15 minutes, then refused."""
    now = time.time()
    _fails[ip] = [t for t in _fails.get(ip, []) if now - t < 900]
    return len(_fails[ip]) >= 5


CSS = """:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2433;--muted:#667085;--line:#e4e7ec;--good:#067647;--bad:#b42318;--warn:#b54708;--acc:#c24a00}
@media (prefers-color-scheme:dark){:root{--bg:#0f1218;--card:#171b23;--ink:#e6e8ec;--muted:#98a2b3;--line:#2a303b;--acc:#ff8a4c}}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,sans-serif}main{max-width:1100px;margin:0 auto;padding:20px 16px}
h1{font-size:20px;margin:0 0 4px}.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}.t{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.badge{font-size:12px;padding:2px 8px;border-radius:99px;border:1px solid var(--line)}.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}
dl{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;margin:8px 0 0}dt{color:var(--muted)}dd{margin:0;overflow-wrap:anywhere}
a{color:var(--acc)}.mono{font-family:ui-monospace,monospace;font-size:12.5px}input{padding:8px;border:1px solid var(--line);border-radius:6px;width:100%;box-sizing:border-box;background:var(--card);color:var(--ink)}
button{padding:8px 14px;border:0;border-radius:6px;background:var(--acc);color:#fff;cursor:pointer}form.s{max-width:340px;display:grid;gap:10px}"""


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                        f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body><main>{body}</main></body></html>",
                        headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'",
                                 "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})


@app.get("/login")
def login_form(err: str = ""):
    e = f"<p class=bad>{html.escape(err)}</p>" if err else ""
    t = "<input name=code placeholder='Authenticator code' autocomplete=one-time-code inputmode=numeric>" if os.environ.get("HUB_TOTP_SECRET") else ""
    return page("Hub sign-in", f"<h1>OpsDesk Hub</h1><p class=muted>Meridian admins only.</p>{e}<form class=s method=post>"
                f"<input name=email type=email placeholder=Email required><input name=password type=password placeholder=Password required>{t}"
                f"<button>Sign in</button></form>")


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), code: str = Form("")):
    ip = request.client.host if request.client else "?"
    if _limited(ip):
        return RedirectResponse("/login?err=Too+many+attempts.+Wait+15+minutes.", status_code=303)
    stored = os.environ.get("HUB_PASSWORD_HASH") or ""
    ok = email.strip().lower() in admins() and stored and check_password(password, stored)
    if ok and os.environ.get("HUB_TOTP_SECRET"):
        ok = totp_ok(code, os.environ["HUB_TOTP_SECRET"])
    if not ok:
        _fails.setdefault(ip, []).append(time.time())
        return RedirectResponse("/login?err=Wrong+email,+password+or+code.", status_code=303)
    request.session.clear()
    request.session["admin"] = email.strip().lower()
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


def _card(key: str, sn: str, d: dict) -> str:
    rel = d["release"]
    cur, prev = (rel.get("current") or {}), (rel.get("previous") or {})
    h = d["health"]
    tone = "good" if h == "healthy" else "bad" if h.startswith(("HTTP", "unreachable")) else "muted"
    errs = d["errors_24h"]
    etone = "good" if errs == 0 else "bad" if errs else "muted"
    body = (f"<dl><dt>Version</dt><dd>{html.escape(str(cur.get('version') or '–'))}</dd>"
            f"<dt>Commit</dt><dd class=mono>{html.escape(str(cur.get('commit') or '')[:10] or '–')}</dd>"
            f"<dt>Deployed</dt><dd>{html.escape(str(cur.get('at') or '–'))}</dd>"
            f"<dt>Recovery copy</dt><dd>{html.escape(str(prev.get('version') or '–'))}</dd>"
            f"<dt>Errors 24h</dt><dd class={etone}>{'–' if errs is None else errs}</dd>"
            f"<dt>Account</dt><dd class=mono>{html.escape(str(d.get('account') or ''))}</dd></dl>") if rel.get("ok") else \
        f"<p class=muted>No release record: {html.escape(rel.get('error', ''))}</p>"
    link = f"<a href='{html.escape(d['url'])}' target=_blank rel='noopener noreferrer'>Open OpsDesk</a>" if d.get("url") else ""
    return (f"<div class=card><div class=t><b>{html.escape(sn.title())}</b><span class='badge {tone}'>{html.escape(h)}</span></div>"
            f"{body}<p style='margin:8px 0 0'>{link}</p></div>")


@app.get("/")
def home(request: Request):
    who = request.session.get("admin")
    if not who or who not in admins():
        return RedirectResponse("/login", status_code=303)
    o = status.overview()
    cv = o["core_versions"]
    core = (f"<div class=card><div class=t><b>Core</b><span class=muted>meridian-ai-operations-desk</span></div>"
            f"<p>Latest release: <b class=mono>{html.escape(cv[0]) if cv else 'no core-v* tag yet'}</b></p></div>")
    ts = []
    for t in o["tenants"]:
        pin = t["core_pinned"] or "not pinned"
        behind = t["core_behind"]
        bt = "" if behind is None else (" <span class='badge good'>current</span>" if behind == 0 else f" <span class='badge warn'>{behind} behind</span>")
        cards = "".join(_card(t["key"], sn, d) for sn, d in t["stages"].items())
        ts.append(f"<h2 style='font-size:16px;margin:22px 0 8px'>{html.escape(t['name'])} <span class='muted mono'>{html.escape(t['key'])}</span></h2>"
                  f"<p class=muted>Core pinned: <span class=mono>{html.escape(pin)}</span>{bt} · repo {html.escape(t.get('repo') or '')}</p>"
                  f"<div class=grid>{cards}</div>")
    return page("OpsDesk Hub", f"<div class=t><div><h1>OpsDesk Hub</h1><p class=muted>Every tenant, read-only · {html.escape(o['at'])}</p></div>"
                f"<form method=post action=/logout><button>Sign out {html.escape(who)}</button></form></div>{core}{''.join(ts)}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["hash"]:
        import getpass
        print(hash_password(getpass.getpass("Hub password: ")))
    else:
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("HUB_PORT", "8100")))
