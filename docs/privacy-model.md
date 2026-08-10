# Privacy model — Common Trails / Chemins Communs

**Status: definitive decision doc.** Records what the community heatmap
collects, what it publishes, how individuals are protected, the exposures that
remain, and the concrete posture chosen for the private-beta launch.

Companion docs: [`raw-trace-cutover-runbook.md`](raw-trace-cutover-runbook.md)
(the staged, Paul-gated flip to the raw-trace display) and
[`gdpr-deletion-runbook.md`](gdpr-deletion-runbook.md) (account deletion).

Decided by Paul (2026-07): *raw precise traces + endpoint masking + a tunable
distinct-user gate*, replacing the earlier "aggregate + K-anonymise" model. This
doc reflects that decision honestly, including where it is weaker than
K-anonymity.

---

## 1. What we collect

| Data | Purpose | Visibility |
|---|---|---|
| **GPS traces** (GPX / FIT, or a Strava archive export) | the user's personal map + the community popularity map | private by default; only the masked geometry of `manual_upload` traces is published (see §3) |
| **Activity metadata** (name, sport, distance, elevation, moving time) | personal stats + sport classification | private |
| **Email address** | passwordless magic-link login | private, never published, never sold |
| **Technical logs** (IP, user-agent) | server operation / abuse defence | server logs, retained 30 days |

We do **not** run advertising, behavioural profiling, third-party analytics
cookies, or data resale. The only cookie is the auth JWT.

## 2. Legal basis

- **Explicit consent** (GDPR Art. 6(1)(a)) for contributing traces to the
  public map. Consent is a specific, unbundled checkbox on the upload path; the
  exact wording the user saw is stored verbatim in `contribution_consents`
  (version-stamped — currently `contribution-2026-07-v3`), so the audit trail
  proves *what* each user agreed to.
- **Contract / legitimate interest** for running the account itself (login,
  showing the user their own traces).
- The published community dataset is released under the **ODbL 1.0** open
  licence — reusable and downloadable by anyone, share-alike.

## 3. What is published (and what is NOT)

The community popularity map is built **only** from traces whose
`source = 'manual_upload'` — i.e. files a user deliberately uploaded with
consent (loose GPX/FIT, or their own Strava *archive export*).

**Excluded from publication, by construction:**

- Activities pulled through the **Strava API** (`source = 'strava_api'`) — the
  Strava 2026 API policy forbids redistributing API data into a public map.
  These stay in the user's *personal* view only.
- Legacy traces with `source = NULL`.

This gate is enforced **in code, at read time**: the raw build path
(`backend/app/services/raw_trace_display.py`) and the matched path both filter on
the provenance SSOT (`backend/app/services/provenance.py`,
`COMMUNITY_SOURCE` / `is_community_source`). Flipping the display to raw
therefore *cannot* leak personal Strava-API data. (Corollary: until users upload
their own archives, the public map is sparse — that is expected, not a bug.)

## 4. The protections

Two independent mechanisms, both always applicable to the published map:

1. **Endpoint masking (always on).** The start *and* end of every published
   activity are trimmed along-track by `TRACE_MASK_METERS` (**default 200 m**).
   An out-and-back ride has *both* physical ends masked. This is designed to
   hide the two places most likely to be home/work — the trailhead and the
   finish.
2. **Tunable distinct-user gate `HEATMAP_MIN_USERS`.** A fine display cell is
   only rendered once at least *K* **distinct users** have passed through it.
   - **K = 1 during the private beta** — everything shows, including solo
     traces. This is the honest current setting.
   - **K = 2 before any public announcement** (target trigger: ~50 contributors,
     or immediately before opening the map to the public — whichever comes
     first). One env var + a PMTiles rebuild; single-user pixels then disappear.

Provenance (§3) is the third protection: personal Strava-API data never reaches
the map at all.

## 5. Residual exposures — stated plainly

The raw-trace model is deliberately weaker than the old K-anonymity aggregation.
The honest exposures:

