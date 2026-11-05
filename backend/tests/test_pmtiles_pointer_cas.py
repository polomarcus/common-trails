"""Overlapping build-pmtiles runs must never publish an OLDER heatmap last.

The archive drain now pings the rebuild per archive, so two builds can run in
parallel. ``_upload_to_export_bucket`` used to be last-writer-wins on the
``heatmap-display.json`` pointer (and every run shared ONE ``.json.tmp``): an
older build finishing last re-published its stale snapshot and the newer
rides vanished from /map.

These tests drive the REAL ``_upload_to_export_bucket`` against an in-memory
fake GCS bucket that honours ``if_generation_match`` like GCS does.
"""

import json
from datetime import UTC, datetime

import pytest
from google.api_core.exceptions import PreconditionFailed

from app.jobs import build_pmtiles as bp

T_OLD = "2026-11-05T10:00:00+00:00"
T_NEW = "2026-11-05T10:05:00+00:00"


class _Store:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, int, datetime]] = {}
        # time_created stamped on every write (GCS sets it per generation).
        # Default = BEFORE every test snapshot start, i.e. "an old build".
        self.clock = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
        # Simulates the client's auto-retry after a LOST response: the pointer
        # rewrite is APPLIED, then the retried request gets a 412.
        self.lose_pointer_rewrite_response = False
        self._gen = 0
        self.rewrites: list[str] = []
        self.written_names: list[str] = []
        # Optional hook run right BEFORE a pointer rewrite (simulates a
        # concurrent build publishing between our read and our write).
        self.before_pointer_rewrite = None

    def put(self, name: str, data: bytes) -> None:
        self._gen += 1
        self.objects[name] = (data, self._gen, self.clock)
        self.written_names.append(name)

    def pointer(self) -> dict:
        return json.loads(self.objects["heatmap-display.json"][0])


class _Blob:
    def __init__(self, store: _Store, name: str):
        self.store = store
        self.name = name
        self.cache_control = None

    @property
    def time_created(self):
        obj = self.store.objects.get(self.name)
        return obj[2] if obj else None

    @property
    def generation(self):
        obj = self.store.objects.get(self.name)
        return obj[1] if obj else None

    def upload_from_filename(self, path, content_type=None):
        with open(path, "rb") as fh:
            self.store.put(self.name, fh.read())

    def upload_from_string(self, data, content_type=None):
        self.store.put(self.name, data.encode() if isinstance(data, str) else data)

    def download_as_bytes(self, if_generation_match=None):
        data, gen, _created = self.store.objects[self.name]
        if if_generation_match is not None and gen != if_generation_match:
            raise PreconditionFailed("generation mismatch")
        return data

    def rewrite(self, source, if_generation_match=None):
        if self.name == "heatmap-display.json" and self.store.before_pointer_rewrite:
            hook, self.store.before_pointer_rewrite = self.store.before_pointer_rewrite, None
            hook()
        self.store.rewrites.append(self.name)
        if if_generation_match is not None and (self.generation or 0) != if_generation_match:
            raise PreconditionFailed("generation mismatch")
        self.store.put(self.name, self.store.objects[source.name][0])
        if self.name == "heatmap-display.json" and self.store.lose_pointer_rewrite_response:
            self.store.lose_pointer_rewrite_response = False
            raise PreconditionFailed("412 on the auto-retry of an applied write")

    def delete(self):
        self.store.objects.pop(self.name, None)


class _Bucket:
    def __init__(self, store: _Store):
        self.store = store

    def blob(self, name):
        return _Blob(self.store, name)

    def get_blob(self, name):
        return _Blob(self.store, name) if name in self.store.objects else None

    def list_blobs(self, prefix=""):
        return [_Blob(self.store, n) for n in list(self.store.objects) if n.startswith(prefix)]


class _Client:
    def __init__(self, store):
        self._bucket = _Bucket(store)

    def bucket(self, _name):
        return self._bucket


@pytest.fixture
def store(monkeypatch):
    from google.cloud import storage

    s = _Store()
    monkeypatch.setattr(storage, "Client", lambda *a, **k: _Client(s))
    monkeypatch.setenv("HEATMAP_GCS_BUCKET", "test-heatmap-bucket")

    def _no_db():
        raise RuntimeError("no DB in this unit test")

    monkeypatch.setattr("app.db.session.SessionLocal", _no_db)
    return s


def _pmtiles(tmp_path, name: str, body: bytes) -> str:
    path = tmp_path / name
    path.write_bytes(body)
    return str(path)


def _referenced(store) -> str:
    return store.pointer()["latest_url"].rsplit("/", 1)[1]


def _pinned(store) -> list[str]:
    return sorted(n for n in store.objects if n.startswith("heatmap-display-") and n.endswith(".pmtiles"))


def test_older_snapshot_finishing_last_never_overwrites_newer_pointer(store, tmp_path):
    new_path = _pmtiles(tmp_path, "new.pmtiles", b"NEW-SNAPSHOT")
    old_path = _pmtiles(tmp_path, "old.pmtiles", b"OLD-SNAPSHOT")

    # The NEWER build (started later) finishes FIRST…
    bp._upload_to_export_bucket(new_path, snapshot_started_at=T_NEW)
    published = store.pointer()
    assert published["snapshot_started_at"] == T_NEW
    # …then the OLDER one lands: it must skip pointer, mutable copy and prune.
    bp._upload_to_export_bucket(old_path, snapshot_started_at=T_OLD)

    assert store.pointer() == published
    assert store.objects["heatmap-display.pmtiles"][0] == b"NEW-SNAPSHOT"
    # The live snapshot survives. The loser NEVER deletes its own pinned
    # snapshot (it may share the live name) — the next prune handles it.
    assert _referenced(store) in store.objects


