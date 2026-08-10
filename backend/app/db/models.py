"""SQLAlchemy ORM models for CHEMINS COMMUNS.

Data separation:
- PRIVATE: activities, activity_cells, activity_photos, integration_accounts, import_jobs
- COMMON (ODbL): heat_cells, edge_popularity, routes, route_versions, route_forks,
                  suggestions, route_tags
"""
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship

try:
    from geoalchemy2 import Geometry
    _HAS_GEOALCHEMY = True
except ImportError:
    _HAS_GEOALCHEMY = False


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


# ── User model ───────────────────────────────────────────────────────────────

class User(Base):
    """A registered user account."""
    __tablename__ = "users"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    email = Column(String(255), nullable=False, unique=True, index=True)
    username = Column(String(100), nullable=False)
    hashed_password = Column(Text, nullable=True)
    is_admin = Column(Boolean, default=False, nullable=False, server_default="false")
    created_at = Column(DateTime(timezone=True), default=_now)
    # Last time the 6-monthly "re-export + re-upload your data" reminder was sent
    # (app.jobs.resync_reminder, migration 0064). NULL = never reminded.
    last_resync_reminder_at = Column(DateTime(timezone=True), nullable=True)


class MagicLinkToken(Base):
    """Single-use nonce backing passwordless magic-link login (migration 0061).

    One row per issued login link. The link itself carries a short-lived JWT
    (signed with JWT_SECRET, ``purpose='magic_link'``); this table records the
    JWT's ``jti`` so verification can enforce SINGLE-USE — a jti with a
    non-NULL ``consumed_at`` (or an unknown jti) is rejected. The row also
    stores the normalized ``email`` + ``request_ip`` so the request endpoint
    can rate-limit (≤N per email / per IP per window) from a simple COUNT over
    ``created_at``.

    Rows are cheap and self-expiring in meaning (``expires_at``); a periodic
    cleanup can prune consumed/expired rows, but correctness never depends on
    it (verify always re-checks ``consumed_at`` + ``expires_at``).
    """
    __tablename__ = "magic_link_tokens"

    jti = Column(UUID(as_uuid=False), primary_key=True)
    user_id = Column(String(36), nullable=False, index=True)
    email = Column(String(255), nullable=False)
    request_ip = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    # Which flow minted this token (migration 0062). "magic_link" = passwordless
    # login (#476); "email_change" = confirm a REAL email for an account whose
    # address is still synthetic/being changed (account unification). Keeps the
    # two flows disjoint: the login rate-limit counts only magic_link rows, and
    # the email-change confirm consumes only email_change rows. For an
    # email_change row, ``email`` holds the NEW (target) address to finalize.
    purpose = Column(
        String(32), nullable=False, server_default="magic_link", default="magic_link"
    )

    __table_args__ = (
        # Rate-limit COUNT(*) WHERE email=... AND created_at > window-start.
        Index("ix_magic_link_tokens_email_created", "email", "created_at"),
        # Rate-limit COUNT(*) WHERE request_ip=... AND created_at > window-start.
        Index("ix_magic_link_tokens_ip_created", "request_ip", "created_at"),
    )


# ── GitHub-like route models ─────────────────────────────────────────────────

