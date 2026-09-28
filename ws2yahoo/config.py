"""Local state kept in ``~/.config/ws2yahoo`` (override with ``WS2YAHOO_HOME``).

* ``yahoo.json``        - Yahoo session cookie (private)
* ``wealthsimple.json`` - Wealthsimple access token (private, ~30 min)
* ``config.json``       - settings: default ``portfolio`` and ``symbols`` overrides
* ``synced.json``       - Wealthsimple activity ids already mirrored, per portfolio
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

HOME = Path(os.environ.get("WS2YAHOO_HOME") or Path.home() / ".config" / "ws2yahoo")


def load(name: str, default: Any = None) -> Any:
    try:
        return json.loads((HOME / name).read_text())
    except (OSError, ValueError):
        return default


def save(name: str, data: Any, private: bool = False) -> Path:
    HOME.mkdir(parents=True, exist_ok=True)
    path = HOME / name
    path.write_text(json.dumps(data, indent=2) + "\n")
    if private:
        os.chmod(path, 0o600)
    return path


def settings() -> dict:
    return load("config.json", {})
