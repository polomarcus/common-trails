"""Audit 2026-05-29 GPX-S2.3 — gpxpy uses stdlib XML which is
DTD-vulnerable per Python's own docs. `defusedxml.defuse_stdlib()`
at the top of `app/services/gpx.py` monkey-patches the stdlib XML
parsers so the DTD attack surface is closed for every consumer
(including third-party libraries we don't control like gpxpy).

These tests pin the hardening — without it the worker can OOM on a
sub-KB crafted GPX:

  - Billion-laughs entity expansion (entity references nested 9
    levels deep, each multiplying by 10 = 10^9 = 1 GB of expanded
    bytes from a 1 KB input). MUST raise an EntitiesForbidden-class
    error from defusedxml rather than expand.
  - External-DTD reference. MUST raise rather than fetch.
  - External-entity reference. MUST raise rather than fetch.

The "raise" behaviour is what we want — a malformed/malicious GPX
is rejected with a parse error, not silently allowed to expand.
"""
from __future__ import annotations

import pytest

# Importing gpx triggers defuse_stdlib() as a side effect.
from app.services.gpx import parse_gpx

# Billion-laughs payload. 9 levels of entity nesting, base x = "lol"
# expands to 10^9 = 1 GB. Sub-2 KB input that would OOM a stdlib
# parser. Real GPX files NEVER use DTD entities, so rejecting these
# never affects a legitimate user.
BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE gpx [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
  <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
  <!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">
  <!ENTITY lol6 "&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;">
  <!ENTITY lol7 "&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;">
  <!ENTITY lol8 "&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;">
  <!ENTITY lol9 "&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;">
]>
<gpx version="1.1" creator="test"><name>&lol9;</name></gpx>"""

EXTERNAL_DTD = b"""<?xml version="1.0"?>
<!DOCTYPE gpx SYSTEM "http://example.com/evil.dtd">
<gpx version="1.1" creator="test"><name>x</name></gpx>"""

EXTERNAL_ENTITY = b"""<?xml version="1.0"?>
<!DOCTYPE gpx [
  <!ENTITY external SYSTEM "file:///etc/passwd">
]>
<gpx version="1.1" creator="test"><name>&external;</name></gpx>"""


def test_billion_laughs_is_rejected_without_expansion():
    """A crafted GPX with 10^9 entity expansion MUST raise quickly,
    not OOM the worker. `defusedxml.defuse_stdlib()` makes the
    stdlib XML parser raise `EntitiesForbidden` instead of expanding.

    Without the defuse, this test alone consumes 1+ GB of RAM and
    times out — so a failure of this test is loud."""
    with pytest.raises(Exception) as exc_info:
        parse_gpx(BILLION_LAUGHS)
    # `defusedxml` raises `defusedxml.EntitiesForbidden` (or a
    # subclass). The exception class name should contain "Entities"
    # or "Forbidden" — both are defused-specific markers. We don't
    # bind to the exact class so a defusedxml-version bump doesn't
    # break the test.
    msg = (str(exc_info.value) + type(exc_info.value).__name__).lower()
    assert "entit" in msg or "forbidden" in msg or "dtd" in msg, (
        f"Expected a defusedxml-class rejection (Entities/DTD); got "
        f"{type(exc_info.value).__name__}: {exc_info.value}"
    )


def test_external_dtd_reference_does_not_leak_content():
    """A GPX referencing an external DTD must NOT result in a network
    fetch — `defusedxml`'s entity-resolver rejects external refs.
    `ElementTree`'s default expat doesn't auto-fetch DTDs anyway,
    so this is a belt-and-suspenders check: parse may succeed but
    no external content reaches the parsed tree."""
    try:
        parsed = parse_gpx(EXTERNAL_DTD)
    except Exception as exc:
        # Either outcome is acceptable: defuse rejects it, OR the
        # parser ignores the unresolved external DTD silently.
        msg = (str(exc) + type(exc).__name__).lower()
        assert "external" in msg or "dtd" in msg or "forbidden" in msg or "entit" in msg, (
            f"Expected defused-class rejection if any, got {type(exc).__name__}: {exc}"
        )
        return
    # If it parsed, no externally-fetched content should appear in
    # the result.
    name = parsed.get("name") or ""
    assert "evil.dtd" not in name


def test_external_entity_reference_does_not_leak_file_content():
    """A GPX with an external `file:///etc/passwd` entity reference
    must NOT actually read the file. Either the parser raises, OR
    the entity is left unresolved (empty / literal reference)."""
    try:
        parsed = parse_gpx(EXTERNAL_ENTITY)
    except Exception as exc:
        msg = (str(exc) + type(exc).__name__).lower()
        assert "external" in msg or "entit" in msg or "forbidden" in msg, (
            f"Expected defused-class rejection if any, got {type(exc).__name__}: {exc}"
        )
        return
    # Parsed without raise → assert no /etc/passwd content leaked.
    name = parsed.get("name") or ""
    assert "root:" not in name, (
        f"External file entity was resolved — /etc/passwd contents in <name>: {name!r}"
    )


def test_normal_gpx_still_parses_after_defuse():
    """Sanity — defuse_stdlib() must not break legitimate GPX
    parsing. Real-world files don't use DTDs / external entities."""
    valid = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>ok</name><trkseg>
    <trkpt lat="43.61" lon="3.87"><ele>100</ele></trkpt>
    <trkpt lat="43.62" lon="3.88"><ele>110</ele></trkpt>
  </trkseg></trk>