class Route(Base):
    """An itinerary — like a git repository."""
    __tablename__ = "routes"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    owner_id = Column(String(36), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    sport = Column(
        Enum("road", "gravel", "mtb", "offroad", "running", name="sport_enum", create_type=False),
        nullable=False,
        default="road",
    )
    visibility = Column(
        Enum("public", "unlisted", "private", name="visibility_enum", create_type=False),
        nullable=False,
        default="public",
    )
    status = Column(String(20), nullable=False, default="published")
    forked_from_id = Column(UUID(as_uuid=False), ForeignKey("routes.id"), nullable=True)
    current_version_id = Column(UUID(as_uuid=False), nullable=True)
    distance_m = Column(Float, nullable=True)
    elevation_gain_m = Column(Float, nullable=True)
    geometry_geojson = Column(Text, nullable=True)
    waypoints_json = Column(Text, nullable=True)
    surface_pct = Column(Text, nullable=True)  # JSON {"asphalt": 72.5, "gravel": 27.5}
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_accessed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    versions = relationship("RouteVersion", back_populates="route", foreign_keys="RouteVersion.route_id")
    tags = relationship("RouteTag", back_populates="route")
    forks = relationship("Route", foreign_keys=[forked_from_id])
    annotations = relationship("RouteAnnotation", cascade="all, delete-orphan")


class RouteVersion(Base):
    """A version snapshot of a route — like a git commit."""
    __tablename__ = "route_versions"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    route_id = Column(UUID(as_uuid=False), ForeignKey("routes.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id = Column(String(36), nullable=False, index=True)
    message = Column(String(500), nullable=True)
    # GeoJSON LineString stored as text (geometry column added in migration)
    geometry_geojson = Column(Text, nullable=True)
    distance_m = Column(Float, nullable=True)
    elevation_gain_m = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    route = relationship("Route", back_populates="versions", foreign_keys=[route_id])


class RouteFork(Base):
    """Tracks fork relationships between routes."""
    __tablename__ = "route_forks"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    parent_route_id = Column(UUID(as_uuid=False), ForeignKey("routes.id", ondelete="CASCADE"), nullable=False)
    fork_route_id = Column(UUID(as_uuid=False), ForeignKey("routes.id", ondelete="CASCADE"), nullable=False)
    forked_by_id = Column(String(36), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (UniqueConstraint("parent_route_id", "fork_route_id"),)


class Suggestion(Base):
    """A local suggestion on a route — like a PR comment/diff."""
    __tablename__ = "suggestions"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    route_id = Column(UUID(as_uuid=False), ForeignKey("routes.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id = Column(String(36), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    geometry_geojson = Column(Text, nullable=True)
    status = Column(
        Enum("open", "merged", "rejected", name="suggestion_status_enum", create_type=False),
        nullable=False,
        default="open",
    )
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class RouteCollection(Base):
    """A user-defined group of routes for organization and comparison."""
    __tablename__ = "route_collections"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    owner_id = Column(String(36), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    visibility = Column(
        Enum("public", "unlisted", "private", name="visibility_enum", create_type=False),
        default="private",
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class RouteCollectionItem(Base):
    """Association between a collection and a route."""
    __tablename__ = "route_collection_items"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    collection_id = Column(UUID(as_uuid=False), ForeignKey("route_collections.id", ondelete="CASCADE"), nullable=False, index=True)
    route_id = Column(UUID(as_uuid=False), ForeignKey("routes.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, default=0, nullable=False)
    added_at = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (UniqueConstraint("collection_id", "route_id"),)


class RouteTag(Base):
    """A named tag/release on a route — like a git tag."""
    __tablename__ = "route_tags"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    route_id = Column(UUID(as_uuid=False), ForeignKey("routes.id", ondelete="CASCADE"), nullable=False)
    version_id = Column(UUID(as_uuid=False), ForeignKey("route_versions.id"), nullable=False, index=True)
    tag = Column(String(100), nullable=False)
    message = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    route = relationship("Route", back_populates="tags")
    version = relationship("RouteVersion")

    __table_args__ = (UniqueConstraint("route_id", "tag"),)


# ── Integration models (PRIVATE) ─────────────────────────────────────────────

class IntegrationAccount(Base):
    """Stored OAuth tokens for external providers (Strava, etc.)."""
    __tablename__ = "integration_accounts"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id = Column(String(36), nullable=False, index=True)
    provider = Column(String(50), nullable=False)  # "strava"
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=True)
    expires_at = Column(BigInteger, nullable=True)
    external_user_id = Column(String(100), nullable=True)
    athlete_name = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    sync_failures = Column(Integer, default=0, server_default="0", nullable=False)
    # Per-account heatmap-contribution preference (migration 0048). The
    # Strava webhook worker reads this once per event instead of querying
    # the user's most-recent Activity row each time — saves ~500 redundant
    # SELECTs/week at friends-beta scale on the db-f1-micro pool.
    # Default False — opting in must be an active user choice (the import
    # flow's consent screen writes True here when the user toggles it).
    contribute_heatmap = Column(
        Boolean, default=False, server_default="false", nullable=False,
    )

    __table_args__ = (UniqueConstraint("user_id", "provider"),)


class ImportJob(Base):
    """Background import job tracking."""
    __tablename__ = "import_jobs"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id = Column(String(36), nullable=False, index=True)
    provider = Column(String(50), nullable=False)  # "strava", "file"
    status = Column(
        Enum("PENDING", "RUNNING", "COMPLETED", "FAILED", name="import_job_status_enum", create_type=False),
        nullable=False,
        default="PENDING",
    )
    # The "cursor" is a JSON blob holding progress markers:
    # {contribute_heatmap, phase, current_page, photos_imported,
    #  last_failed_phase, …}. It started as a small pagination token
    # (~10 chars, hence String(255)) but accreted more fields over time.
    # On error paths that merge a long `last_error` into the cursor it
    # has been observed near the 255 cap. Widened to Text in migration
    # 0051 to remove the foot-gun. Audit 2026-05-29 ST-S2.6.
    cursor = Column(Text, nullable=True)
    total_count = Column(Integer, default=0)
    imported_count = Column(Integer, default=0)
    skipped_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)
    gps_upgraded_count = Column(Integer, default=0)
    # Snapshot of "Strava activities still on polyline geometry" computed once
    # at end of Phase 2 ingest. The /jobs/{id} poll endpoint reads this column
    # instead of running a COUNT(*) on activities every 5 s (which starved the
    # 5+3 pool — see migration 0045 for the incident).
    gps_total = Column(Integer, default=0, server_default="0", nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class HeatmapMetric(Base):
    """Append-only snapshot of heatmap size, for the admin evolution chart.

    One row per heatmap rebuild (``source='rebuild'``) + one per daily cron
    (``source='daily'``). Numbers mirror ``compute_community_stats`` (SSOT)
    so the time-series stays consistent with the home-banner headline. Reads
    are ``ORDER BY captured_at DESC LIMIT N`` off ``ix_heatmap_metrics_captured_at``
    — never a scan. See migration 0058.
    """
    __tablename__ = "heatmap_metrics"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    captured_at = Column(DateTime(timezone=True), nullable=False, default=_now, index=True)
    heat_edges = Column(BigInteger, nullable=False, default=0, server_default="0")
    agg_ways = Column(Integer, nullable=False, default=0, server_default="0")
    activities = Column(Integer, nullable=False, default=0, server_default="0")
    contributors = Column(Integer, nullable=False, default=0, server_default="0")
    network_km = Column(Numeric, nullable=False, default=0, server_default="0")
    # NULL on the rebuild/daily snapshots (computing it needs a raw heat_edges
    # scan, off-limits on the hot path). Populated only if a caller passes it.
    grid_fallback_pct = Column(Numeric, nullable=True)
    source = Column(Text, nullable=False, default="unknown", server_default="unknown")


# ── Activity models (PRIVATE) ─────────────────────────────────────────────────

class Activity(Base):
    """A user's imported activity (private — never public)."""
    __tablename__ = "activities"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id = Column(String(36), nullable=False, index=True)
    provider = Column(String(50), nullable=False)
    provider_activity_id = Column(String(100), nullable=True)  # idempotency
    # Provenance of the underlying data (migration 0059). Distinguishes the
    # legally-distinct sources feeding the community heatmap:
    #   NULL          — legacy / pre-migration (unknown provenance)
    #   "manual_upload" — user's own uploaded archive/GPX, consented (ODbL-eligible)
    #   "strava_api"    — pulled via Strava OAuth API (NOT community-eligible)
    source = Column(String(50), nullable=True)
    sport = Column(String(50), nullable=False, default="road")
    name = Column(String(255), nullable=True)
    # Legacy: GeoJSON LineString stored as TEXT. Kept until all readers
    # migrate to the binary `geometry` column (added in 0036). Writers
    # currently dual-write both columns. See migration 0036 for details.
    geometry_geojson = Column(Text, nullable=True)
    # Binary PostGIS geometry — added in 0036 to halve storage and skip
    # `json.loads` on every read. Dual-written by ingest_activity().
    geometry = Column(Geometry("LINESTRING", srid=4326), nullable=True) if _HAS_GEOALCHEMY else Column(Text, nullable=True)
    distance_m = Column(Float, nullable=True)
    elevation_gain_m = Column(Float, nullable=True)
    file_hash = Column(String(64), nullable=True)  # SHA256 for file dedup
    contribute_heatmap = Column(Boolean, default=True)
    geometry_source = Column(String(20), default="polyline")  # "polyline" | "stream"
    moving_time = Column(Integer, nullable=True)  # seconds
    total_photo_count = Column(Integer, default=0)  # from Strava API
    activity_date = Column(DateTime(timezone=True), nullable=True)  # actual date of activity
    created_at = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        UniqueConstraint("user_id", "provider", "provider_activity_id"),
    )


class ContributionConsent(Base):
    """Audit trail of a user's explicit consent to contribute their OWN
    uploaded data to the open (ODbL) community heatmap (migration 0059).

    One row per consented submission. This is the legally-defensible
    record backing the "user's own archive, contributed by choice" basis
    for the community heatmap — it stores the EXACT consent version +
    wording the user agreed to, when, and for which provenance ``source``.
    Append-only; never updated.
    """
    __tablename__ = "contribution_consents"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id = Column(String(36), nullable=False, index=True)
    source = Column(String(50), nullable=False)  # e.g. "manual_upload"
    consent_version = Column(String(100), nullable=False)
    consent_text = Column(Text, nullable=False)  # exact wording shown to the user
    locale = Column(String(8), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)


class PendingArchiveFile(Base):
    """Durable queue for PROGRESSIVE (paced) ingest of an uploaded archive
    (migration 0059).

    A big Strava archive (thousands of GPX) must NOT be ingested on the
    request thread — that would spike CPU/RAM and OOM a db-f1-micro
    instance. Instead the upload stores each raw member to a per-user
    bucket prefix (``<user_id>/…``) and enqueues one row here; the paced
    worker ``app.jobs.ingest_pending_archives`` drains the queue in small
    batches, ingesting each member and tagging the resulting activity with
    ``source``.
    """
    __tablename__ = "pending_archive_files"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id = Column(String(36), nullable=False, index=True)
    consent_id = Column(UUID(as_uuid=False), nullable=True)
    source = Column(String(50), nullable=False, default="manual_upload")
    storage_backend = Column(String(16), nullable=False)  # "gcs" | "local"
    storage_key = Column(Text, nullable=False)
    original_filename = Column(String(512), nullable=True)
    sport = Column(String(50), nullable=True)  # pre-resolved from activities.csv
    contribute_heatmap = Column(Boolean, nullable=False, default=True)
    status = Column(String(16), nullable=False, default="pending")  # pending|done|skipped|failed
    attempts = Column(Integer, nullable=False, default=0)
    activity_id = Column(UUID(as_uuid=False), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class PendingArchive(Base):
    """ONE row per uploaded WHOLE archive (migration 0060) for the signed-URL
    direct-to-GCS upload flow.

    A REAL Strava export (~70 MB zipped, ~400 MB unzipped, 1406 members)
    cannot route through Cloud Run (~32 MB request cap) and must never be
    held in the 512 Mi web instance. So the browser PUTs the .zip straight
    to a per-user bucket key via a V4 signed URL; this row tracks that one
    object. The unzip + per-member parse happens LATER, in the scale-to-zero
    2 Gi Cloud Run job ``app.jobs.ingest_pending_archives`` which STREAMS the
    .zip from the bucket — the web never touches the archive bytes.

    Lifecycle: ``awaiting_upload`` → (browser PUT) → ``uploaded`` → (job)
    ``processing`` → ``done`` | ``failed``.

    Contrast with :class:`PendingArchiveFile` (migration 0059), which is one
    row PER MEMBER and is fed by the older small-zip direct endpoint — that
    path did the splitting on the web request thread.
    """
    __tablename__ = "pending_archives"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id = Column(String(36), nullable=False, index=True)
    consent_id = Column(UUID(as_uuid=False), nullable=True)
    source = Column(String(50), nullable=False, default="manual_upload")
    storage_backend = Column(String(16), nullable=False)  # "gcs" | "local"
    bucket_key = Column(Text, nullable=False)  # full object path incl. prefix
    fallback_sport = Column(String(50), nullable=False, default="road")
    contribute_heatmap = Column(Boolean, nullable=False, default=True)
    # awaiting_upload | uploaded | processing | done | failed
    status = Column(String(20), nullable=False, default="awaiting_upload")
    attempts = Column(Integer, nullable=False, default=0)
    members_total = Column(Integer, nullable=True)
    imported = Column(Integer, nullable=False, default=0)
    skipped = Column(Integer, nullable=False, default=0)
    failed = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class ActivityPhoto(Base):
    """A geolocated photo from a user's Strava activity (private)."""
    __tablename__ = "activity_photos"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    activity_id = Column(UUID(as_uuid=False), ForeignKey("activities.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    strava_photo_id = Column(String(100), nullable=True)
    url_thumb = Column(String(1024), nullable=False)
    url_medium = Column(String(1024), nullable=False)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    caption = Column(Text, nullable=True)
    activity_name = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (UniqueConstraint("user_id", "strava_photo_id"),)


class ActivityCell(Base):
    """S2/H3 cell covered by a user activity (private)."""
    __tablename__ = "activity_cells"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    activity_id = Column(UUID(as_uuid=False), ForeignKey("activities.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    cell_key = Column(String(32), nullable=False, index=True)
    zoom = Column(Integer, nullable=False, default=14)


# ── Heatmap models (COMMON — ODbL) ───────────────────────────────────────────

class HeatCell(Base):
    """Aggregated community heat cell (ODbL). K-anonymity enforced at read time."""
    __tablename__ = "heat_cells"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    cell_key = Column(String(32), nullable=False, index=True)
    zoom = Column(Integer, nullable=False, default=14)
    sport = Column(String(50), nullable=False, default="road")
    user_count = Column(Integer, nullable=False, default=0)
    pass_count = Column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("cell_key", "sport"),)


class EdgePopularity(Base):
    """Popularity score per road segment (ODbL)."""
    __tablename__ = "edge_popularity"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    edge_id = Column(BigInteger, nullable=False, unique=True, index=True)
    sport = Column(String(50), nullable=False, default="road")
    popularity = Column(Float, nullable=False, default=0.0)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


# ── PostGIS edge tables (replaces in-memory stores) ──────────────────────────

class HeatEdge(Base):
    """Community heat edge between two GPS-snapped points (ODbL)."""
    __tablename__ = "heat_edges"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    edge_key = Column(Text, nullable=False)
    sport = Column(Text, nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("edge_key", "sport"),
        # Migration 0034 creates a PARTIAL index (`WHERE osm_way_id IS NOT NULL`)
        # so the grid-fallback rows — which dominate the table — don't bloat the
        # index. The model must reproduce that shape so a fresh DB bootstrapped
        # from `Base.metadata.create_all()` (CI, ephemeral envs) matches
        # the prod-via-Alembic schema bit-for-bit. `index=True` on the column
        # would have generated a full index → drift.
        Index(
            "ix_heat_edges_osm_way_id",
            "osm_way_id",
            postgresql_where=sa_text("osm_way_id IS NOT NULL"),
        ),
    )
    user_count = Column(Integer, default=0)
    pass_count = Column(Integer, default=0)
    forward_count = Column(Integer, default=0)
    backward_count = Column(Integer, default=0)
    ele_delta_m = Column(Float, default=0.0)
    slope_grade = Column(Float, default=0.0)
    surface_type = Column(Text, default="unknown")
    highway_type = Column(Text, default="unknown")
    tracktype = Column(Text, nullable=True)
    smoothness = Column(Text, nullable=True)
    trail_network = Column(Boolean, default=False)
    trail_type = Column(Text, nullable=True)
    # Migration 0034 — tag heat_edges with the OSM way they snapped onto.
    # Used by build_pmtiles to render a single feature per way (no
    # spaghetti). NULL = grid-fallback row. Index lives in __table_args__
    # above as a partial index (postgresql_where) to match migration 0034.
    osm_way_id = Column(BigInteger, nullable=True)
    # Migration 0039 — confidence + source of the OSM match (spatial,
    # grid_fallback). Drives `data_quality` in /routes/surface_stats.
    match_confidence = Column(Float, nullable=True)
    match_source = Column(Text, nullable=True)
    # Migration 0042 — Crouzet confidence on the surface label. Lets the
    # frontend fade overlay opacity by confidence instead of dropping
    # everything at the data_quality=poor cliff.
    surface_confidence = Column(Float, nullable=True)
    geometry = Column(Geometry("LINESTRING", srid=4326), nullable=False) if _HAS_GEOALCHEMY else Column(Text)


class HeatEdgeContributor(Base):
    """K-anonymity dedup: tracks which user_id_hashes contributed to each edge.

    Cascade-delete is enforced at the schema level by an AFTER DELETE
    trigger on `heat_edges` (migration 0046). A real FOREIGN KEY can't
    be declared here because `heat_edges` is partitioned by sport
    (migration 0029) and its only unique constraint is
    `(edge_key, sport)` — but this table has no `sport` column. The
    trigger matches on `edge_key`, which embeds sport by construction
    (see `_edge_key` in services/ingest.py), so cross-partition
    collisions are impossible.

    PK is `(edge_key, user_id_hash, activity_id)` since migration 0052.
    The activity_id triple lets us distinguish "same user, same edge,
    different activity" (real new pass — bump pass_count) from "same
    activity re-ingested" (rebuild_heatmap / fixture bootstrap path —
    pass_count must stay put). See migration 0052 for the why.
    """
    __tablename__ = "heat_edge_contributors"

    edge_key = Column(Text, nullable=False, primary_key=True)
    user_id_hash = Column(BigInteger, nullable=False, primary_key=True)
    activity_id = Column(UUID(as_uuid=False), nullable=False, primary_key=True)
    activity_date = Column(DateTime(timezone=True), nullable=True)


class HeatCellContributor(Base):
    """K-anonymity dedup: tracks which user_id_hashes contributed to each cell."""
    __tablename__ = "heat_cell_contributors"

    cell_key = Column(Text, nullable=False, primary_key=True)
    sport = Column(Text, nullable=False, primary_key=True)
    user_id_hash = Column(BigInteger, nullable=False, primary_key=True)


class DfciEdge(Base):
    """DFCI fire-prevention track segment (ODbL, from OSM)."""
    __tablename__ = "dfci_edges"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ref = Column(Text, nullable=True)
    surface = Column(Text, default="unknown")
    highway = Column(Text, default="track")
    trail_type = Column(Text, default="DFCI")
    slope_grade = Column(Float, default=0.0)
    geometry = Column(Geometry("LINESTRING", srid=4326), nullable=False) if _HAS_GEOALCHEMY else Column(Text)


class TrailEdge(Base):
    """Marked trail segment — GR, GT, GRP, PR, EV (ODbL, from OSM)."""
    __tablename__ = "trail_edges"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ref = Column(Text, nullable=True)
    surface = Column(Text, default="unknown")
    highway = Column(Text, default="path")
    trail_type = Column(Text, nullable=False)
    slope_grade = Column(Float, default=0.0)
    geometry = Column(Geometry("LINESTRING", srid=4326), nullable=False) if _HAS_GEOALCHEMY else Column(Text)


# ── Trip models (COMMON — ODbL) ──────────────────────────────────────────────

class Trip(Base):
    """A multi-day journey composed of stages (routes), POIs, and logistics."""
    __tablename__ = "trips"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    owner_id = Column(String(36), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=True, unique=True, index=True)
    description = Column(Text, nullable=True)
    cover_image_url = Column(String(1024), nullable=True)
    sport = Column(
        Enum("road", "gravel", "mtb", "offroad", "running", name="sport_enum", create_type=False),
        default="road",
    )
    visibility = Column(
        Enum("public", "unlisted", "private", name="visibility_enum", create_type=False),
        default="private",
    )
    status = Column(String(20), default="draft")       # draft/planned/completed
    region = Column(String(255), nullable=True)
    tags_json = Column(Text, nullable=True)             # JSON array of strings
    total_distance_m = Column(Float, nullable=True)
    total_dplus_m = Column(Float, nullable=True)
    forked_from_id = Column(UUID(as_uuid=False), ForeignKey("trips.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    stages = relationship("TripStage", back_populates="trip", order_by="TripStage.day_index")
    pois = relationship("TripPOI", back_populates="trip")


class TripStage(Base):
    """A single stage (day / segment) within a trip."""
    __tablename__ = "trip_stages"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    trip_id = Column(UUID(as_uuid=False), ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    route_id = Column(UUID(as_uuid=False), ForeignKey("routes.id", ondelete="SET NULL"), nullable=True, index=True)
    day_index = Column(Integer, default=0)
    title = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    estimated_time_min = Column(Integer, nullable=True)
    lodging_type = Column(String(50), nullable=True)    # camping/hotel/refuge/free/none
    is_rest_day = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    trip = relationship("Trip", back_populates="stages")


class RouteAnnotation(Base):
    """A point annotation on a route or collection (water, food, danger, etc.)."""
    __tablename__ = "route_annotations"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    route_id = Column(UUID(as_uuid=False), ForeignKey("routes.id", ondelete="CASCADE"), nullable=True, index=True)
    collection_id = Column(UUID(as_uuid=False), ForeignKey("route_collections.id", ondelete="CASCADE"), nullable=True, index=True)
    author_id = Column(String(36), nullable=False, index=True)
    lon = Column(Float, nullable=False)
    lat = Column(Float, nullable=False)
    dist_m = Column(Float, nullable=True)
    icon = Column(String(50), nullable=False, default="info")
    text = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    __table_args__ = (
        CheckConstraint(
            "route_id IS NOT NULL OR collection_id IS NOT NULL",
            name="ck_annotation_has_parent",
        ),
    )


class OsmRoadEdge(Base):
    """Cached OSM road segment for map-matching heatmap edges (per z14 tile).

    Migration 0056 (substrate slim) recreated this table PARTITIONED
    ``BY LIST (region)`` — partitions are created on demand by
    ``app.cli.import_osm_roads`` (one per Geofabrik region) plus a DEFAULT
    partition for ``region='adhoc'`` rows (tests, dev-only Overpass fetch,
    fixture loads). Same migration moved the full-way polyline to the
    ``osm_ways`` side-table (was duplicated ×13.5 per segment = 15 GB),
    changed ``tile_key`` TEXT → BIGINT (``app.services.tile_keys``), and
    dropped ``fetched_at`` (per-region freshness lives in ``osm_import_meta``).

    Earlier column history:
    - ``ele_*`` / ``slope_grade`` (0041) — DEM-derived, populated at PBF import.
    - ``surface_confidence`` (0042) — strength of the OSM surface signal [0, 1].
    - ``bridge_yes`` / ``tunnel_yes`` (0053) — critical-connectors layer in
      ``_build_bbox_graph`` (river crossings routable without Strava traces).
    """
    __tablename__ = "osm_road_edges"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    region = Column(Text, primary_key=True, nullable=False, default="adhoc",
                    server_default="adhoc")  # LIST partition key
    tile_key = Column(BigInteger, nullable=False, index=True)  # x*100000+y, see tile_keys.py
    osm_way_id = Column(BigInteger, nullable=False)
    segment_idx = Column(Integer, nullable=False)
    surface = Column(Text, default="unknown")
    highway = Column(Text, default="unknown")
    geometry = Column(Geometry("LINESTRING", srid=4326), nullable=False) if _HAS_GEOALCHEMY else Column(Text)
    ele_start_m = Column(Float, nullable=True)
    ele_end_m = Column(Float, nullable=True)
    ele_delta_m = Column(Float, nullable=True)
    slope_grade = Column(Float, nullable=True)
    surface_confidence = Column(Float, nullable=True)
    bridge_yes = Column(Boolean, nullable=False, default=False)
    tunnel_yes = Column(Boolean, nullable=False, default=False)


class OsmWay(Base):
    """Full OSM way polyline, stored ONCE per way (migration 0056).

    Replaces the per-segment ``way_geometry`` duplication on
    ``osm_road_edges``. No GiST on ``way_geometry`` — every consumer joins
    by ``osm_way_id`` (display aggregation, exports, paved-skeleton ranking);
    zero spatial queries exist on the way polyline. ``region`` is the last
    importer that wrote the way (boundary ways shared by two regional PBFs
    keep the most recent import's copy).
    """
    __tablename__ = "osm_ways"

    osm_way_id = Column(BigInteger, primary_key=True)
    way_geometry = Column(Geometry("LINESTRING", srid=4326), nullable=False) if _HAS_GEOALCHEMY else Column(Text)
    region = Column(Text, nullable=False, default="adhoc", server_default="adhoc")


class OsmImportMeta(Base):
    """Per-region OSM import bookkeeping (migration 0056).

    Written by the import guard in ``app.cli.import_osm_roads`` after a
    successful region import; the >60 %-of-previous-count sanity check reads
    it. Replaces the dropped per-row ``fetched_at``.
    """
    __tablename__ = "osm_import_meta"

    region = Column(Text, primary_key=True)
    imported_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    row_count = Column(BigInteger, nullable=False)


class HeatEdgeAgg(Base):
    """Pre-materialised by-``(osm_way_id, sport)`` display aggregate (migration 0057).

    Holds the OSM-matched half of the heatmap aggregation (MAX counts, raw
    K-agnostic ``user_count``, smooth OSM way geometry) so the PMTiles build
    + live MVT fallback read an indexed table instead of re-running the
    ~5 M-row GROUP BY (which OOMed the whole-world build on db-f1-micro).
    Maintained INCREMENTALLY per-activity (recompute-from-source) — NOT the
    full-refresh matview that 0055 dropped. Grid-fallback rows are NOT stored
    here (read live from ``heat_edges``); see migration 0057.
    """
    __tablename__ = "heat_edges_agg"

    osm_way_id = Column(BigInteger, primary_key=True)
    sport = Column(Text, primary_key=True)
    # geometry(Geometry) — usually the way's LineString, MultiLineString on
    # the disjoint-merge fallback (local dev without the OSM PBF).
    # spatial_index=False → the explicit gist Index below is the ONLY
    # geometry index (matches migration 0057's ``ix_heat_edges_agg_geometry``;
    # avoids a duplicate geoalchemy2 auto-index under create_all in CI).
    geometry = (
        Column(Geometry("GEOMETRY", srid=4326, spatial_index=False), nullable=False)
        if _HAS_GEOALCHEMY else Column(Text)
    )
    user_count = Column(Integer, nullable=False, default=0)
    pass_count = Column(Integer, nullable=False, default=0)
    forward_count = Column(Integer, nullable=False, default=0)
    backward_count = Column(Integer, nullable=False, default=0)
    highway_type = Column(Text, nullable=False, default="unknown", server_default="unknown")
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_heat_edges_agg_geometry", "geometry", postgresql_using="gist"),
        Index("ix_heat_edges_agg_sport", "sport"),
    )


class TripPOI(Base):
    """A point of interest attached to a trip (water, food, camp, etc.)."""
    __tablename__ = "trip_pois"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    trip_id = Column(UUID(as_uuid=False), ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    stage_id = Column(UUID(as_uuid=False), ForeignKey("trip_stages.id", ondelete="SET NULL"), nullable=True, index=True)
    type = Column(String(50), default="custom")         # water/food/camp/shelter/shop/train/viewpoint/custom
    lon = Column(Float, nullable=False)
    lat = Column(Float, nullable=False)
    name = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    source = Column(String(50), default="user")         # osm/user/imported
    created_at = Column(DateTime(timezone=True), default=_now)

    trip = relationship("Trip", back_populates="pois")


# ── Contraction Hierarchies ─────────────────────────────────────────────────

class CHShortcut(Base):
    """Precomputed CH shortcut edge for fast long-distance routing."""
    __tablename__ = "ch_shortcuts"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    sport = Column(Text, nullable=False, index=True)
    source_lon = Column(Float, nullable=False)
    source_lat = Column(Float, nullable=False)
    target_lon = Column(Float, nullable=False)
    target_lat = Column(Float, nullable=False)
    via_lon = Column(Float, nullable=False)
    via_lat = Column(Float, nullable=False)
    cost = Column(Float, nullable=False)
    ch_level = Column(Integer, nullable=False)
    profile_hash = Column(Text, nullable=False)
    inline_coords = Column(Text, nullable=True)  # JSON array of [lon, lat] pairs
    cumulative_ascent_m = Column(Float, default=0.0)

    __table_args__ = (
        # Composite indexes for tile bbox queries
        # (sport, source_lon, source_lat) for forward search
        # (sport, target_lon, target_lat) for backward search
    )


class CHBuildStatus(Base):
    """Tracks CH build state per sport for lazy rebuild and backoff."""
    __tablename__ = "ch_build_status"

    sport = Column(Text, primary_key=True)
    ch_version = Column(Integer, default=0)
    edge_version = Column(Integer, default=0)
    profile_hash = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    failed_count = Column(Integer, default=0)
    last_failure_at = Column(DateTime(timezone=True), nullable=True)


# ── Notifications (post-login banner for long-running imports) ───────────────

class Notification(Base):
    """A user-scoped notification.

    Emitted when a long-running async phase finishes (Strava GPS upgrade,
    photo import, monthly resync) so the user sees the outcome the next
    time they load the app, even if they closed the tab.
    """
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # kind: "strava_import_complete" | "strava_gps_upgrade_complete"
    #     | "strava_photo_import_complete" | "strava_resync_complete"
    #     | "strava_import_failed"
    kind = Column(String(64), nullable=False)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False, default="")
    meta = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_notifications_user_unread", "user_id", "read_at", "created_at"),
    )


# ── Heatmap export — async pipeline (PRD #391 Phase 3) ───────────────────────


class ExportRequest(Base):
    """A queued / running / completed heatmap export build.

    Created by ``POST /export/heatmap/request`` for bboxes too large
    for the synchronous endpoints (Phase 2 caps at 50 km × 50 km;
    async lifts to 250 km × 250 km). The Cloud Tasks worker
    ``POST /internal/export/build/{id}`` does the build and writes
    ``gcs_uri`` + flips ``status``.

    The cleanup job (``app.jobs.cleanup_export_requests``) drops rows
    where ``expires_at < NOW()``; the GCS lifecycle on the
    ``archive/`` prefix handles the binaries.
    """
    __tablename__ = "export_requests"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    # Anonymous requests allowed (user_id NULL) — same posture as the
    # sync endpoints. Authenticated requests get the user_id for
    # future-facing "my recent exports" UI.
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    format = Column(String(32), nullable=False)
    bbox = Column(JSONB, nullable=False)
    sport = Column(String(32), nullable=True)
    min_uc = Column(Integer, nullable=True)
    days = Column(Integer, nullable=True)
    status = Column(String(16), nullable=False, server_default="queued")
    progress = Column(Float, nullable=False, server_default="0")
    gcs_uri = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=sa_text("NOW()"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=sa_text("NOW()"),
    )
    expires_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa_text("NOW() + INTERVAL '24 hours'"),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'ready', 'failed')",
            name="ck_export_requests_status",
        ),
        Index("ix_export_requests_expires_at", "expires_at"),
        Index("ix_export_requests_user_status", "user_id", "status"),
    )
