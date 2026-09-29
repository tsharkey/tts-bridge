import os
import shutil
import subprocess

import pytest

import config
import tts_bridge


def test_lua_str_survives_closing_brackets():
    s = tts_bridge.lua_str("a]]b]=]c")
    assert s.startswith("[==[") and s.endswith("]==]")
    assert "a]]b]=]c" in s


@pytest.mark.skipif(not shutil.which("luajit"), reason="LuaJIT isn't installed")
def test_lua_str_reads_back_in_lua(tmp_path):
    texts = ["[Pathfinder Team]", "ends ]=", "a]]b]=]c", "plain", "]", "", "[b]x[/b]\nnext"]
    lua = tmp_path / "strings.lua"
    lua.write_text("\n".join(f"io.write({tts_bridge.lua_str(t)}, '\\0')" for t in texts))
    run = subprocess.run(["luajit", str(lua)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert run.stdout.split("\0")[:-1] == texts   # a long bracket drops the newline right after it


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
