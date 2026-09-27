"""Scribe: turn an army list (any export) into a TTS army, as a Saved Object or spawned onto the table."""

from app.tools.scribe.routes import router

TOOL = {"name": "Scribe",
        "description": "Paste an army list from any app, check what it became, then save it as a TTS Saved Object "
                       "or spawn it onto the table."}

__all__ = ["TOOL", "router"]
