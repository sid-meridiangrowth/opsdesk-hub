# Core / tenant split (step 1 spec)

Status: approved direction, in progress. Amp production is frozen at tag `amp-prod-freeze-2026-11-17` (v1.31, PR #96)
in the Amp repo until the split is proven on the Amp sandbox.

## Goal

GitHub holds three kinds of repository, and no tenant's data, rules or credentials ever sit in a shared one:

| Repo | Folder | Contains |
|---|---|---|
| `meridian-ai-operations-desk` (core) | `E:\Sid-Work\Meridian\projects\meridian-ai-operations-desk` | The platform: data model, engine, UI, the connector catalog, deploy tooling, core tests against a fake tenant |
| `opsdesk-tenant-<key>` (one per customer) | `E:\Sid-Work\Meridian\projects\opsdesk-tenant-<key>` | Tenant bundle: profile, value sets, surveys, field specs, branding, the tenant's pipelines (JSON), tenant-only plugins, environments, migration tools, tenant tests |
| `opsdesk-hub` | `E:\Sid-Work\Meridian\projects\opsdesk-hub` | Meridian's admin plane over every tenant (see opsdesk-hub.md) |

Secrets are in no repo: each tenant's SSM Parameter Store, as today.

## Rule of thumb for what goes where

- **Core** if a second customer could use it unchanged, configured by settings: entities, engine, pipeline node types, UI,
  security, MCP, reports, and connectors to products other customers also run (Magento, Shopify, Klaviyo, Typeform,
  Intercom, Slack, FedEx, ShipStation, J.B. Hunt, Onward). A connector is a capability; which ones a tenant switches on,
  with what credentials, is tenant data.
- **Tenant** if it encodes one customer's business: their pipelines and thresholds, product SKUs and families, survey
  forms, branding, environments and AWS accounts, their migration (Amp: the Salesforce sunset), their one-off repairs,
  and connectors to systems only they have (Amp: device firmware).

## Classification of the current code (from the Amp repo, 2026-11-17)

Counts are references to Amp / partner names and Salesforce migration terms per module.

### Moves to the Amp tenant repo (`opsdesk-tenant-amp`)

| Path | Why |
|---|---|
| `tenants/amp/**` (tenant.json, value_sets, surveys, field_specs, derived_fields, environments, legacy/, logo) | Amp's profile and data |
| `app/connectors/amp_firmware.py` | Amp-only system (device firmware / B2B whitelisting) -> tenant plugin |
| `app/core/rlx_repair.py`, `shipstation_repair.py`, `returns_repair.py` | One-off Amp data repairs -> tenant maintenance plugins |
| `app/core/data_fixes.py` (ops that name Amp records: backfill, remap, seed_dashboards, attach_files, extended_warranty) | Amp reconciliation; the `data_fix` runner stays in core, the ops register from the tenant |
| `app/connectors/salesforce_sync.py`, `sf_field_maps.py`, `sf_full_model.py`, `sf_validations.py`, `core/legacy.py`, `tools/sf_*` | Amp's Salesforce sunset. Generic "legacy CRM import" can come back to core later as a connector |
| Amp pipelines in `app/transform/library.py` / `etl_library.py` (send rules, record rules, surveys -> order, tracking mapping, Klaviyo emails, Magento ETL maps) | Amp business rules -> exported as JSON into `pipelines/` of the tenant repo; core keeps generic starter templates only |
| `app/assistant_library.py` / `assistant_guides.py` Amp question texts, `docs/` Amp guides, `sales/`, `design/` | Tenant content |
| Tests that build the Amp tenant (`tests/_harness.py` copies tenants/amp; e2e_order_lifecycle, jbh payload matrix, sf_reconcile, golive, cutover) | Tenant acceptance tests, run against core + the Amp bundle |
| `deploy` defaults: AWS account 637423233486, image prefix `ampfit`, stage names | Tenant `environments.json` |

### Stays in core, made tenant-neutral

| Path | Change |
|---|---|
| `app/core/models.py` | Partner columns (`jbh_ref_override`, `onward_order_id`, `rlx_job_id`, `booking_request_id`, `jbh_warning`, `fw_*`) stay for now (owned by the J.B. Hunt / Onward / firmware plugins; additive schema rule). Later: a `partner_refs` table |
| `app/transform/engine.py` | `jbh_ready()` / `onward_ready()` / payload functions move behind the 3PL adapter registry (`domain.fulfillment.adapters()`): one generic `tpl_ready(code)`; each 3PL plugin registers its functions |
| `app/connectors/registry.py`, `endpoints.py`, `links.py` | Connector catalog becomes plugin-registered (entry points); core never imports a partner module directly |
| `app/domain/tracking.py` | `start_warranty` reads every SKU / term from tenant settings (done in v1.31) |
| `app/web/orders.py`, `order_view.py`, `public.py`, `common.py` | Partner wording ("J.B. Hunt", "RLX") from the adapter's display name, not literals |
| `app/core/config.py`, `cli.py`, `migrate.py` | Default tenant `amp` removed: the tenant key is required (env `OPSDESK_TENANT`) |
| `app/core/tenant.py` | Platform defaults stay; nothing Amp-specific in `DEFAULTS` |
| `tenants/_template/` | The scaffold for a new tenant repo (see new-tenant-policy.md) |
| `deploy/aws/*` | Takes the tenant bundle path and `environments.json` instead of `--tenant amp` defaults |
| Core tests | Run against `tenants/_template` and a fake second tenant (`demo`) that has none of Amp's settings. Passing them is the proof that core has no Amp dependency |

## How a deploy works after the split

1. Core release: tag `core-vX.Y.Z` in the core repo; CI builds and scans `opsdesk-core:X.Y.Z` (Cython-compiled, no tenant).
2. Tenant repo pins it in `tenant.lock` (`core: X.Y.Z`).
3. Tenant deploy (`opsdesk deploy --stage sandbox|production`, run from the tenant repo): image `FROM opsdesk-core:X.Y.Z`
   + the tenant bundle + tenant plugins -> the tenant's own AWS account; pipelines in `pipelines/*.json` are loaded into
   the tenant database (new and changed system pipelines only; UI-customised ones are kept and reported).
4. Gates, unchanged: local test run on the exact commit (core tests + tenant tests), in-image tests, scan, health check,
   last-good release record, rollback.

## Freshness

- Renovate in each tenant repo opens a PR on every core release; tenant CI runs the tenant tests on it.
- Patch / minor (semver) auto-merge to the tenant's `sandbox` on green CI; production stays a human PR.
- The Hub shows each tenant's pinned core version and how far behind it is.

## Order of work (no change to Amp production until the last step)

1. Core repo with fresh history in `opsdesk-core-staging` (this repo). Copy the platform, leave Amp out.
2. Plugin seams: connector registry, 3PL adapter registry, data_fix op registry, pipeline bundle loader.
3. Core tests green on the `demo` tenant.
4. Amp tenant repo = today's repo slimmed to the bundle + plugins, pinned to the core build; Amp tests green on core + bundle.
5. Amp sandbox deploy from the new build; full e2e.
6. Amp production switch (current image kept as the recovery copy), then folder / repo renames.
