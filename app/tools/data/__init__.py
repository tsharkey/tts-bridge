"""Data cache: see what's in the local data cache and fetch or refresh it."""

from app.tools.data.routes import router

TOOL = {"name": "Data cache",
        "description": "See which datasheets and model data are cached, and fetch or refresh them."}

__all__ = ["TOOL", "router"]
