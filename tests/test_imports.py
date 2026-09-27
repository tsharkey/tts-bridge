"""Every module imports without TTS running (nothing connects at import time)."""

import importlib

import pytest


@pytest.mark.parametrize("name", ["army", "board", "recreate", "tts_bridge", "config",
                                  "app.server", "app.tools.board_replay.vision"])
def test_imports(name):
    importlib.import_module(name)
