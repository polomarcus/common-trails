"""Persist gps_total on import_jobs (Strava poll hotfix).

The `/integrations/strava/jobs/{id}` polling endpoint was calling
``_job_to_response`` which ran a `COUNT(*)` on ``activities`` filtered
by user_id + provider + geometry_source + non-null geometry — on every
5-second poll. During a 1400-activity import this contended with the
ingest path and starved the 5+3 connection pool, ultimately wedging
every endpoint (including unauthenticated ``/heatmap/summary``).

The fix stores the count on ``import_jobs.gps_total`` once at the end
of Phase 2 ingest, so the poll endpoint can read it as a single-column
projection on an indexed row.

Revision ID: 0045
Revises: 0044
Create Date: 2026-05-17
"""
import sqlalchemy as sa

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "import_jobs",
        sa.Column("gps_total", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("import_jobs", "gps_total")
