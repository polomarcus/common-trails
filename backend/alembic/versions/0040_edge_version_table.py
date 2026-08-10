"""Persist edge mutation version across instances.

Background
----------
The routing layer caches the per-bbox graph keyed by an ``edge_version``
counter. Pre-2026-05-10, that counter was a Python module-level int
incremented on each ``_update_heat_edges`` call. Fine on a single
Cloud Run instance — but with ``max_instances ≥ 2``, instance B's
counter lags instance A's, so instance B serves stale routing-graph
caches even though heat_edges have changed (on A).

This migration creates a single-row state table that all instances
read/write. The version becomes globally consistent (subject to a
short per-instance TTL cache to avoid hammering the DB on every
routing request).

Schema
------
Singleton row enforced by a ``CHECK (id = 1)`` constraint; the bumper
``UPDATE edge_version_state SET version = version + 1 WHERE id = 1``
is the only writer pattern.

Revision ID: 0040
Revises: 0039
"""
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS edge_version_state (
            id      SMALLINT PRIMARY KEY,
            version BIGINT NOT NULL DEFAULT 0,
            CONSTRAINT edge_version_state_singleton CHECK (id = 1)
        );
        INSERT INTO edge_version_state (id, version)
        VALUES (1, 0)
        ON CONFLICT (id) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS edge_version_state")
