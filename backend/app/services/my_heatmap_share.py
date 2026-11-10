"""Sharing "Ma heatmap" through an unguessable token link (opt-in, revocable).

Design: docs/ma-heatmap-fichier-prive.md §10. In short:

- ``create_or_regenerate``: a fresh ``secrets.token_urlsafe(24)`` (192 bits,
  never derived from the user id or the file version). Only its SHA-256 is
  stored (``my_heatmap_shares.token_sha256``, unique index); the clear token
  is returned ONCE, to the owner, at creation. Regenerating replaces the hash:
  the old token is dead on the next request.
- ``revoke``: deletes the row (every ``/s/{token}/…`` URL → 404 at once, the
  lookup runs on every request, nothing is cached server-side), clears
  ``share_version`` and deletes the ``shares/{user}/`` objects.
- ``resolve``: token → (owner, shared artefact). The public routes build the
  object key from ``share_version`` under ``shares/`` ONLY — the private
  ``users/`` objects are unreachable from a token by construction.
- No automatic expiry (product decision, §10.2): the link is pasted as a layer
  in gpx.studio / OsmAnd / Locus, where an expiring URL silently breaks the
  layer. Safety comes from the 192-bit token, hash-only storage, endpoint
  masking, instant revocation and regeneration; the UI shows the creation date.

NEVER log a token (only its owner id); ``app.sentry_scrub`` redacts ``/s/…``
paths from Sentry events.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text as sa_text

from app.services import my_heatmap_files as mhf

TOKEN_BYTES = 24  # 192 bits
# token_urlsafe(24) → exactly 32 chars of the URL-safe base64 alphabet.
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def share_urls(base_url: str, token: str) -> dict:
    root = f"{base_url.rstrip('/')}/s/{token}"
    return {
        "style": f"{root}/style.json",
        "raster": f"{root}/{{z}}/{{x}}/{{y}}.png",
        "vector": f"{root}/{{z}}/{{x}}/{{y}}.pbf",
        "pmtiles": f"{root}/heatmap.pmtiles",
    }


def get_created_at(db, user_id: str) -> datetime | None:
    return db.execute(
        sa_text("SELECT created_at FROM my_heatmap_shares WHERE user_id::text = :u"), {"u": user_id}
    ).scalar()


def status(db, user_id: str) -> dict:
    """What the owner's UI needs (never the token: only its hash is stored)."""
    if not mhf.is_enabled_for(user_id):
        return {"available": False, "active": False}
    created = get_created_at(db, user_id)
    if created is None:
        return {"available": True, "active": False}
    state = mhf.read_state(db, user_id)
    ready = state is not None and state.share_version is not None
    if not ready:
        mhf.schedule_rebuild(user_id, reason="share-pending")
    return {
        "available": True,
        "active": True,
        "created_at": created.isoformat(),
        "ready": ready,
        "activity_count": state.share_activity_count if ready and state else None,
    }


def create_or_regenerate(db, user_id: str) -> tuple[str, datetime]:
    """New token for ``user_id`` (replacing any previous one). Commits.
    The shared artefact is not tied to the token: a regeneration keeps it."""
    token = new_token()
    created = db.execute(
        sa_text(
            """
            INSERT INTO my_heatmap_shares (user_id, token_sha256, created_at)
            VALUES (CAST(:u AS uuid), :h, now())
            ON CONFLICT (user_id) DO UPDATE
               SET token_sha256 = EXCLUDED.token_sha256, created_at = EXCLUDED.created_at
            RETURNING created_at
            """
        ),
        {"u": user_id, "h": token_sha256(token)},
    ).scalar()
    db.commit()
    state = mhf.read_state(db, user_id)
    if state is None or state.share_version is None:
        mhf.schedule_rebuild(user_id, reason="share-created")
    return token, created


def revoke(db, user_id: str) -> bool:
    """Disable sharing. The link dies with the row (commit); the shared
    objects are deleted right after (``sweep-orphans`` is the backstop).
    Returns whether a share existed."""
    n = db.execute(
        sa_text("DELETE FROM my_heatmap_shares WHERE user_id::text = :u"), {"u": user_id}
    ).rowcount
    db.execute(
        sa_text(
            "UPDATE my_heatmap_files SET share_version = NULL, share_pmtiles_bytes = NULL, "
            "share_activity_count = NULL WHERE user_id = :u"
        ),
        {"u": user_id},
    )
    db.commit()
    mhf.delete_share_objects(user_id)
    return bool(n)


def heal_missing_object(user_id: str, share_version: str) -> None:
    """The row points at a shared object that is gone (a revoke or a sweep
    raced a publish): forget it and rebuild — otherwise the file looks fresh
    and the link would serve 404s until the user's data changes."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.execute(
            sa_text(
                "UPDATE my_heatmap_files SET share_version = NULL, share_pmtiles_bytes = NULL, "
                "share_activity_count = NULL WHERE user_id = :u AND share_version = :v"
            ),
            {"u": user_id, "v": share_version},
        )
        db.commit()
    finally:
        db.close()
    mhf.schedule_rebuild(user_id, reason="share-object-missing")


@dataclass(frozen=True)
class ResolvedShare:
    user_id: str
    share_version: str | None
    share_pmtiles_bytes: int

    @property
    def key(self) -> str | None:
        """The ONLY object a token can reach: ``shares/{user}/{v}.pmtiles``."""
        if not self.share_version or not self.share_pmtiles_bytes:
            return None
        return mhf.share_pmtiles_key(self.user_id, self.share_version)


def resolve(db, token: str) -> ResolvedShare | None:
    """Token → share, or None (unknown / revoked / malformed / feature off).
    One indexed lookup on the token's hash per call — no cache, so a
    revocation or regeneration takes effect on the very next request."""
    if not TOKEN_RE.match(token or ""):
        return None
    row = db.execute(
        sa_text(
            """
            SELECT s.user_id::text, f.share_version, f.share_pmtiles_bytes,
                   f.data_rev, f.built_rev
              FROM my_heatmap_shares s
              LEFT JOIN my_heatmap_files f ON f.user_id = s.user_id::text
             WHERE s.token_sha256 = :h
            """
        ),
        {"h": token_sha256(token)},
    ).first()
    if row is None or not mhf.is_enabled_for(row[0]):
        return None
    if row[1] is None or row[3] != row[4]:
        # Self-heal (debounced): shared artefact missing or behind the data.
        mhf.schedule_rebuild(row[0], reason="share-view")
    return ResolvedShare(user_id=row[0], share_version=row[1], share_pmtiles_bytes=int(row[2] or 0))
