"""
config.py — optional settings, read from the environment or from a .env file
in the repo root (copy .env.example to .env). Real environment variables win
over .env. Everything here is optional; the tools work with none of it set.
"""

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent / ".env"


def load_env(path=ENV_FILE):
    """Load KEY=value lines into os.environ without overriding what's already set."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env()

# A personal integration: the 40K VOD Index ingest tool, which only exists on
# the maintainer's machine. Unset, the web app hides the import entirely.
VOD_INGEST_URL = os.environ.get("VOD_INGEST_URL", "").strip().rstrip("/")
