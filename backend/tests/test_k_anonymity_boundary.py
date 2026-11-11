"""K-anonymity default — the export module's advertised floor.

(The per-edge ``user_count >= :k`` SQL boundary tests that lived here pinned
the removed ``heat_edges`` read models. The raw render's distinct-user floor is
``HEATMAP_MIN_USERS`` — covered in ``test_raw_trace_display.py``.)
"""
from __future__ import annotations


def test_default_k_value_is_2_in_prod() -> None:
    """The HEATMAP_K_ANONYMITY default in the export module must be 2.

    Lowering this default to 1 in prod would silently expose single-user
    data on the public heatmap. Tested via module read so a future PR
    that flips the default fails CI.
    """
    import os
    # Reset env to verify the hard-coded default
    saved = os.environ.pop("HEATMAP_K_ANONYMITY", None)
    try:
        # Re-import to pick up the default (the module reads at import time)
        import importlib

        from app.api import export as heatmap_module
        importlib.reload(heatmap_module)
        assert heatmap_module.HEATMAP_K_ANONYMITY == 2, (
            f"HEATMAP_K_ANONYMITY default is {heatmap_module.HEATMAP_K_ANONYMITY}, expected 2. "
            "Lowering this default leaks single-user data publicly. "
            "Use the env var HEATMAP_K_ANONYMITY=1 in dev/docker-compose only."
        )
    finally:
        if saved is not None:
            os.environ["HEATMAP_K_ANONYMITY"] = saved
        # Reload once more to restore module state
        import importlib

        from app.api import export as heatmap_module
        importlib.reload(heatmap_module)
