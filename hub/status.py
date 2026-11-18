"""Read-only status of every tenant stage: running release, recovery copy, Lambda errors, health, pinned core version.

Reads only: S3 release record, CloudWatch Errors metric, the stage's /health endpoint, tenant.lock in the tenant repo, and
core release tags in the core repo. Never a tenant database, SSM secret or customer record.
"""
from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def registry() -> dict:
    return json.loads((HERE / "registry.json").read_text(encoding="utf-8"))


def _session(stage: dict):
    """boto3 session for a tenant account: the stage's read-only role when set (opsdesk-hub-readonly), else the admin's own."""
    import boto3
    if stage.get("role_arn"):
        c = boto3.client("sts").assume_role(RoleArn=stage["role_arn"], RoleSessionName="opsdesk-hub", DurationSeconds=900)["Credentials"]
        return boto3.Session(aws_access_key_id=c["AccessKeyId"], aws_secret_access_key=c["SecretAccessKey"],
                             aws_session_token=c["SessionToken"], region_name=stage.get("region"))
    return boto3.Session(region_name=stage.get("region"))


def release(key: str, stage_name: str, stage: dict) -> dict:
    """The stage's last good release and its recovery copy (written by deploy.py after each healthy deploy)."""
    bucket = f"meridian-opsdesk-{key}-{stage_name}-{stage['account']}"
    try:
        body = _session(stage).client("s3").get_object(Bucket=bucket, Key=f"{key}/{stage_name}/releases/last-good.json")["Body"].read()
        rec = json.loads(body)
        return {"ok": True, "current": rec.get("current") or {}, "previous": rec.get("previous") or {}}
    except Exception as e:  # noqa: BLE001  (no access, no deploy yet: shown on the card)
        return {"ok": False, "error": type(e).__name__ + ": " + str(e)[:160]}


def errors_24h(key: str, stage_name: str, stage: dict) -> int | None:
    try:
        cw = _session(stage).client("cloudwatch")
        now = datetime.now(timezone.utc)
        r = cw.get_metric_statistics(Namespace="AWS/Lambda", MetricName="Errors", StartTime=now - timedelta(hours=24), EndTime=now,
                                     Period=86400, Statistics=["Sum"],
                                     Dimensions=[{"Name": "FunctionName", "Value": f"meridian-opsdesk-{key}-{stage_name}"}])
        return int(sum(p["Sum"] for p in r.get("Datapoints", [])))
    except Exception:  # noqa: BLE001
        return None


def health(stage: dict) -> str:
    if not stage.get("url"):
        return "no URL"
    try:
        import httpx
        r = httpx.get(stage["url"].rstrip("/") + "/healthz", timeout=8, follow_redirects=False)
        return "healthy" if r.status_code == 200 else f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return "unreachable (" + type(e).__name__ + ")"


def core_versions(core_repo: Path) -> list[str]:
    """Core release tags, newest first (core-vX.Y.Z)."""
    try:
        out = subprocess.run(["git", "-C", str(core_repo), "tag", "--list", "core-v*", "--sort=-v:refname"], capture_output=True,
                             text=True, timeout=10).stdout
        return [t.strip() for t in out.splitlines() if t.strip()]
    except Exception:  # noqa: BLE001
        return []


def pinned_core(tenant_repo: Path) -> str | None:
    """core: X.Y.Z from the tenant repo's tenant.lock."""
    p = tenant_repo / "tenant.lock"
    if not p.exists():
        return None
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("core:"):
            return line.split(":", 1)[1].strip()
    return None


def behind(pinned: str | None, versions: list[str]) -> int | None:
    if not pinned or not versions:
        return None
    tag = f"core-v{pinned}"
    return versions.index(tag) if tag in versions else None


def overview() -> dict:
    reg = registry()
    core_repo = (ROOT / reg.get("core_repo", "")).resolve()
    versions = core_versions(core_repo)
    jobs = []
    for t in reg["tenants"]:
        for sn, st in t["stages"].items():
            jobs.append((t, sn, st))

    def one(job):
        t, sn, st = job
        return t["key"], sn, {"release": release(t["key"], sn, st), "errors_24h": errors_24h(t["key"], sn, st),
                              "health": health(st), "url": st.get("url"), "account": st.get("account")}

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(one, jobs))
    tenants = []
    for t in reg["tenants"]:
        pin = pinned_core((ROOT / t.get("repo", "")).resolve())
        tenants.append({"key": t["key"], "name": t["name"], "owner": t.get("owner"), "repo": t.get("repo"), "core_pinned": pin,
                        "core_behind": behind(pin, versions),
                        "stages": {sn: d for k, sn, d in results if k == t["key"]}})
    return {"core_versions": versions[:10], "tenants": tenants, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
