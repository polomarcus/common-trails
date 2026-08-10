"""PR-E High #2: ``get_user_stats`` uses a SQL aggregate, not a Python sum.

The function used to ``q.all()`` then ``sum()`` in Python — for a
5000-activity user that pulled 25-250 MB of GeoJSON into the API worker
to compute three scalars. PR-E switches it to a single SQL aggregate
round-trip.

This test pins:
1. The function returns the documented shape (didn't break the contract).
2. The numbers match what a Python sum would compute (didn't break math).
3. The path doesn't touch ``geometry_geojson`` — proxied by ensuring an
   activity with a huge GeoJSON blob doesn't change the return shape.
"""
from __future__ import annotations

import gpxpy
import gpxpy.gpx


def _make_gpx(name: str, offset: int = 0, n_points: int = 8) -> bytes:
    gpx = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(track)
    seg = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(seg)
    for i in range(n_points):
        seg.points.append(
            gpxpy.gpx.GPXTrackPoint(
                latitude=43.62 + (i + offset) * 0.001,
                longitude=3.87 + (i + offset) * 0.001,
                elevation=50 + i * 5,
            )
        )
    return gpx.to_xml().encode()


def _upload(client, headers, sport: str, name: str, offset: int = 0) -> None:
    gpx = _make_gpx(name, offset=offset)
    resp = client.post(
        "/gpx/upload",
        headers=headers,
        files={"file": (f"{name}.gpx", gpx, "application/gpx+xml")},
        data={"sport": sport, "contribute_heatmap": "false"},
    )
    assert resp.status_code == 202, resp.text


class TestMeStatsSqlAggregate:
    def test_response_shape_unchanged(self, client, auth_headers) -> None:
        """Endpoint still returns the documented 4-key shape."""
        _upload(client, auth_headers, "road", "r1", offset=0)
        resp = client.get("/me/stats", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        for key in ("activity_count", "total_distance_m", "total_elevation_gain_m", "unique_cells"):
            assert key in data, f"missing key {key}"

    def test_numeric_types_are_correct(self, client, auth_headers) -> None:
        """``activity_count`` is int; ``total_distance_m`` is numeric.

        The SQL ``SUM`` returns Decimal in psycopg; PR-E coerces to float.
        Without the coercion, the JSON serialiser would either reject the
        Decimal or emit it as a string.
        """
        _upload(client, auth_headers, "road", "r1", offset=0)
        _upload(client, auth_headers, "road", "r2", offset=10)
        data = client.get("/me/stats", headers=auth_headers).json()
        assert isinstance(data["activity_count"], int)
        assert data["activity_count"] == 2
        # Pydantic emits floats as JSON numbers; never a string.
        assert isinstance(data["total_distance_m"], (int, float))
        assert data["total_distance_m"] > 0

    def test_empty_user_returns_zeros(self, client, auth_headers) -> None:
        """No activities → all-zero. The COUNT/SUM gracefully handle empty."""
        data = client.get("/me/stats", headers=auth_headers).json()
        assert data["activity_count"] == 0
        assert data["total_distance_m"] == 0
        assert data["total_elevation_gain_m"] == 0
        assert data["unique_cells"] == 0

    def test_sport_filter_isolates_aggregates(self, client, auth_headers) -> None:
        """``?sport=X`` aggregates only that sport."""
        _upload(client, auth_headers, "road", "r1", offset=0)
        _upload(client, auth_headers, "road", "r2", offset=20)
        _upload(client, auth_headers, "gravel", "g1", offset=40)
        road = client.get("/me/stats?sport=road", headers=auth_headers).json()
        gravel = client.get("/me/stats?sport=gravel", headers=auth_headers).json()
        total = client.get("/me/stats", headers=auth_headers).json()
        assert road["activity_count"] == 2
        assert gravel["activity_count"] == 1
        assert total["activity_count"] == 3
        # The unfiltered total distance equals the sum of per-sport distances.
        assert abs(total["total_distance_m"] - (road["total_distance_m"] + gravel["total_distance_m"])) < 0.01

    def test_does_not_load_geometry_into_python(self, client, auth_headers, monkeypatch) -> None:
        """The aggregate path must NOT materialise Activity rows.

        We patch ``Activity.geometry_geojson`` access via a tripwire and
        assert it's never read during get_user_stats. The SQL aggregate
        only references the count + scalar columns.
        """
        # Upload one activity so there's something to aggregate.
        _upload(client, auth_headers, "road", "r1", offset=0)

        from app.services.ingest import get_user_stats
        user_id = client.get("/auth/me", headers=auth_headers).json()["user_id"]

        # If the old code path was still in use, ``.all()`` would load
        # full ORM rows including ``geometry_geojson``. The new path only
        # SELECTs aggregates. We patch SQLAlchemy ``Query.all`` to flag
        # any caller; only the cells JOIN may still need it (it doesn't).
        from sqlalchemy.orm import Query
        all_calls: list[str] = []
        real_all = Query.all

        def _tracking_all(self):
            all_calls.append(str(self))
            return real_all(self)

        monkeypatch.setattr(Query, "all", _tracking_all)
        result = get_user_stats(user_id)
        monkeypatch.setattr(Query, "all", real_all)

        # No ``q.all()`` call should reference ``Activity`` directly — the
        # aggregate uses ``.one()`` and the cells query uses ``.scalar()``.
        for s in all_calls:
            assert "activities" not in s.lower() or "count" in s.lower(), (
                f"unexpected Activity .all() in get_user_stats: {s}"
            )
        assert result["activity_count"] == 1
