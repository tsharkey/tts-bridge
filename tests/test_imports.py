"""Every module imports without TTS running (nothing connects at import time)."""

import importlib

import pytest


@pytest.mark.parametrize("name", ["army", "board", "recreate", "tts_bridge", "config", "data", "bsdata", "datasheets", "mods", "bases",
                                  "app.server", "app.tools.board_replay.vision"])
def test_imports(name):
    importlib.import_module(name)
