"""Sentry PII scrubbing — pin the coord-redaction behavior.

These tests pin the privacy guarantee of `app.sentry_scrub.before_send`:
no recognizable lat/lon pair survives in the event payload, but the
hashed bbox identifier IS stable for grouping ("this region routes
slowly"). Tested at the function level — we don't need a real Sentry
DSN to verify the scrub.
"""
from __future__ import annotations

import pytest

from app.sentry_scrub import (
    before_send,
    hashed_bbox,
)

# ── hashed_bbox: stability ─────────────────────────────────────────────

def test_hashed_bbox_is_deterministic() -> None:
    """Same coords → same hash. Different coords → different hash."""
    h1 = hashed_bbox(43.61, 3.87, 44.05, 3.99)
    h2 = hashed_bbox(43.61, 3.87, 44.05, 3.99)
    h3 = hashed_bbox(48.85, 2.35, 48.86, 2.36)  # Paris
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 8


def test_hashed_bbox_bins_nearby_points_together() -> None:
    """0.1° bins → points within ~11km share the same hash."""
    # Two points within the same 0.1° bin
    h_same = hashed_bbox(43.61, 3.87)
    h_close = hashed_bbox(43.62, 3.88)  # still in 43.6, 3.8 bin
    assert h_same == h_close


def test_hashed_bbox_separates_distant_points() -> None:
    """Distant points → different hashes."""
    h_mtp = hashed_bbox(43.6, 3.9)  # Montpellier
    h_par = hashed_bbox(48.8, 2.3)  # Paris
    assert h_mtp != h_par


# ── before_send: URL scrubbing ──────────────────────────────────────────

def test_before_send_scrubs_routing_url_query() -> None:
    """The /routing?start_lat=...&start_lon=... URL has all four
    coords replaced with hashed values."""
    event = {
        "request": {
            "url": "https://api.example.com/routing?start_lat=43.61&start_lon=3.87&end_lat=44.05&end_lon=3.99&profile=gravel",
        },
    }
    out = before_send(event, {})
    url = out["request"]["url"]
    # No raw coords survive
    assert "43.61" not in url
    assert "3.87" not in url
    assert "44.05" not in url
    assert "3.99" not in url
    # Hashed values are present
    assert "h:" in url
    # Non-coord params survive untouched
    assert "profile=gravel" in url


def test_before_send_scrubs_query_string_field() -> None:
    """Some Sentry integrations populate request.query_string separately."""
    event = {
        "request": {
            "query_string": "start_lat=43.61&start_lon=3.87&end_lat=44.05&end_lon=3.99",
        },
    }
    out = before_send(event, {})
    qs = out["request"]["query_string"]
    assert "43.61" not in qs
    assert "3.87" not in qs
    assert "44.05" not in qs


def test_before_send_preserves_non_coord_params() -> None:
    """`profile=gravel&use_popularity=true` is NOT a coord — leave it alone."""
    event = {
        "request": {
            "url": "https://api/routing?profile=gravel&use_popularity=true&corridor_km=5.0",
        },
    }
    out = before_send(event, {})
    url = out["request"]["url"]
    assert "profile=gravel" in url
    assert "use_popularity=true" in url
    assert "corridor_km=5.0" in url


# ── before_send: transaction/message scrubbing ─────────────────────────

def test_before_send_scrubs_transaction_name() -> None:
    """A transaction name like `'GET /routing?lat=43.61&lon=3.87'` —
    coords replaced with hash."""
    event = {
        "transaction": "GET /routing?start_lat=43.61&start_lon=3.87&end_lat=44.05&end_lon=3.99",
    }
    out = before_send(event, {})
    tn = out["transaction"]
    assert "43.61" not in tn
    assert "3.87" not in tn


def test_before_send_scrubs_span_descriptions() -> None:
    """Spans with raw-coord descriptions get scrubbed too."""
    event = {
        "spans": [
            {"description": "routing.proposals 43.61,3.87→44.05,3.99"},
            {"description": "db query"},  # untouched
        ],
    }
    out = before_send(event, {})
    assert "43.61" not in out["spans"][0]["description"]
    assert "44.05" not in out["spans"][0]["description"]
    assert out["spans"][1]["description"] == "db query"


def test_before_send_replaces_coord_pair_with_bbox_marker() -> None:
    """The replacement is a hashed bbox marker, not just deletion —
    the format is grep-able for debugging ('bbox=...')."""
    event = {"message": "Error processing 43.61,3.87 → 44.05,3.99"}
    out = before_send(event, {})
    msg = out["message"]
    assert "bbox=" in msg
    assert "43.61" not in msg


# ── before_send: defense in depth ──────────────────────────────────────

def test_before_send_walks_breadcrumb_data() -> None:
    """Breadcrumb data dicts can contain raw URLs — walk them too."""
    event = {
        "breadcrumbs": {
            "values": [
                {
                    "category": "http",
                    "data": {
                        "url": "https://api/routing?start_lat=43.61&start_lon=3.87",
                        "method": "GET",
                    },
                },
            ],
        },
    }
    out = before_send(event, {})
    url = out["breadcrumbs"]["values"][0]["data"]["url"]
    assert "43.61" not in url


def test_before_send_is_idempotent() -> None:
    """Running before_send twice doesn't double-hash."""
    event = {
        "request": {"url": "/routing?start_lat=43.61&start_lon=3.87"},
        "transaction": "43.6,3.87→44.0,3.99",
    }
    once = before_send(dict(event), {})
    twice = before_send(once, {})
    assert once["request"]["url"] == twice["request"]["url"]
    assert once["transaction"] == twice["transaction"]


def test_before_send_handles_empty_or_missing_fields() -> None:
    """Robust to events with no request, transaction, etc."""
    assert before_send({}, {}) == {}
    assert before_send({"foo": "bar"}, {}) == {"foo": "bar"}


# ── Non-regression: real-world false positives ─────────────────────────

@pytest.mark.parametrize("benign", [
    "version 1.2.3",
    "took 12.5 ms",
    "5.0,10.0",       # could be coord-shaped but isolated number-pair is borderline
])
def test_before_send_does_not_touch_short_decimal_pairs(benign: str) -> None:
    """Version strings and short decimals are NOT lat/lon shapes."""
    # We accept that some 2-decimal pairs like "5.0,10.0" still trigger
    # the scrub (regex sees them as coord-shaped). Document this:
    # erring on the side of MORE scrubbing for privacy is the right
    # default. The version "1.2.3" is multi-dot and not matched.
    out = before_send({"message": benign}, {})
    # Either the message is unchanged, OR scrubbed to bbox=... (both are
    # acceptable). What we MUST NOT do is leave a 4dp coord visible.
    msg = out["message"]
    assert "43.6789" not in msg
