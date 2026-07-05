"""Couche réseau côté client : connexion WebSocket + config locale."""

from __future__ import annotations

import json
import os
from pathlib import Path

import websockets

from . import protocol

CONFIG_FILE = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "papote" / "config.json"
)


def load_config() -> dict:
    try:
        with CONFIG_FILE.open() as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(**kw) -> None:
    cfg = load_config()
    cfg.update(kw)
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_FILE.open("w") as fh:
            json.dump(cfg, fh)
    except OSError:
        pass


async def open_connection(url: str):
    return await websockets.connect(url, max_size=2 ** 20, open_timeout=10)


async def auth(ws, op: str, username="", password="", token=""):
    """Envoie register/login/auth et renvoie la 1re réponse (ignore les events)."""
    payload = {"op": op}
    if op == "auth":
        payload["token"] = token
    else:
        payload["username"] = username
        payload["password"] = password
    await ws.send(protocol.encode(payload))
    while True:
        msg = protocol.decode(await ws.recv())
        if "reply" in msg:
            return msg
