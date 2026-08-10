"""Tests for GPX backup service (local filesystem backend)."""

import os
import tempfile

import pytest


@pytest.fixture
def backup_dir(monkeypatch):
    """Create a temp dir and configure GPX_BACKUP_BUCKET to use it."""
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setenv("GPX_BACKUP_BUCKET", d)
        # Re-import to pick up new env var
        import importlib

        from app.services import gpx_backup
        importlib.reload(gpx_backup)
        yield d
        # Reset
        monkeypatch.delenv("GPX_BACKUP_BUCKET", raising=False)
        importlib.reload(gpx_backup)


@pytest.fixture
def disabled_backup(monkeypatch):
    """Disable backup by clearing the env var."""
    monkeypatch.setenv("GPX_BACKUP_BUCKET", "")
    import importlib

    from app.services import gpx_backup
    importlib.reload(gpx_backup)
    yield
    monkeypatch.delenv("GPX_BACKUP_BUCKET", raising=False)
    importlib.reload(gpx_backup)


@pytest.mark.asyncio
async def test_backup_stores_file(backup_dir):
    from app.services.gpx_backup import backup_gpx

    content = b"<gpx>test track data</gpx>"
    ok = await backup_gpx("user123", "abc123def456", content)
    assert ok is True

    stored = os.path.join(backup_dir, "user123", "abc123def456.gpx")
    assert os.path.exists(stored)
    with open(stored, "rb") as f:
        assert f.read() == content


@pytest.mark.asyncio
async def test_backup_idempotent(backup_dir):
    """Same hash = same file path = idempotent overwrite."""
    from app.services.gpx_backup import backup_gpx

    content1 = b"<gpx>version 1</gpx>"
    content2 = b"<gpx>version 2</gpx>"
    await backup_gpx("user1", "samehash", content1)
    await backup_gpx("user1", "samehash", content2)

    stored = os.path.join(backup_dir, "user1", "samehash.gpx")
    # Last write wins (idempotent overwrite)
    with open(stored, "rb") as f:
        assert f.read() == content2


@pytest.mark.asyncio
async def test_exists_check(backup_dir):
    from app.services.gpx_backup import backup_gpx, gpx_exists

    assert await gpx_exists("user1", "missing") is False
    await backup_gpx("user1", "present", b"<gpx/>")
    assert await gpx_exists("user1", "present") is True


@pytest.mark.asyncio
async def test_list_user_backups(backup_dir):
    from app.services.gpx_backup import backup_gpx, list_user_backups

    await backup_gpx("user1", "hash_a", b"<gpx/>")
    await backup_gpx("user1", "hash_b", b"<gpx/>")
    await backup_gpx("user2", "hash_c", b"<gpx/>")

    user1_files = await list_user_backups("user1")
    assert sorted(user1_files) == ["hash_a", "hash_b"]

    user2_files = await list_user_backups("user2")
    assert user2_files == ["hash_c"]


@pytest.mark.asyncio
async def test_disabled_backup_returns_false(disabled_backup):
    from app.services.gpx_backup import backup_gpx, gpx_exists

    assert await backup_gpx("user1", "hash", b"data") is False
    assert await gpx_exists("user1", "hash") is False


@pytest.mark.asyncio
async def test_backup_different_users_same_hash(backup_dir):
    """Two users uploading the same file get separate backups."""
    from app.services.gpx_backup import backup_gpx, gpx_exists

    await backup_gpx("alice", "samefile", b"<gpx/>")
    await backup_gpx("bob", "samefile", b"<gpx/>")

    assert await gpx_exists("alice", "samefile") is True
    assert await gpx_exists("bob", "samefile") is True


@pytest.mark.asyncio
async def test_uploads_bucket_fallback(monkeypatch, tmp_path):
    """When GPX_BACKUP_BUCKET is unset, fall back to UPLOADS_BUCKET (prod path)."""
    monkeypatch.delenv("GPX_BACKUP_BUCKET", raising=False)
    monkeypatch.setenv("UPLOADS_BUCKET", str(tmp_path))
    import importlib

    from app.services import gpx_backup
    importlib.reload(gpx_backup)

    ok = await gpx_backup.backup_gpx("user1", "hashX", b"<gpx/>")
    assert ok is True
    assert (tmp_path / "user1" / "hashX.gpx").exists()

    monkeypatch.delenv("UPLOADS_BUCKET", raising=False)
    importlib.reload(gpx_backup)


@pytest.mark.asyncio
async def test_explicit_overrides_uploads(monkeypatch, tmp_path):
    """GPX_BACKUP_BUCKET takes priority over UPLOADS_BUCKET when both set."""
    explicit = tmp_path / "explicit"
    fallback = tmp_path / "fallback"
    monkeypatch.setenv("GPX_BACKUP_BUCKET", str(explicit))
    monkeypatch.setenv("UPLOADS_BUCKET", str(fallback))
    import importlib

    from app.services import gpx_backup
    importlib.reload(gpx_backup)

    await gpx_backup.backup_gpx("user1", "hashY", b"<gpx/>")
    assert (explicit / "user1" / "hashY.gpx").exists()
    assert not (fallback / "user1" / "hashY.gpx").exists()

    monkeypatch.delenv("GPX_BACKUP_BUCKET", raising=False)
    monkeypatch.delenv("UPLOADS_BUCKET", raising=False)
    importlib.reload(gpx_backup)
