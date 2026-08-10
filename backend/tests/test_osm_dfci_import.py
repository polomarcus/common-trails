"""Tests for OSM DFCI import (geometric matching).

Tests the matching logic used by scripts/osm_dfci_import.py.
The geometric functions are defined inline here to avoid depending on
the scripts/ directory (which is not mounted in the Docker container).

Requires pyproj — skipped if not installed (optional dependency).
"""
import csv
from dataclasses import dataclass, field
from io import StringIO

import pytest

pyproj = pytest.importorskip("pyproj", reason="pyproj not installed")
from pyproj import Transformer  # noqa: E402
from shapely.geometry import LineString  # noqa: E402
from shapely.ops import transform  # noqa: E402

# ── Geometric matching functions (mirrored from scripts/osm_dfci_import.py) ──

_to_metric = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)


def _project_to_metric(geom: LineString) -> LineString:
    return transform(_to_metric.transform, geom)


def hausdorff_match(dfci_geom: LineString, osm_geom: LineString) -> float:
    a = _project_to_metric(dfci_geom)
    b = _project_to_metric(osm_geom)
    return a.hausdorff_distance(b)


def overlap_ratio(
    dfci_geom: LineString,
    osm_geom: LineString,
    buffer_m: float = 30.0,
) -> float:
    a = _project_to_metric(dfci_geom)
    b = _project_to_metric(osm_geom)
    buffered = b.buffer(buffer_m)
    intersection = a.intersection(buffered)
    if a.length == 0:
        return 0.0
    return intersection.length / a.length


@dataclass
class DfciRecord:
    ref: str
    geometry: LineString
    statut: str


@dataclass
class MatchResult:
    dfci_ref: str
    status: str
    osm_way_id: int | None = None
    hausdorff_m: float | None = None
    overlap_pct: float | None = None
    existing_tag: str | None = None


@dataclass
class ImportReport:
    results: list[MatchResult] = field(default_factory=list)

    @property
    def matched(self) -> int:
        return sum(1 for r in self.results if r.status == "matched")

    @property
    def already_tagged(self) -> int:
        return sum(1 for r in self.results if r.status == "already_tagged")

    @property
    def no_match(self) -> int:
        return sum(1 for r in self.results if r.status == "no_match")

    @property
    def ambiguous(self) -> int:
        return sum(1 for r in self.results if r.status == "ambiguous")

    def to_csv(self) -> str:
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "dfci_ref", "status", "osm_way_id",
            "hausdorff_m", "overlap_pct", "existing_tag",
        ])
        for r in self.results:
            writer.writerow([
                r.dfci_ref, r.status, r.osm_way_id or "",
                f"{r.hausdorff_m:.1f}" if r.hausdorff_m is not None else "",
                f"{r.overlap_pct:.1f}" if r.overlap_pct is not None else "",
                r.existing_tag or "",
            ])
        return output.getvalue()

    def summary(self) -> str:
        total = len(self.results)
        return (
            f"Total: {total} | Matched: {self.matched} | "
            f"Already tagged: {self.already_tagged} | "
            f"No match: {self.no_match} | Ambiguous: {self.ambiguous}"
        )


def match_dfci_to_osm(
    dfci: DfciRecord,
    candidates: list[dict],
    hausdorff_threshold_m: float = 30.0,
    overlap_threshold: float = 0.7,
) -> MatchResult:
    if not candidates:
        return MatchResult(dfci_ref=dfci.ref, status="no_match")

    best_match = None
    best_score = float("inf")

    for cand in candidates:
        h_dist = hausdorff_match(dfci.geometry, cand["geometry"])
        o_ratio = overlap_ratio(dfci.geometry, cand["geometry"])

        if h_dist < hausdorff_threshold_m and o_ratio >= overlap_threshold and h_dist < best_score:
                best_score = h_dist
                best_match = (cand, h_dist, o_ratio)

    if best_match is None:
        return MatchResult(dfci_ref=dfci.ref, status="no_match")

    cand, h_dist, o_ratio = best_match
    existing = cand["tags"].get("ref:FR:DFCI", "")

    if existing:
        return MatchResult(
            dfci_ref=dfci.ref, status="already_tagged",
            osm_way_id=cand["id"], hausdorff_m=h_dist,
            overlap_pct=o_ratio * 100, existing_tag=existing,
        )

    return MatchResult(
        dfci_ref=dfci.ref, status="matched",
        osm_way_id=cand["id"], hausdorff_m=h_dist,
        overlap_pct=o_ratio * 100,
    )


class TestHausdorffMatch:
    """Test Hausdorff distance calculation."""

    def test_identical_geometries(self):
        geom = LineString([(3.5, 43.5), (3.6, 43.6)])
        dist = hausdorff_match(geom, geom)
        assert dist < 1.0  # < 1m for identical geometries

    def test_close_geometries(self):
        """Parallel lines ~20m apart should have Hausdorff < 30m."""
        dfci = LineString([(3.5, 43.5), (3.6, 43.5)])
        # Offset ~0.0002° latitude ≈ ~22m
        osm = LineString([(3.5, 43.5002), (3.6, 43.5002)])
        dist = hausdorff_match(dfci, osm)
        assert dist < 30.0

    def test_far_geometries(self):
        """Lines 1km apart should have Hausdorff > 100m."""
        dfci = LineString([(3.5, 43.5), (3.6, 43.5)])
        # Offset ~0.01° latitude ≈ ~1.1km
        osm = LineString([(3.5, 43.51), (3.6, 43.51)])
        dist = hausdorff_match(dfci, osm)
        assert dist > 100.0

    def test_perpendicular_lines(self):
        """Perpendicular lines should have large Hausdorff distance."""
        dfci = LineString([(3.5, 43.5), (3.7, 43.5)])
        osm = LineString([(3.6, 43.4), (3.6, 43.6)])
        dist = hausdorff_match(dfci, osm)
        assert dist > 500.0  # Very different directions


