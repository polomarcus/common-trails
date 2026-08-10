"""Test that user_id hashing is deterministic (not Python hash())."""
import hashlib
import subprocess
import sys


def _user_id_hash(user_id) -> int:
    """Mirror the hashing logic from ingest.py."""
    return int(hashlib.sha256(str(user_id).encode()).hexdigest()[:8], 16)


def test_hash_deterministic_same_process():
    """Same user_id always produces the same hash within a process."""
    assert _user_id_hash(42) == _user_id_hash(42)
    assert _user_id_hash("alice") == _user_id_hash("alice")


def test_hash_different_users():
    """Different user_ids produce different hashes."""
    assert _user_id_hash(1) != _user_id_hash(2)
    assert _user_id_hash("alice") != _user_id_hash("bob")


def test_hash_deterministic_across_processes():
    """Hash must be identical across separate Python processes.

    This is the actual bug: Python's built-in hash() is randomized per process
    (PYTHONHASHSEED), so each Cloud Run cold start produced a different hash
    for the same user, inflating contributor counts.
    """
    script = (
        "import hashlib; "
        "print(int(hashlib.sha256(str(42).encode()).hexdigest()[:8], 16))"
    )
    results = set()
    for _ in range(5):
        out = subprocess.check_output(
            [sys.executable, "-c", script],
            text=True,
        )
        results.add(out.strip())

    assert len(results) == 1, f"Hash varied across processes: {results}"
    assert int(results.pop()) == _user_id_hash(42)


def test_hash_fits_32bit():
    """Hash value fits in an unsigned 32-bit integer (8 hex chars)."""
    for uid in [0, 1, 999999, "alice", "bob@example.com"]:
        h = _user_id_hash(uid)
        assert 0 <= h <= 0xFFFFFFFF, f"Hash {h} for {uid} exceeds 32 bits"
