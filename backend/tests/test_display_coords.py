"""Precomputed display-coords cache (migration 0065) — non-regression suite.

WHY: every heatmap rebuild used to stream the whole community corpus as
geometry_geojson TEXT off the db-f1-micro TWICE and re-run parse+clip+outlier
per activity. The cache stores the post-clip/outlier polyline as a compact blob
(~15× fewer bytes measured) that the reader trusts ONLY when its parameter
fingerprint matches the current env — else it transparently falls back to the
live pipeline. THE invariant: blob path ≡ live path, byte-for-byte.
"""
import json

import pytest
from sqlalchemy import text as sa_text

from app.db.models import Activity
from app.db.session import SessionLocal
from app.services.raw_trace_display import (
    DISPLAY_COORDS_VERSION,
    decode_display_coords,
    display_coords_fingerprint,
    encode_display_coords,
    iter_masked_runs,
)

# The seeded corpus of test_raw_trace_display lives around lon -40/-42 — reuse
# its fixture + a bbox that scopes iter_masked_runs to JUST those rows, so this
# suite stays fast even on a dev DB carrying a big local corpus.
from tests.test_raw_trace_display import (
    _TEST_USER,  # noqa: F401
    seeded_activities,  # noqa: F401
)

_SEED_BBOX = (-43.0, 43.0, -39.0, 44.5)  # lon/lat box around the seeded lines (LAT=43.61)


def _fill_binary_geometry(db) -> None:
    """The bbox scope in _stream_activities filters on the BINARY ``geometry``
    column, which the ORM fixture leaves NULL (it only sets geometry_geojson).
    Backfill it for the seeded rows so the bbox sees them."""
    db.execute(sa_text(
        "UPDATE activities SET geometry = "
        "ST_SetSRID(ST_GeomFromGeoJSON(geometry_geojson), 4326) "
        "WHERE user_id = :u AND geometry IS NULL"), {"u": _TEST_USER})
    db.commit()


def _seeded_runs(db):
    return [
        (aid, uid, sport, [tuple(p[:2]) for p in run])
        for aid, uid, sport, run in iter_masked_runs(db, bbox=_SEED_BBOX)
    ]


def _backfill_seeded(db) -> int:
    """Targeted backfill of the seeded user's community rows (same logic as
    app.cli.backfill_display_coords, scoped so the test never churns through an
    unrelated dev corpus)."""
    fp = display_coords_fingerprint()
    rows = db.execute(sa_text(
        "SELECT id, geometry_geojson FROM activities WHERE user_id = :u"
    ), {"u": _TEST_USER}).fetchall()
    n = 0
    for aid, gj in rows:
        blob = encode_display_coords(gj)
        if blob is None:
            continue
        db.execute(sa_text(
            "UPDATE activities SET display_coords=:b, display_coords_params=:fp "
            "WHERE id=:id"), {"b": blob, "fp": fp, "id": aid})
        n += 1
    db.commit()
    return n


# ── Unit: the codec ───────────────────────────────────────────────────────────

def test_encode_decode_roundtrip():
    gj = json.dumps({"type": "LineString",
                     "coordinates": [[3.87 + i * 1e-3, 43.61 + i * 1e-3, 12.5]
                                     for i in range(40)]})
    blob = encode_display_coords(gj)
    pts = decode_display_coords(blob)
    assert len(pts) == 40
    # float64 EXACT (byte-identity downstream depends on it) + 2D only.
    assert pts[9] == [3.87 + 9e-3, 43.61 + 9e-3]


def test_decode_rejects_garbage_and_wrong_version():
    assert decode_display_coords(None) is None
    assert decode_display_coords(b"junk") is None
    import struct
    import zlib
    bad = zlib.compress(struct.pack("<BI", DISPLAY_COORDS_VERSION + 1, 0))
    assert decode_display_coords(bad) is None
    truncated = zlib.compress(struct.pack("<BI", DISPLAY_COORDS_VERSION, 3) + b"\x00" * 8)
    assert decode_display_coords(truncated) is None


def test_encode_degenerate_geometry_is_none():
    assert encode_display_coords(None) is None
    assert encode_display_coords("not json") is None
    assert encode_display_coords(json.dumps({"type": "LineString",
                                             "coordinates": [[1.0, 1.0]]})) is None


