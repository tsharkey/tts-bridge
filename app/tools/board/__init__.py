"""Board view: the table from above, live; click a unit for what it can see and how far it reaches."""

from app.tools.board.routes import router

TOOL = {"name": "Board view",
        "description": "The table from above, kept up to date. Click a unit to see what it can see and how far it "
                       "reaches; shift-click another for the distance between them."}

__all__ = ["TOOL", "router"]