def test_newer_snapshot_replaces_older_pointer_and_prunes(store, tmp_path):
    bp._upload_to_export_bucket(_pmtiles(tmp_path, "a", b"OLD"), snapshot_started_at=T_OLD)
    bp._upload_to_export_bucket(_pmtiles(tmp_path, "b", b"NEW"), snapshot_started_at=T_NEW)

    pointer = store.pointer()
    assert pointer["snapshot_started_at"] == T_NEW
    assert store.objects["heatmap-display.pmtiles"][0] == b"NEW"
    assert _pinned(store) == [pointer["latest_url"].rsplit("/", 1)[1]]


def test_legacy_pointer_without_timestamp_counts_as_older(store, tmp_path):
    store.put("heatmap-display.json", json.dumps({"latest": "v-legacy"}).encode())

    bp._upload_to_export_bucket(_pmtiles(tmp_path, "a", b"FRESH"), snapshot_started_at=T_OLD)

    assert store.pointer()["snapshot_started_at"] == T_OLD


def test_precondition_race_is_retried_once(store, tmp_path):
    """Another (older) build publishes between our read and our rewrite → the
    generation no longer matches → ONE retry with a fresh read wins."""
    store.put("heatmap-display.json", json.dumps({"snapshot_started_at": T_OLD}).encode())
    store.before_pointer_rewrite = lambda: store.put(
        "heatmap-display.json", json.dumps({"snapshot_started_at": T_OLD, "racer": True}).encode()
    )

    bp._upload_to_export_bucket(_pmtiles(tmp_path, "a", b"NEW"), snapshot_started_at=T_NEW)

    assert store.rewrites.count("heatmap-display.json") == 2
    assert store.pointer()["snapshot_started_at"] == T_NEW
    assert store.objects["heatmap-display.pmtiles"][0] == b"NEW"


def test_precondition_race_lost_to_a_newer_build_skips(store, tmp_path):
    """The racer that slipped in is NEWER than us → the retry's fresh read sees
    it and we skip (pointer + mutable copy untouched by us)."""
    store.put("heatmap-display.json", json.dumps({"snapshot_started_at": T_OLD}).encode())
    newer = json.dumps({"snapshot_started_at": "2026-11-05T11:00:00+00:00"}).encode()
    store.before_pointer_rewrite = lambda: store.put("heatmap-display.json", newer)

    bp._upload_to_export_bucket(_pmtiles(tmp_path, "a", b"MINE"), snapshot_started_at=T_NEW)

    assert store.objects["heatmap-display.json"][0] == newer
    assert "heatmap-display.pmtiles" not in store.objects


def test_tmp_blob_names_are_unique_per_run(store, tmp_path):
    bp._upload_to_export_bucket(_pmtiles(tmp_path, "a", b"ONE"), snapshot_started_at=T_OLD)
    bp._upload_to_export_bucket(_pmtiles(tmp_path, "b", b"TWO"), snapshot_started_at=T_NEW)

    tmps = [n for n in store.written_names if ".tmp" in n]
    assert "heatmap-display.json.tmp" not in tmps
    assert "heatmap-display.pmtiles.tmp" not in tmps
    assert len(tmps) == len(set(tmps)) == 4, tmps
    # Every tmp is cleaned up.
    assert not [n for n in store.objects if ".tmp" in n]


def test_same_bytes_same_hour_overlap_keeps_live_snapshot(store, tmp_path):
    """Two builds with IDENTICAL bytes in the same hour share one version_id /
    pinned name. The losing (older) one must not delete "its" snapshot — it is
    the winner's live one, /map would 404."""
    same = b"IDENTICAL-BYTES"
    bp._upload_to_export_bucket(_pmtiles(tmp_path, "a", same), snapshot_started_at=T_NEW)
    bp._upload_to_export_bucket(_pmtiles(tmp_path, "b", same), snapshot_started_at=T_OLD)

    assert store.pointer()["snapshot_started_at"] == T_NEW
    assert store.objects[_referenced(store)][0] == same


def test_applied_write_then_412_retry_counts_as_success(store, tmp_path):
    """The client auto-retries ``rewrite(if_generation_match=…)``: if the first
    write landed but its response was lost, the retry raises 412 although the
    pointer IS ours. That is a success — mutable copy written, nothing deleted."""
    store.put("heatmap-display.json", json.dumps({"snapshot_started_at": T_OLD}).encode())
    store.lose_pointer_rewrite_response = True

    bp._upload_to_export_bucket(_pmtiles(tmp_path, "a", b"MINE"), snapshot_started_at=T_NEW)

    assert store.pointer()["snapshot_started_at"] == T_NEW
    assert store.objects[_referenced(store)][0] == b"MINE"
    assert store.objects["heatmap-display.pmtiles"][0] == b"MINE"


def test_prune_spares_referenced_and_newer_pending_snapshots(store, tmp_path):
    """Prune deletes only OLD snapshots (created before our start): never the
    one the live pointer references, never a snapshot an overlapping newer
    build uploaded after our start and has not published yet."""
    store.put("heatmap-display-v1-oldold.pmtiles", b"ANCIENT")  # clock 09:00 < T_NEW
    store.clock = datetime(2026, 11, 5, 10, 6, tzinfo=UTC)  # after T_NEW
    store.put("heatmap-display-v2-pending.pmtiles", b"NEWER-BUILD-NOT-YET-PUBLISHED")

    bp._upload_to_export_bucket(_pmtiles(tmp_path, "a", b"MINE"), snapshot_started_at=T_NEW)

    assert "heatmap-display-v1-oldold.pmtiles" not in store.objects
    assert "heatmap-display-v2-pending.pmtiles" in store.objects
    assert _referenced(store) in store.objects