def test_fingerprint_tracks_params(monkeypatch):
    base = display_coords_fingerprint()
    monkeypatch.setenv("HEATMAP_RAW_MAX_SPAN_KM", "123")
    assert display_coords_fingerprint() != base
    monkeypatch.delenv("HEATMAP_RAW_MAX_SPAN_KM", raising=False)
    monkeypatch.setenv("HEATMAP_RAW_BBOX", "-50,-5,-30,5")
    assert display_coords_fingerprint() != base


# ── THE invariant: blob path ≡ live path ─────────────────────────────────────

@pytest.mark.usefixtures("seeded_activities")
def test_blob_fast_path_yields_identical_runs():
    db = SessionLocal()
    try:
        _fill_binary_geometry(db)
        before = _seeded_runs(db)          # all-NULL cache → live pipeline
        assert before, "seeded corpus must yield runs"
        n = _backfill_seeded(db)
        assert n >= 2                       # the community rows got a blob
        after = _seeded_runs(db)            # fingerprint-current → blob path
    finally:
        db.close()
    assert after == before


@pytest.mark.usefixtures("seeded_activities")
def test_stale_fingerprint_falls_back_to_live_pipeline(monkeypatch):
    """A cache stamped under DIFFERENT params must be ignored (the SQL ships the
    JSON instead) — never half-trusted."""
    db = SessionLocal()
    try:
        _fill_binary_geometry(db)
        _backfill_seeded(db)
        # Stamp every seeded blob with a bogus fingerprint.
        db.execute(sa_text(
            "UPDATE activities SET display_coords_params='v0|stale' "
            "WHERE user_id=:u"), {"u": _TEST_USER})
        db.commit()
        import app.services.raw_trace_display as rtd
        calls = {"decode": 0}
        real = rtd.decode_display_coords
        monkeypatch.setattr(rtd, "decode_display_coords",
                            lambda b: calls.__setitem__("decode", calls["decode"] + 1) or real(b))
        runs = _seeded_runs(db)
        assert runs, "fallback must still yield the corpus"
        assert calls["decode"] == 0, "a stale blob must never be decoded"
    finally:
        db.close()


# ── Writers: ingest + promotion keep the cache fresh ─────────────────────────

@pytest.mark.usefixtures("seeded_activities")
def test_ingest_writes_cache_for_community_rows_only():
    db = SessionLocal()
    try:
        rows = db.execute(sa_text(
            "SELECT source, contribute_heatmap, display_coords "
            "FROM activities WHERE user_id=:u"), {"u": _TEST_USER}).fetchall()
    finally:
        db.close()
    # The fixture inserts via the ORM directly (no cache) — this test documents
    # the WRITER contract through _display_coords_pair instead:
    from app.services.ingest import _display_coords_pair
    gj = json.dumps({"type": "LineString",
                     "coordinates": [[3.87 + i * 1e-3, 43.61] for i in range(10)]})
    blob, fp = _display_coords_pair(gj, "manual_upload", True)
    assert blob is not None and fp == display_coords_fingerprint()
    assert _display_coords_pair(gj, "strava_api", True) == (None, None)
    assert _display_coords_pair(gj, "manual_upload", False) == (None, None)
    assert _display_coords_pair("not json", "manual_upload", True) == (None, None)
    assert rows  # fixture sanity


@pytest.mark.usefixtures("seeded_activities")
def test_promotion_refreshes_cache_with_new_geometry():
    """#453 promotion replaces the geometry — the derived cache must follow it,
    or the map would keep drawing the pre-promotion polyline."""
    from app.services.ingest import _promote_activity_to_community
    db = SessionLocal()
    try:
        aid = db.execute(sa_text(
            "SELECT id FROM activities WHERE user_id=:u AND source='strava_api' "
            "LIMIT 1"), {"u": _TEST_USER}).scalar()
        new_geo = json.dumps({"type": "LineString",
                              "coordinates": [[-41.0 + i * 1e-3, 0.0]
                                              for i in range(400)]})
        _promote_activity_to_community(
            db, str(aid), {"geometry_geojson": new_geo}, True)
        db.commit()
        act = db.query(Activity).filter(Activity.id == aid).first()
        assert act.source == "manual_upload"
        assert act.display_coords is not None
        assert act.display_coords_params == display_coords_fingerprint()
        pts = decode_display_coords(act.display_coords)
        assert pts[0] == [-41.0, 0.0] and len(pts) == 400
    finally:
        db.close()
