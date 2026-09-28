"""Layouts: the exact terrain of every LCT layout (layouts/): look, fix, import and retire."""

from app.tools.layouts.routes import router

TOOL = {"name": "Layouts",
        "description": "The exact terrain of every LCT layout, which line of sight and the board view use. "
                       "Fix a piece's category, heights or shape, import a new LCT version, compare with the table."}

__all__ = ["TOOL", "router"]
