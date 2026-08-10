# Email login — Resend domain verification (Paul's one-time setup)

Phase 1 magic-link login is wired and deployed-ready, but **no email will
deliver to real users** until the sending domain `chemins-communs.fr` is
verified in Resend. Until then, `EMAIL_FROM` uses Resend's shared test sender
`onboarding@resend.dev`, which **only delivers to the Resend account owner's
address** — perfect for your own smoke test, useless for a friend.

## What's already done (code side)

- Secret `RESEND_API_KEY` is in GCP Secret Manager (version 1) and wired into
  `scripts/deploy-prod.sh` (`--set-secrets`).
- `EMAIL_FROM` is set in `deploy-prod.sh`'s env block to the test sender.
- `backend/app/services/email.py` sends via the Resend HTTP API; no-ops in
  `TEST_MODE` so tests/dev never send.

## Steps for you (~10 min + DNS propagation)

1. **Add the domain in Resend** → https://resend.com/domains → *Add Domain* →
   `chemins-communs.fr`. Pick the EU region (matches Cloud Run `europe-west1`).
2. **Copy the DNS records Resend shows** — typically:
   - an **SPF** `TXT` on a subdomain (e.g. `send.chemins-communs.fr`) →
     `v=spf1 include:amazonses.com ~all`
   - a **DKIM** `TXT` (`resend._domainkey…`) with the public key
   - an **MX** on the same `send` subdomain (for bounce handling)
   - a **DMARC** `TXT` on `_dmarc.chemins-communs.fr` →
     `v=DMARC1; p=none;` (start permissive; tighten to `quarantine` later)
3. **Create those records at your DNS registrar** for `chemins-communs.fr`.
4. **Click *Verify*** in Resend. Verification usually completes within minutes
   (can take up to a few hours for DNS to propagate).
5. **Flip `EMAIL_FROM`** to a verified address, in TWO places (keep them in
   sync):
   - `scripts/deploy-prod.sh` — the `EMAIL_FROM:` line in the env block, e.g.
     `EMAIL_FROM: "Chemins Communs <bonjour@chemins-communs.fr>"`
   - then redeploy: `DEPLOY_YES=1 scripts/deploy-prod.sh <IMAGE_TAG>`
     (or just re-run the deploy — `EMAIL_FROM` is a plain env var, no rebuild
     needed).
6. **Smoke test**: on chemins-communs.fr, enter your email → you should get the
   login link within seconds. Click it → you land signed in on `/strava`.

## Notes

- The magic link is single-use and valid 15 min (`MAGIC_LINK_TTL_MIN`).
- Rate limit: ≤3 requests per email and per IP per 15 min.
- If an email doesn't arrive, check the Resend dashboard *Logs* — an unverified
  domain or a recipient other than the account owner (on the test sender) shows
  up there as rejected.
