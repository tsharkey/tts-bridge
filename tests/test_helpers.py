import os

import config
import tts_bridge


def test_lua_str_survives_closing_brackets():
    s = tts_bridge.lua_str("a]]b]=]c")
    assert s.startswith("[==[") and s.endswith("]==]")
    assert "a]]b]=]c" in s


def test_load_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# comment\n\nFOO_A=one\nexport FOO_B="two words"\nFOO_C=from-file\nnot a setting\n')
    monkeypatch.delenv("FOO_A", raising=False)
    monkeypatch.delenv("FOO_B", raising=False)
    monkeypatch.setenv("FOO_C", "from-env")
    config.load_env(env)
    assert os.environ["FOO_A"] == "one"
    assert os.environ["FOO_B"] == "two words"
    assert os.environ["FOO_C"] == "from-env"  # the real environment wins
    for k in ("FOO_A", "FOO_B"):
        monkeypatch.delenv(k)
