"""Sentry PII scrubbing — strip raw lat/lon from events and span names.

Sentry's FastAPI integration captures request URLs by default, and span
names set manually can contain coordinates. Raw coordinates are PII for a
personal home location (a 4dp coord identifies a 11m square — same as a
street address, often the user's home). This module scrubs anything that
looks like a lat/lon pair before it leaves the process.

Scrub strategy:
- Query params: replace numeric values of start_lat/start_lon/end_lat/end_lon
  (and lat/lon variants) with a per-event hashed bbox identifier.
- Span name: same — anywhere a "{float},{float}" pair appears with one
  in the lat range and the other in the lon range.
- Free-form messages: same regex.

The hashed bbox identifier is a deterministic 8-char hex from a 0.1°
binned coordinate — still useful for grouping ("this region has flaky
routes") but no longer identifies any individual.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

# Latitude/longitude pair: two floats with one in [-90,90] and the other
# in [-180,180]. We match conservatively: any "<float>,<float>" with at
# least one decimal point and both values within the geographic ranges.
_COORD_PAIR_RE = re.compile(
    r"(-?\d{1,3}\.\d+)[,\s]+(-?\d{1,3}\.\d+)"
)

# URL query params commonly used for routing endpoints.
_COORD_PARAM_NAMES = {
    "start_lat", "start_lon", "end_lat", "end_lon",
    "lat", "lon", "latitude", "longitude",
    "from_lat", "from_lon", "to_lat", "to_lon",
}


def _hashed_region(lat: float, lon: float) -> str:
    """Return 6-char hash of the 0.1° bin holding (lat, lon).

    0.1° ≈ 11 km — coarse enough to anonymize a home, fine enough to
    distinguish regions for debugging ("this hash always 500s").
    """
    bin_key = f"{round(lat, 1):.1f},{round(lon, 1):.1f}"
    return hashlib.sha256(bin_key.encode()).hexdigest()[:6]


def hashed_bbox(*coords: float) -> str:
    """Return a stable 8-char identifier for a bbox of one or more points.

    Used in span names — e.g. ``f"proposals {sport} bbox={hashed_bbox(slat,slon,elat,elon)}"``.
    """
    # Normalize to 0.1° bins so nearby points hash to the same identifier
    binned = ",".join(f"{round(c, 1):.1f}" for c in coords)
    return hashlib.sha256(binned.encode()).hexdigest()[:8]


def _is_coord_value(s: str) -> bool:
    """True if ``s`` parses as a float in either lat or lon range."""
    try:
        v = float(s)
    except ValueError:
        return False
    return -180.0 <= v <= 180.0


def _scrub_coord_pair(s: str) -> str:
    """Replace every ``<float>,<float>`` looking like (lat,lon) with a hash."""
    def _repl(m: re.Match[str]) -> str:
        a, b = m.group(1), m.group(2)
        try:
            va, vb = float(a), float(b)
        except ValueError:
            return m.group(0)
        # Both in geographic range — looks like a coord pair
        if -180.0 <= va <= 180.0 and -180.0 <= vb <= 180.0:
            return f"bbox={hashed_bbox(va, vb)}"
        return m.group(0)
    return _COORD_PAIR_RE.sub(_repl, s)


def _scrub_query_string(qs: str) -> str:
    """Replace coordinate-named query params with hashed values.

    ``?start_lat=43.6&start_lon=3.87&end_lat=44.05`` →
    ``?start_lat=h:abcd12&start_lon=h:abcd12&end_lat=h:f1e2d3``
    """
    parts = qs.split("&")
    out = []
    for p in parts:
        if "=" not in p:
            out.append(p)
            continue
        k, _, v = p.partition("=")
        if k in _COORD_PARAM_NAMES and _is_coord_value(v):
            out.append(f"{k}=h:{_hashed_region(float(v), 0.0)}")
        else:
            out.append(p)
    return "&".join(out)


def _scrub_url(url: str) -> str:
    """Scrub coords from a full URL (path + query)."""
    if "?" in url:
        path, _, qs = url.partition("?")
        return f"{path}?{_scrub_query_string(qs)}"
    return url


def _walk(value: Any) -> Any:
    """Recursively scrub strings inside dict/list/tuple structures."""
    if isinstance(value, str):
        if "?" in value and "=" in value:  # likely URL
            value = _scrub_url(value)
        return _scrub_coord_pair(value)
    if isinstance(value, dict):
        return {k: _walk(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_walk(v) for v in value)
    return value


def before_send(event: dict, hint: dict) -> dict | None:  # noqa: ARG001
    """Sentry ``before_send`` hook — scrub raw coords from event payload.

    Touches:
    - ``event["request"]["url"]`` (full URL incl. query string)
    - ``event["request"]["query_string"]`` (some integrations split it)
    - ``event["transaction"]`` (transaction name)
    - ``event["message"]`` (free-form message)
    - ``event["spans"][*]["description"]`` (span names/descriptions)
    - ``event["breadcrumbs"]["values"][*]["data"]`` (breadcrumb data)
    """
    if not event:
        return event

    req = event.get("request")
    if isinstance(req, dict):
        if isinstance(req.get("url"), str):
            req["url"] = _scrub_url(req["url"])
        if isinstance(req.get("query_string"), str):
            req["query_string"] = _scrub_query_string(req["query_string"])

    if isinstance(event.get("transaction"), str):
        tn = event["transaction"]
        if "?" in tn and "=" in tn:  # URL-shaped transaction
            tn = _scrub_url(tn)
        event["transaction"] = _scrub_coord_pair(tn)
    if isinstance(event.get("message"), str):
        msg = event["message"]
        if "?" in msg and "=" in msg:  # URL-shaped message
            msg = _scrub_url(msg)
        event["message"] = _scrub_coord_pair(msg)

    for span in event.get("spans") or []:
        if isinstance(span, dict) and isinstance(span.get("description"), str):
            span["description"] = _scrub_coord_pair(span["description"])

    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict):
        for b in breadcrumbs.get("values") or []:
            if isinstance(b, dict):
                if isinstance(b.get("message"), str):
                    b["message"] = _scrub_coord_pair(b["message"])
                if isinstance(b.get("data"), dict):
                    b["data"] = _walk(b["data"])

    return event


def before_send_transaction(event: dict, hint: dict) -> dict | None:  # noqa: ARG001
    """Sentry ``before_send_transaction`` hook — same scrub on perf transactions."""
    return before_send(event, hint)
