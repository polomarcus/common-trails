"""Regression: deleting a heat_edges row cascade-deletes contributor rows.

Schema invariant set up by migration 0046 — see the migration docstring
for why this is a trigger (not a FOREIGN KEY) and why matching on
edge_key alone is safe.

The test inserts a heat_edge + matching contributor row via raw SQL
(SQLAlchemy ORM session), deletes the heat_edge, commits, and asserts
no contributor row survives. A control heat_edge with its own
contributor (unrelated edge_key) must survive to confirm the trigger
scopes to the deleted row.
"""

import uuid

from sqlalchemy import text as sa_text

from app.db.session import SessionLocal


def _insert_heat_edge(db, edge_key: str, sport: str = "gravel") -> None:
    """Insert a minimal heat_edges row directly (skip the ingest pipeline)."""
    db.execute(
        sa_text("""
            INSERT INTO heat_edges (
                edge_key, sport, user_count, pass_count, geometry
            ) VALUES (
                :edge_key, :sport, 1, 1,
                ST_GeomFromText('LINESTRING(3.87 43.62, 3.88 43.63)', 4326)
            )
        """),
        {"edge_key": edge_key, "sport": sport},
    )


def _insert_contributor(db, edge_key: str, user_id_hash: int) -> None:
    # `activity_id` is NOT NULL since migration 0052. Use
    # gen_random_uuid() so each call produces a distinct row even if
    # the test repeats with the same (edge_key, user_id_hash).
    db.execute(
        sa_text("""
            INSERT INTO heat_edge_contributors (edge_key, user_id_hash, activity_id)
            VALUES (:edge_key, :user_id_hash, gen_random_uuid())
        """),
        {"edge_key": edge_key, "user_id_hash": user_id_hash},
    )


def _count_contribs(db, edge_key: str) -> int:
    return (
        db.execute(
            sa_text("SELECT COUNT(*) FROM heat_edge_contributors WHERE edge_key = :k"),
            {"k": edge_key},
        ).scalar()
        or 0
    )


class TestHeatEdgeContributorsCascade:
    """Migration 0046: AFTER DELETE trigger on heat_edges propagates to contributors."""

    def test_delete_heat_edge_cascades_to_contributors(self):
        # Use a unique edge_key per test run so re-runs don't collide with
        # leftover rows. The `gravel/` prefix routes the row into the
        # heat_edges_gravel partition (migration 0029).
        suffix = uuid.uuid4().hex[:12]
        target_key = f"gravel/9.{suffix},9.{suffix}/9.{suffix},9.{suffix}"
        control_key = f"gravel/8.{suffix},8.{suffix}/8.{suffix},8.{suffix}"

        db = SessionLocal()
        try:
            # Setup: two edges, each with one contributor.
            _insert_heat_edge(db, target_key)
            _insert_heat_edge(db, control_key)
            _insert_contributor(db, target_key, 1001)
            _insert_contributor(db, control_key, 1002)
            db.commit()

            assert _count_contribs(db, target_key) == 1
            assert _count_contribs(db, control_key) == 1

            # Act: delete the target edge.
            db.execute(
                sa_text("DELETE FROM heat_edges WHERE edge_key = :k"),
                {"k": target_key},
            )
            db.commit()

            # Assert: target's contributor is gone, control survives.
            assert _count_contribs(db, target_key) == 0, (
                "AFTER DELETE trigger on heat_edges did not cascade to "
                "heat_edge_contributors — migration 0046 regression"
            )
            assert _count_contribs(db, control_key) == 1, (
                "Trigger over-cascaded: it deleted contributors for an unrelated edge_key"
            )
        finally:
            # Cleanup any rows that survived (control + any partial state).
            db.execute(
                sa_text("DELETE FROM heat_edges WHERE edge_key IN (:t, :c)"),
                {"t": target_key, "c": control_key},
            )
            db.execute(
                sa_text("DELETE FROM heat_edge_contributors WHERE edge_key IN (:t, :c)"),
                {"t": target_key, "c": control_key},
            )
            db.commit()
            db.close()

    def test_multiple_contributors_all_cascade(self):
        """All contributor rows for a given edge_key vanish on heat_edge delete."""
        suffix = uuid.uuid4().hex[:12]
        target_key = f"gravel/7.{suffix},7.{suffix}/7.{suffix},7.{suffix}"

        db = SessionLocal()
        try:
            _insert_heat_edge(db, target_key)
            for hash_id in (2001, 2002, 2003, 2004):
                _insert_contributor(db, target_key, hash_id)
            db.commit()

            assert _count_contribs(db, target_key) == 4

            db.execute(
                sa_text("DELETE FROM heat_edges WHERE edge_key = :k"),
                {"k": target_key},
            )
            db.commit()

            assert _count_contribs(db, target_key) == 0, "Trigger left some contributor rows behind"
        finally:
            db.execute(
                sa_text("DELETE FROM heat_edge_contributors WHERE edge_key = :k"),
                {"k": target_key},
            )
            db.commit()
            db.close()
