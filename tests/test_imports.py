"""Every module imports without TTS running (nothing connects at import time)."""

import importlib

import pytest


@pytest.mark.parametrize("name", ["army", "board", "recreate", "tts_bridge", "config", "vision", "server"])
def test_imports(name):
    importlib.import_module(name)
