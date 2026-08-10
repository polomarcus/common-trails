"""Shared Pydantic schemas for integration endpoints."""
from pydantic import BaseModel


class ImportJobResponse(BaseModel):
    job_id: str
    status: str
    total_count: int = 0
    imported_count: int
    skipped_count: int = 0
    failed_count: int
    last_error: str | None
    gps_upgraded_count: int = 0
    gps_total: int = 0
    phase: str | None = None
    current_page: int | None = None
    photos_imported: int = 0
    # PR-E High #6: when a phase fails, persist which one so the frontend
    # can surface "GPS upgrade failed" instead of transitioning silently
    # to a done card with `failed_count > 0` and no context.
    last_failed_phase: str | None = None


class DisconnectResponse(BaseModel):
    status: str
