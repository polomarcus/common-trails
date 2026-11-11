"""Container entrypoint (backend/start-web.sh): migrate, then serve.

2026-11-11 architecture review: the old Dockerfile CMD retried
``alembic upgrade head`` 5 times and then started uvicorn ANYWAY (the loop
ended on a successful ``sleep``), so a failed migration put the API in front of
an un-migrated schema. The script must exit non-zero instead.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "start-web.sh"

pytestmark = pytest.mark.skipif(
    not _SCRIPT.is_file() or shutil.which("sh") is None, reason="start-web.sh or sh not available",
)


def _run(tmp_path: Path, alembic: str) -> subprocess.CompletedProcess:
    marker = tmp_path / "served"
    env = {
        **os.environ,
        "ALEMBIC_CMD": alembic,
        "SERVE_CMD": f"touch {marker}",
        "MIGRATE_RETRY_SLEEP": "0",
    }
    return subprocess.run(["sh", str(_SCRIPT)], env=env, capture_output=True, text=True, timeout=30)


def test_migration_ok_then_the_server_starts(tmp_path):
    proc = _run(tmp_path, "true")
    assert proc.returncode == 0
    assert (tmp_path / "served").exists()


def test_migration_failing_every_time_never_starts_the_server(tmp_path):
    proc = _run(tmp_path, "false")
    assert proc.returncode != 0
    assert not (tmp_path / "served").exists()
    assert proc.stderr.count("Alembic attempt") == 5
    assert "refusing to start" in proc.stderr


def test_migration_recovering_on_a_retry_starts_the_server(tmp_path):
    counter = tmp_path / "n"
    flaky = tmp_path / "flaky.sh"
    flaky.write_text(
        f'#!/bin/sh\nn=$(cat {counter} 2>/dev/null || echo 0); n=$((n+1)); echo $n > {counter}; [ "$n" -ge 3 ]\n'
    )
    flaky.chmod(0o755)
    proc = _run(tmp_path, f"sh {flaky}")
    assert proc.returncode == 0
    assert (tmp_path / "served").exists()
    assert counter.read_text().strip() == "3"
