"""The whole-archive drain ingests in ONE pass, in the zip's own order.

There is no spatial pre-sort (it only existed to keep the removed OSM matcher's
segment cache warm, and forced a second I/O pass over the zip — re-opening
every inner Garmin ``UploadedFiles_*.zip``).

DB-free: everything is driven through monkeypatched seams.
"""
import contextlib
import io
import zipfile
from unittest.mock import MagicMock

from app.jobs import ingest_pending_archives as drain_mod

# Two well-separated regions.
_MONTPELLIER = (43.61, 3.87)
_BARCELONA = (41.39, 2.17)


def _gpx_bytes(lat: float, lon: float, n: int = 5) -> bytes:
    pts = "".join(
        f'<trkpt lat="{lat + i * 0.001}" lon="{lon + i * 0.001}"><ele>100</ele></trkpt>'
        for i in range(n)
    )
    return (
        '<?xml version="1.0"?><gpx version="1.1" creator="test">'
        f"<trk><name>t</name><trkseg>{pts}</trkseg></trk></gpx>"
    ).encode()


def _interleaved_archive() -> bytes:
    """Members deliberately interleaved by region in the zip's own order."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mont_1.gpx", _gpx_bytes(*_MONTPELLIER))
        zf.writestr("spain_1.gpx", _gpx_bytes(*_BARCELONA))
        zf.writestr("mont_2.gpx", _gpx_bytes(_MONTPELLIER[0] + 0.02, _MONTPELLIER[1] + 0.02))
        zf.writestr("spain_2.gpx", _gpx_bytes(_BARCELONA[0] + 0.02, _BARCELONA[1] + 0.02))
        zf.writestr("mont_3.gpx", _gpx_bytes(_MONTPELLIER[0] + 0.04, _MONTPELLIER[1] + 0.04))
    return buf.getvalue()


class TestArchiveIterationOrder:
    def test_every_member_ingested_once_in_iteration_order(self, monkeypatch):
        """The one-pass archive drain ingests EVERY member exactly once, in the
        order ``iter_zip_members`` yields them (zip order) — NO spatial re-sort,
        NO re-read: the interleaved 2-region archive
        is processed mont/spain/mont/spain/mont, not grouped."""
        from app.services import archive_intake

        zip_bytes = _interleaved_archive()

        @contextlib.contextmanager
        def fake_open(backend, key):
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                yield zf

        processed_order: list[str] = []

        def fake_ingest(*, user_id, filename, raw, resolved_sport,
                        contribute_heatmap, source, fallback_sport=None,
                        fallback_date=None, stats=None):
            # The bytes the drain feeds ingest must be the member's REAL bytes.
            assert raw.lstrip().startswith(b"<?xml"), raw[:40]
            processed_order.append(filename)
            return "imported", "act-id", None

        monkeypatch.setattr(drain_mod, "_apply_drain_statement_timeout", lambda: None)
        monkeypatch.setattr(drain_mod, "_ping_pmtiles_rebuild", lambda i, s: None)
        monkeypatch.setattr(drain_mod, "_finish_archive", lambda *a, **k: None)
        monkeypatch.setattr(drain_mod, "_notify_archive_terminal", lambda *a, **k: None)
        monkeypatch.setattr(drain_mod, "_ingest_member_bytes", fake_ingest)
        monkeypatch.setattr(drain_mod, "_claim_archives", lambda db, limit: [{
            "id": "arch-1", "user_id": "u1", "storage_backend": "local",
            "bucket_key": "k", "fallback_sport": "road",
            "contribute_heatmap": True, "source": "manual_upload_archive",
        }])
        monkeypatch.setattr(archive_intake, "archive_size", lambda b, k: None)
        monkeypatch.setattr(archive_intake, "open_archive_zip", fake_open)
        monkeypatch.setattr("app.db.session.SessionLocal", MagicMock())

        summary = drain_mod.drain_pending_archives(limit=1, pace_seconds=0.0)

        assert summary["imported"] == 5, summary
        # One pass, iteration order — exactly the zip's own member order.
        assert processed_order == [
            "mont_1.gpx", "spain_1.gpx", "mont_2.gpx", "spain_2.gpx", "mont_3.gpx",
        ], processed_order
