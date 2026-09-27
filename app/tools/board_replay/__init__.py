"""Board from image: rebuild a board state in TTS from a top-down picture of a game."""

from app.tools.board_replay.routes import router

TOOL = {"name": "Board from image",
        "description": "Rebuild a game in TTS from a top-down picture: fit the table, find the units, send it over."}

__all__ = ["TOOL", "router"]
