"""Borrow logins from your everyday Chrome through the DevTools protocol.

Turn it on once at ``chrome://inspect/#remote-debugging``. Chrome then writes a
``DevToolsActivePort`` file in its profile folder, which tells us where to
connect. Brave, Edge and Chromium work the same way. Set ``WS2YAHOO_CDP_URL``
(``ws://...``) to point at any other browser.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import websocket


class BrowserError(RuntimeError):
    pass


def _profile_dirs() -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support"
        names = ["Google/Chrome", "Google/Chrome Beta", "Google/Chrome Canary", "Chromium",
                 "BraveSoftware/Brave-Browser", "Microsoft Edge"]
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        names = ["Google/Chrome/User Data", "Chromium/User Data", "BraveSoftware/Brave-Browser/User Data",
                 "Microsoft/Edge/User Data"]
    else:
        base = home / ".config"
        names = ["google-chrome", "google-chrome-beta", "chromium", "BraveSoftware/Brave-Browser", "microsoft-edge"]
    return [base / n for n in names]


def devtools_url() -> str:
    if os.environ.get("WS2YAHOO_CDP_URL"):
        return os.environ["WS2YAHOO_CDP_URL"]
    for d in _profile_dirs():
        try:
            port, path = (d / "DevToolsActivePort").read_text().split("\n")[:2]
            return f"ws://127.0.0.1:{port.strip()}{path.strip()}"
        except (OSError, ValueError):
            continue
    raise BrowserError(
        "Can't find a browser to read your logins from. In Chrome, open "
        "chrome://inspect/#remote-debugging and turn on remote debugging, then retry."
    )


class Browser:
    """Minimal DevTools client. Use as a context manager."""

    def __init__(self, url: Optional[str] = None):
        self.url = url or devtools_url()
        self._id = 0

    def __enter__(self) -> "Browser":
        try:
            self._ws = websocket.create_connection(self.url, timeout=30, suppress_origin=True)
        except websocket.WebSocketBadStatusException as e:
            raise BrowserError("Chrome declined the connection. Retry and click Allow when Chrome asks "
                               "to allow remote debugging.") from e
        except (OSError, websocket.WebSocketException) as e:
            raise BrowserError(f"Couldn't connect to the browser at {self.url}: {e}") from e
        return self

    def __exit__(self, *exc):
        self._ws.close()

    def call(self, method: str, **params) -> dict:
        self._id += 1
        self._ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:  # skip events until our reply arrives
            msg = json.loads(self._ws.recv())
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise BrowserError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result", {})

    def cookies(self) -> list[dict]:
        return self.call("Storage.getCookies")["cookies"]

    def cookie(self, name: str, domain_suffix: str) -> Optional[dict]:
        return next((c for c in self.cookies()
                     if c["name"] == name and c["domain"].lstrip(".").endswith(domain_suffix)), None)

    def open_background_tab(self, url: str) -> str:
        return self.call("Target.createTarget", url=url, background=True)["targetId"]

    def close_tab(self, target_id: str) -> None:
        self.call("Target.closeTarget", targetId=target_id)


def cookie_header(cookies: list[dict], host: str) -> str:
    """The Cookie header a browser would send to ``host``."""
    def sent_to(domain: str) -> bool:
        d = domain.lstrip(".")
        return bool(d) and (host == d or host.endswith("." + d))

    return "; ".join(f"{c['name']}={c['value']}" for c in cookies if sent_to(c["domain"]))
