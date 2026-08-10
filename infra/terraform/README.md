# ⛔ Terraform — RETIRED / NON-AUTHORITATIVE (2026-08-05)

**Do NOT `terraform apply` this. Prod is NOT managed by terraform.**

The `.tf` files here have been renamed to `*.tf.archived` on purpose, so
`terraform init/plan/apply` finds no configuration and cannot run. They are kept
only as **reference documentation** of the intended topology.

## Why it was retired

- **Prod's SSOT is [`scripts/deploy-prod.sh`](../../scripts/deploy-prod.sh) + `gcloud`.** The CD workflow
  [`deploy-gcp.yml`](../../.github/workflows/deploy-gcp.yml) deploys via that script on every push to `main` —
  it deliberately does NOT run terraform.
- **The terraform state (`gs://common-trails-tfstate`) is dangerously stale.** It
  still references the OLD Cloud SQL instance that was DELETED + recreated
  out-of-band during the 2026-08-04 downgrade (42→10 GB), plus 6 dead
  matched-era Cloud Run jobs and dead Cloud Tasks queues that were removed at the
  raw-trace pivot. **Any `terraform apply` would RECREATE all of them — the
  ~€65/mo trap.** See the drift analysis: memory
  `reference_terraform_reconcile_delta_2026_08_05`.
- Keeping a parallel IaC that no deploy path uses = perpetual drift risk for zero
  benefit. Paul's decision 2026-08-05: retire it.

## Consequences (already handled in this change)

- The `Makefile` no longer reads the DB password from terraform state — it reads
  it from the `DATABASE_URL` Secret Manager secret (`gcloud secrets versions
  access`). `DATA_BUCKET` is a fixed literal (`common-trails-common-trails-data-prod`).
- Nothing in `Makefile` / `scripts/` / `.github/workflows/` invokes terraform
  anymore (only historical comments remain).

## If you ever want terraform back (supervised only)

Reconciling is real work — do NOT just rename the files back and apply:
1. Rename `*.tf.archived` → `*.tf`.
2. `export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token paleclercq@gmail.com)` (ADC on this machine is the forbidden okeiro account).
3. `terraform init`, then `terraform plan` — expect a large destroy/recreate diff.
4. `terraform state rm` the deleted resources / `terraform import` the real
   `common-trails-prod` SQL instance + the live jobs/queues, until `plan` is
   CLEAN (no create/destroy of live resources).
5. ONLY THEN apply. Full delta + step list: `reference_terraform_reconcile_delta_2026_08_05`.
