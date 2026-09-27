"""Models: browse every Force Org model in 3D, and choose one for a list's model."""

from app.core.api import router

TOOL = {"name": "Models",
        "description": "Browse every Force Org model by army, search them, and see them in 3D."}

router = router()  # the page uses the shared /api/catalog routes

__all__ = ["TOOL", "router"]