- **Under K = 1, an individual's precise mid-route trace is visible** on the
  public map and **downloadable under ODbL**. A solo rider's route line is drawn
  as-is (minus its two endpoints). This is the core trade Paul chose ("on oublie
  K, on fait confiance à l'avenir") — it must not be presented as anonymised.
- **The K gate protects a CELL, not a ROUTE.** `HEATMAP_MIN_USERS` is *cell-K*:
  it decides whether a ~5 m cell is drawn, based on how many distinct users
  crossed *that cell*. It does **not** give *route-K* — even at K = 2, wherever
  an individual's cells happen to clear the gate, that individual's distinct
  route line is still traced through them.
- **Masking is endpoint-only.** A loop that leaves home, rides out, and returns
  *past* home mid-route still exposes the home area at the mid-route pass — only
  the very first and very last `TRACE_MASK_METERS` are trimmed.
- **ODbL is irrevocable for data already published.** A user can delete their
  traces from our systems (§7), but anyone who downloaded the ODbL dataset while
  a trace was public keeps their copy. Deletion stops *future* publication and
  removes it from *our* map; it cannot recall third-party copies.

## 6. Posture for the K = 1 ramp — decision

**Ratified decision (Paul, 2026-07): the community MAP is PUBLICLY VIEWABLE; the
community DATA EXPORT is members-only.** Privacy for the raw K = 1 traces rests
on **endpoint masking + the `HEATMAP_MIN_USERS` (K) gate (both in §4)** — NOT on
gating who can *look* at the map.

Concretely:

- **The map is public.** Anonymous visitors can browse the heatmap on the home
  hero and on `/map`. Making it visible "pour donner envie" is a deliberate
  growth lever: see the map freely → sign up → contribute. The PMTiles display
  asset is served publicly to match.
- **The community ODbL EXPORT is the one members-only affordance.** The bulk
  ODbL community-data download (`ExportHeatmapModal`) requires a login: an
  anonymous user who opens it gets a passwordless login CTA; a logged-in user
  exports normally. This is the conversion point — you can look, but to *take*
  the dataset you become a member (and are nudged to contribute back). The
  user's OWN drawn-route GPX export is personal data and is never gated.

Options considered and **rejected**:

- **Login-gate the whole map until K ≥ 2 — REJECTED.** An earlier iteration
  gated all of `/map` behind login. Paul reversed it: hiding the map defeats the
  "donner envie" growth goal, and the map's privacy properties come from masking
  + K, not from who can view it. (This is why the map is public even at K = 1.)
- **Ship K = 2 from day one — not yet.** Strongest privacy, but on a
  single-/few-user beta corpus the map is nearly empty (nothing has 2 distinct
  users yet), which defeats the point of the beta. K = 2 is the pre-public-launch
  target (§4, §10), not the beta setting.

**In force: public map render + members-only ODbL bulk export + endpoint masking
always on + K = 1 for the private beta → K = 2 before any public announcement.**
Individual precise traces are visible on the public map at K = 1; this is stated
plainly in §5 and must never be presented as anonymised. Relaxing/tightening is a
single env var (`HEATMAP_MIN_USERS`) + a PMTiles rebuild.

## 7. User rights

- **Per-activity deletion** — `DELETE /me/activities/{id}` (implemented,
  `backend/app/api/me_activities.py`). Removes the activity *and* its
  contribution to the community heatmap data; the rendered map updates on the
  next regeneration.
- **Account deletion** — full erasure of the account and all its data; procedure
  in [`gdpr-deletion-runbook.md`](gdpr-deletion-runbook.md).
- **Consent withdrawal** — revoking consent stops future publication; combine
  with deletion to remove already-contributed traces from our map.
- Standard GDPR rights (access, rectification, portability, objection) as listed
  on the in-app privacy page.

Caveat (§5): deletion cannot recall ODbL copies third parties already downloaded.

## 8. Retention

- **Uploaded archives** (the raw `.zip`/GPX/FIT in the intake bucket): retained
  **730 days**, then lifecycle-expired. The derived `activities` rows persist for
  as long as the account is active (or until per-activity/account deletion).
- **Technical logs**: 30 days.
- **Account data**: retained while the account is active; erased on account
  deletion.

## 9. Recommended posture for the private-beta launch

1. `HEATMAP_DISPLAY_SOURCE = raw`, `HEATMAP_MIN_USERS = 1`,
   `TRACE_MASK_METERS = 200` (endpoint masking ON).
2. **Public map, members-only export** (§6) — the heatmap map is publicly
   viewable by anonymous visitors (home hero + `/map`); only the bulk ODbL
   community-data export requires a login. Endpoint masking + K = 1 are the
   privacy mechanisms, not a page gate.
3. Ship the **reworded consent** (v3 — drops the false "anonymisées" claim; see
   the cutover runbook step (e)) *with* the cutover, not before — the wording
   must be true of the behaviour actually live.
4. Keep the matching substrate live during the soak (raw display is reversible
   to matched); do not drop `osm_road_edges` / `heat_edges` until raw has soaked
   and Paul explicitly approves the one-way steps.

## 10. Checklist to flip to public

- [ ] Contributor base large enough that K = 2 renders a useful map (~50 users,
      or as Paul judges).
- [ ] `HEATMAP_MIN_USERS = 2` set + PMTiles rebuilt (single-user pixels gone).
- [ ] Confirm endpoint masking is on and `TRACE_MASK_METERS` is at the chosen
      value for public.
- [ ] The map is already public (no page gate to lift). Re-evaluate whether the
      members-only ODbL bulk export can be opened wider once K = 2 is live.
- [ ] Consent wording (v3) is live and matches the behaviour; decide with legal
      counsel whether users who consented under an older version need re-consent.
- [ ] Privacy page reflects the raw model (done in this change): no "K=2
      minimum / impossible to reconstruct an individual trace" claims remain.
- [ ] Confirm no `source = 'strava_api'` / `NULL` traces are in the published
      set (provenance gate green).
- [ ] Public announcement only after all boxes are checked.
