"""Activity provenance — the SSOT for the PUBLIC ⟷ PERSONAL data split.

WHY (compliance): under Strava's 2026 API Policy, data obtained via the
Strava API may NOT feed a public/community heatmap or an ODbL export
(§5.4 / §5.10). It may only power a user's OWN personal view (§2.3). A
user's OWN activity archive — exported by them via Strava's official
"request your data" flow and uploaded here by choice, with consent — is a
DIFFERENT, legally-defensible basis for contributing to the open (ODbL)
community heatmap.

So every activity carries a provenance ``source`` (migration 0059):

* ``"manual_upload"`` — the user's own uploaded GPX / folder / consented
  Strava archive. The ONLY source eligible for the community heatmap.
* ``"strava_api"``    — pulled via the Strava OAuth API (bulk import,
  webhook, resync). Personal-only; MUST NOT feed the community layer.
* ``None``            — legacy / pre-migration rows (unknown provenance).
  Treated as NOT community-eligible: those rows originated from the
  Strava API import that pre-dated this split, so excluding them is the
  compliance-safe default. (Consequence: the current prod ``heat_edges``,
  built entirely from Strava-API syncs, must be REBUILT from
  manual_upload uploads before the public map is repopulated — an ops
  step, see docs/strava/community-contribution-ux.md §5.)

DESIGN — filter at INGEST, not at read (option (b)). The community layer
is ``heat_edges`` / ``heat_edges_agg``. Rather than add a ``source``
dimension to those aggregate tables (which mix many users per way and
would need per-source counts), we gate CONTRIBUTION: only
community-eligible activities ever write to ``heat_edges``. Then
``heat_edges`` == "the community layer" BY CONSTRUCTION, and every reader
(``build_pmtiles``, the live ``/heatmap/tiles`` MVT, ``heat_edges_agg``,
the GeoJSON/KML exports) is correct with ZERO SQL changes. The personal
view reads the user's OWN rows from ``activities`` directly (never
``heat_edges``), so it still shows strava_api rides — unaffected.
"""
from __future__ import annotations

# Provenance values stored in ``activities.source`` (migration 0059).
COMMUNITY_SOURCE = "manual_upload"
STRAVA_API_SOURCE = "strava_api"


def is_community_source(source: str | None) -> bool:
    """True iff an activity with this ``source`` may feed the community heatmap.

    ONLY ``manual_upload`` qualifies. ``strava_api`` and legacy ``None``
    are excluded (see module docstring for the legal + operational why).
    """
    return source == COMMUNITY_SOURCE


def community_eligible_conditions(
    source_param: str = "community_source",
) -> tuple[list[str], dict]:
    """SQL WHERE conditions (+ bind params) selecting community-eligible activities.

    The SSOT for "which ``activities`` rows feed the community layer", so every
    consumer (the raw-trace display build, the community stats banner, exports)
    filters IDENTICALLY and can never drift. An activity is community-eligible iff:

    * ``source = 'manual_upload'`` (``COMMUNITY_SOURCE``) — the ONLY eligible
      provenance (``strava_api`` + legacy ``NULL`` are personal-only, §5.4/§5.10);
    * ``geometry_geojson IS NOT NULL`` — it actually has a trace to draw/count;
    * ``contribute_heatmap = true`` — the user consented to contribute it.

    Returns the conditions as a LIST so callers can append their own scoping
    (bbox, date window) before ``" AND ".join(...)``. ``source_param`` names the
    bind parameter so a caller can avoid collisions.
    """
    return (
        [
            "geometry_geojson IS NOT NULL",
            "contribute_heatmap = true",
            f"source = :{source_param}",
        ],
        {source_param: COMMUNITY_SOURCE},
    )


def should_promote_to_community(
    incoming_source: str | None,
    existing_source: str | None,
) -> bool:
    """Decide whether a cross-source duplicate should PROMOTE the existing row.

    Context (③ cross-source idempotence): the same physical ride can
    arrive both via the Strava API (personal, ``strava_api``) and via a
    manual archive/GPX upload (community, ``manual_upload``) — with NO
    shared ``provider_activity_id`` (raw GPX has none). They are matched
    by the (user, start-time, distance) heuristic in ``ingest_activity``.

    When the community-eligible copy arrives and the already-stored twin
    is NOT community-eligible, we must not simply drop the upload (that
    would leave the ride out of the community layer forever). Instead we
    PROMOTE the existing row to ``manual_upload`` and replace its
    geometry with the user's authoritative GPX — a single row, counted
    ONCE in the community, fed by the uploaded (not Strava-API) geometry.

    Returns True only for ``manual_upload`` incoming over a non-community
    existing row. All other combinations keep the plain-dedup behaviour
    (skip), so a strava_api sync landing after an existing manual_upload
    row never touches the community copy.
    """
    return is_community_source(incoming_source) and not is_community_source(existing_source)
