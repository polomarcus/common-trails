"""Tests for GET /me/stats_by_sport (PR #310 — db-pool-pressure bundle).

Replaces the N round-trips pattern (one /me/stats?sport=X per sport)
on the frontend with a single GROUP BY query. This test pins:

1. The new endpoint exists and returns the documented shape.
2. The per-sport breakdown matches the existing /me/stats?sport=X calls
   for the same fixtures (proves we didn't regress the math).
3. The totals block matches /me/stats (no filter).
"""
import gpxpy
import gpxpy.gpx


def _make_gpx_bytes(name: str, sport_offset: int = 0, n_points: int = 6) -> bytes:
    gpx = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(track)
    seg = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(seg)
    for i in range(n_points):
        seg.points.append(
            gpxpy.gpx.GPXTrackPoint(
                latitude=43.62 + (i + sport_offset) * 0.001,
                longitude=3.87 + (i + sport_offset) * 0.001,
                elevation=50 + i * 5,
            )
        )
    return gpx.to_xml().encode()


def _upload(client, headers, sport: str, name: str, offset: int = 0) -> None:
    gpx = _make_gpx_bytes(name, sport_offset=offset)
    resp = client.post(
        "/gpx/upload",
        headers=headers,
        files={"file": (f"{name}.gpx", gpx, "application/gpx+xml")},
        data={"sport": sport, "contribute_heatmap": "false"},
    )
    assert resp.status_code == 202, resp.text


class TestMeStatsBySport:
    def test_empty_user_returns_zeros(self, client, auth_headers):
        resp = client.get("/me/stats_by_sport", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()

        # Documented shape: total + 5 sport keys
        for key in ("total", "road", "gravel", "mtb", "offroad", "running"):
            assert key in data, f"missing key {key} in {data}"
            block = data[key]
            assert block["activity_count"] == 0
            assert block["total_distance_m"] == 0.0
            assert block["total_elevation_gain_m"] == 0.0
            assert block["unique_cells"] == 0

        assert data["pending_gps_upgrade"] == 0

    def test_requires_auth(self, client):
        resp = client.get("/me/stats_by_sport")
        assert resp.status_code == 401

    def test_matches_per_sport_endpoint(self, client, auth_headers):
        """The aggregated endpoint must return the same numbers as the
        per-sport /me/stats?sport=X calls. This is the "didn't regress
        the math" invariant.
        """
        # Upload a mix of sports (offset different so they have distinct cells)
        _upload(client, auth_headers, "road", "road-1", offset=0)
        _upload(client, auth_headers, "road", "road-2", offset=20)
        _upload(client, auth_headers, "gravel", "gravel-1", offset=40)
        _upload(client, auth_headers, "mtb", "mtb-1", offset=60)

        agg = client.get("/me/stats_by_sport", headers=auth_headers)
        assert agg.status_code == 200
        agg_data = agg.json()

        # Compare each sport
        for sport in ("road", "gravel", "mtb"):
            per = client.get(f"/me/stats?sport={sport}", headers=auth_headers)
            assert per.status_code == 200
            per_data = per.json()
            block = agg_data[sport]
            assert block["activity_count"] == per_data["activity_count"], (
                f"sport={sport}: agg={block} per={per_data}"
            )
            assert block["total_distance_m"] == per_data["total_distance_m"], (
                f"sport={sport}: distance mismatch"
            )
            assert block["total_elevation_gain_m"] == per_data["total_elevation_gain_m"], (
                f"sport={sport}: elevation mismatch"
            )
            assert block["unique_cells"] == per_data["unique_cells"], (
                f"sport={sport}: unique_cells mismatch"
            )

        # Empty sports should still be zeros
        assert agg_data["offroad"]["activity_count"] == 0
        assert agg_data["running"]["activity_count"] == 0

    def test_total_matches_unfiltered_stats(self, client, auth_headers):
        _upload(client, auth_headers, "road", "r1", offset=0)
        _upload(client, auth_headers, "gravel", "g1", offset=30)

        agg = client.get("/me/stats_by_sport", headers=auth_headers).json()
        totals = client.get("/me/stats", headers=auth_headers).json()

        assert agg["total"]["activity_count"] == totals["activity_count"]
        assert agg["total"]["total_distance_m"] == totals["total_distance_m"]
        assert agg["total"]["total_elevation_gain_m"] == totals["total_elevation_gain_m"]
        # unique_cells is computed across all sports → matches the unfiltered call
        assert agg["total"]["unique_cells"] == totals["unique_cells"]

    def test_unknown_sport_doesnt_pollute_road_bucket(self, client, auth_headers):
        """Activities with sport values outside the 5 standard buckets
        (legacy data, NULL sport, historical 'cycling') must NOT silently
        fold into the road bucket — but they MUST still count toward
        total. The per-sport breakdown only reflects sports the UI
        renders; total is honest about everything in the user's library.
        """
        # Two valid road uploads
        _upload(client, auth_headers, "road", "road-A", offset=0)
        _upload(client, auth_headers, "road", "road-B", offset=10)

        # Inject one activity with a non-standard sport directly via the DB.
        # Bypasses /gpx/upload's sport validation — simulates legacy / migrated
        # rows where sport was stored as something we don't model anymore.
        import uuid

        from app.db.models import Activity
        from app.db.session import SessionLocal
        user_id = client.get("/auth/me", headers=auth_headers).json()["user_id"]
        db = SessionLocal()
        try:
            db.add(Activity(
                id=str(uuid.uuid4()),
                user_id=user_id,
                provider="file",
                sport="kayaking",  # ← intentionally outside the 5
                name="legacy-kayak",
                distance_m=8000,
                elevation_gain_m=10,
                contribute_heatmap=False,
            ))
            db.commit()
        finally:
            db.close()

        agg = client.get("/me/stats_by_sport", headers=auth_headers).json()

        # Road bucket reflects ONLY the two road uploads — kayak must not leak in.
        assert agg["road"]["activity_count"] == 2, (
            f"kayaking activity leaked into road bucket: {agg['road']}"
        )
        # Total counts every activity, kayak included.
        assert agg["total"]["activity_count"] == 3, (
            f"Expected 3 activities in total (2 road + 1 kayak), got {agg['total']}"
        )
        assert agg["total"]["total_distance_m"] >= 8000, (
            f"Total distance should include the kayak's 8 km, got {agg['total']}"
        )
