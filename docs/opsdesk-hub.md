# OpsDesk Hub (Meridian admin plane)

One place where Meridian admins see and reach every tenant's OpsDesk and the core code. It never holds tenant data or
tenant credentials.

## What it shows

- **Tenants**: key, customer, stages with URLs, AWS account, owner, status.
- **Releases**: per tenant and stage, the running version, image, commit, core version pinned, deploy time and the
  recovery copy (read from each tenant's `releases/last-good.json` in its S3 bucket); how many core releases behind.
- **Health**: last health check, error count and Lambda errors in the last 24 hours, cost today against the $1/day
  target, scan result of the running image.
- **Core**: core releases, changelog, which tenants run which core version, open Renovate PRs per tenant.
- **Activity**: a merged audit view (who signed in where from the Hub, deploys, rollbacks).

## What it lets an admin do

- **Open a tenant's OpsDesk as admin** without a separate password: the Hub issues a signed, single-use admin session
  token (Ed25519, 2-minute lifetime, bound to the tenant key and the admin's email); the tenant's `/hub/sso` verifies it
  with the Hub's public key (tenant setting `hub.public_key`) and starts a normal session, written to that tenant's audit
  log as `hub_sso`. A tenant without the key set refuses Hub sign-ins.
- **Read-only operations**: view releases, logs summary, costs.
- Deploys and rollbacks stay in each tenant repo's deploy command (and its gates); the Hub links to them, it does not
  run them in the first version.

## How it reaches tenants

- Runs in Meridian's own AWS account.
- Each tenant account has a role `opsdesk-hub-readonly` trusted by the Hub account: `s3:GetObject` on
  `<bucket>/*/releases/*`, `cloudwatch:GetMetricData`, `logs:FilterLogEvents` (errors only), `ce:GetCostAndUsage`. No
  access to tenant databases, SSM secrets or customer data.
- GitHub: a read-only GitHub App for the core repo and the tenant repos (releases, PRs, tags).

## Security

- Hub sign-in: Meridian admins only, with MFA; the admin list is in the Hub, not in tenants.
- Every Hub action is audited in the Hub; every SSO is also audited in the tenant.
- Revoking an admin in the Hub stops new SSO tokens at once; a tenant can rotate or remove `hub.public_key` to cut the
  Hub off.

## Build plan

1. Tenant registry (JSON in the hub repo) + releases and health pages (read-only, cross-account role).
2. SSO: key pair in the Hub, `/hub/sso` endpoint in core (off unless `hub.public_key` is set).
3. Core / Renovate view from GitHub.
