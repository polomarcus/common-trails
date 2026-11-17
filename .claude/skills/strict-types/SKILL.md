---
name: strict-types
description: Python backend typing rule for Common Trails — use a dataclass, TypedDict or Pydantic model instead of a raw dict for structured return values and parameters. Applies whenever you write or modify a function in backend/app.
user-invocable: false
---

# Strict return types (backend)

When writing or modifying Python in `backend/app`, **don't use a raw `dict` as
the return or parameter type of structured data**. Define a named type.

1. **Function returns**: `@dataclass`, `TypedDict` or Pydantic `BaseModel` — not `dict`
   / `dict[str, Any]`.
2. **Modifying a function that returns a dict**: type its shape as part of your edit.
3. **Which one**:
   - `@dataclass` (often `frozen=True`) for internal data passed between functions —
     e.g. `surface_inference.InferenceReport`, `surface_inference.Share`,
     `garmin_connect.ConnectActivity`, `archive_intake.IntakeResult`;
   - Pydantic `BaseModel` for FastAPI request/response schemas (`response_model=` on
     the route, e.g. `ImportResult`, `DefaultSportStatus`);
   - `TypedDict` for dict-shaped data serialized as-is to JSON (GeoJSON properties,
     the heatmap pointer / stats payloads).
4. **Name it after what it represents** (`InferenceReport`, `ConnectActivity`), not
   after its structure.
5. **Don't refactor untouched code** to satisfy this — only the functions you are
   actively changing. Hot paths that stream millions of items (the raw-trace build's
   per-cell / per-feature dicts in `raw_trace_display.py`) stay plain where a class
   would cost memory or speed; say so in a comment if you keep a dict there.
