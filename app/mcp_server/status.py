"""status: whether the hub can reach TTS, and whether LCT is the game loaded."""

from typing import TypedDict

from app.core import tts


class Status(TypedDict):
    connected: bool   # TTS is running with a game loaded and answers through the hub
    lct: bool         # that game is the LCT table


def status() -> Status:
    """Whether Tabletop Simulator is reachable with a game loaded ("connected"), and whether that
    game is the LCT table ("lct"). Call it first when other tools can't reach the game."""
    return tts.status()


TOOLS = [status]