</gpx>"""
    parsed = parse_gpx(valid)
    assert parsed.get("coord_count", 0) == 2
    assert parsed.get("geometry_geojson") is not None


def test_defuse_stdlib_is_actually_applied_to_gpxpys_parser():
    """Strong pin for the hardening — introspect that the parser
    gpxpy resolves to (a) is stdlib (not lxml), and (b) has had
    `XMLParser` swapped to the defusedxml subclass.

    Why this matters beyond the billion-laughs test:
    - Python 3.13's pyexpat ALREADY enforces an amplification limit
      (CVE-2023-52425 hardening) — so billion-laughs raises even
      without defusedxml. A test that only checks rejection passes
      vacuously on stock CPython. This test fails specifically if
      `defuse_stdlib()` is removed.
    - gpxpy prefers `lxml` if it's importable. lxml is not pulled
      by any current dep, but the moment a future transitive dep
      brings it in (e.g. `beautifulsoup4[lxml]`), gpxpy silently
      switches and `defuse_stdlib()` becomes a no-op for the GPX
      path. This test catches that regression at import time, not
      after a payload arrives in prod.
    """
    import xml.etree.ElementTree as ET

    import gpxpy.parser as gp

    # (a) gpxpy must be using stdlib ElementTree, not lxml.
    parser_mod_name = getattr(gp, "mod_etree", None) and gp.mod_etree.__name__
    assert parser_mod_name is not None, (
        "gpxpy.parser.mod_etree not found — gpxpy internals changed; review."
    )
    assert parser_mod_name.startswith("xml.etree."), (
        f"gpxpy resolved to {parser_mod_name!r} — defuse_stdlib() does NOT "
        f"patch lxml. Either remove the lxml dep or apply a lxml-equivalent "
        f"guard (e.g. defusedxml.lxml)."
    )

    # (b) The stdlib XMLParser class must be the defusedxml subclass.
    assert ET.XMLParser.__module__.startswith("defusedxml"), (
        f"xml.etree.ElementTree.XMLParser.__module__ is "
        f"{ET.XMLParser.__module__!r}, not defusedxml. defuse_stdlib() was "
        f"not called before this test ran. Check `import defusedxml; "
        f"defusedxml.defuse_stdlib()` is still at the top of "
        f"`app/services/gpx.py` (BEFORE `import gpxpy`)."
    )
    assert ET.parse.__module__.startswith("defusedxml"), (
        f"xml.etree.ElementTree.parse is not defused "
        f"({ET.parse.__module__!r}). Same fix as above."
    )