class TestOverlapRatio:
    """Test overlap ratio calculation."""

    def test_identical_geometries(self):
        geom = LineString([(3.5, 43.5), (3.6, 43.6)])
        ratio = overlap_ratio(geom, geom, buffer_m=30.0)
        assert ratio > 0.99  # ~100% overlap

    def test_close_parallel(self):
        """Parallel lines 20m apart with 30m buffer → high overlap."""
        dfci = LineString([(3.5, 43.5), (3.6, 43.5)])
        osm = LineString([(3.5, 43.5002), (3.6, 43.5002)])
        ratio = overlap_ratio(dfci, osm, buffer_m=30.0)
        assert ratio > 0.8

    def test_far_apart(self):
        """Lines 1km apart → zero overlap with 30m buffer."""
        dfci = LineString([(3.5, 43.5), (3.6, 43.5)])
        osm = LineString([(3.5, 43.51), (3.6, 43.51)])
        ratio = overlap_ratio(dfci, osm, buffer_m=30.0)
        assert ratio < 0.1

    def test_partial_overlap(self):
        """Lines that share only part of their extent."""
        dfci = LineString([(3.5, 43.5), (3.7, 43.5)])  # Long
        osm = LineString([(3.5, 43.5), (3.55, 43.5)])  # Short, overlapping half
        ratio = overlap_ratio(dfci, osm, buffer_m=30.0)
        assert 0.2 < ratio < 0.6


class TestMatchDfciToOsm:
    """Test the matching pipeline logic."""

    def test_no_candidates(self):
        dfci = DfciRecord(
            ref="D34-A1",
            geometry=LineString([(3.5, 43.5), (3.6, 43.6)]),
            statut="Opérationnel",
        )
        result = match_dfci_to_osm(dfci, [])
        assert result.status == "no_match"
        assert result.dfci_ref == "D34-A1"

    def test_good_match(self):
        """Close geometry without existing tag → matched."""
        dfci = DfciRecord(
            ref="D34-A1",
            geometry=LineString([(3.5, 43.5), (3.6, 43.5)]),
            statut="Opérationnel",
        )
        candidates = [{
            "id": 12345,
            "tags": {"highway": "track"},
            "geometry": LineString([(3.5, 43.5001), (3.6, 43.5001)]),
        }]
        result = match_dfci_to_osm(dfci, candidates)
        assert result.status == "matched"
        assert result.osm_way_id == 12345
        assert result.hausdorff_m is not None
        assert result.hausdorff_m < 30.0
        assert result.overlap_pct is not None
        assert result.overlap_pct > 70.0

    def test_already_tagged(self):
        """Close geometry with existing ref:FR:DFCI → already_tagged."""
        dfci = DfciRecord(
            ref="D34-A1",
            geometry=LineString([(3.5, 43.5), (3.6, 43.5)]),
            statut="Opérationnel",
        )
        candidates = [{
            "id": 12345,
            "tags": {"highway": "track", "ref:FR:DFCI": "D34-A1"},
            "geometry": LineString([(3.5, 43.5001), (3.6, 43.5001)]),
        }]
        result = match_dfci_to_osm(dfci, candidates)
        assert result.status == "already_tagged"
        assert result.existing_tag == "D34-A1"

    def test_far_candidate_no_match(self):
        """Distant candidate → no_match."""
        dfci = DfciRecord(
            ref="D34-A1",
            geometry=LineString([(3.5, 43.5), (3.6, 43.5)]),
            statut="Opérationnel",
        )
        candidates = [{
            "id": 99999,
            "tags": {"highway": "track"},
            "geometry": LineString([(3.5, 43.6), (3.6, 43.6)]),  # ~11km away
        }]
        result = match_dfci_to_osm(dfci, candidates)
        assert result.status == "no_match"


class TestImportReport:
    """Test report generation."""

    def test_summary_counts(self):
        report = ImportReport(results=[
            MatchResult(dfci_ref="A", status="matched", osm_way_id=1),
            MatchResult(dfci_ref="B", status="matched", osm_way_id=2),
            MatchResult(dfci_ref="C", status="already_tagged", osm_way_id=3),
            MatchResult(dfci_ref="D", status="no_match"),
            MatchResult(dfci_ref="E", status="ambiguous"),
        ])
        assert report.matched == 2
        assert report.already_tagged == 1
        assert report.no_match == 1
        assert report.ambiguous == 1
        assert "Total: 5" in report.summary()

    def test_csv_output(self):
        report = ImportReport(results=[
            MatchResult(
                dfci_ref="D34-A1",
                status="matched",
                osm_way_id=12345,
                hausdorff_m=15.3,
                overlap_pct=85.0,
            ),
            MatchResult(dfci_ref="D34-B2", status="no_match"),
        ])
        csv_str = report.to_csv()
        reader = csv.reader(StringIO(csv_str))
        rows = list(reader)
        assert rows[0] == [
            "dfci_ref",
            "status",
            "osm_way_id",
            "hausdorff_m",
            "overlap_pct",
            "existing_tag",
        ]
        assert rows[1][0] == "D34-A1"
        assert rows[1][1] == "matched"
        assert rows[1][2] == "12345"
        assert rows[2][0] == "D34-B2"
        assert rows[2][1] == "no_match"

    def test_empty_report(self):
        report = ImportReport()
        assert report.matched == 0
        assert report.no_match == 0
        assert "Total: 0" in report.summary()
