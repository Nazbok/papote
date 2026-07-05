"""Tests de bout en bout du serveur papote.

Démarre un vrai serveur sur un port éphémère (base SQLite temporaire) puis
pilote plusieurs clients WebSocket pour vérifier les principaux flux : profils,
groupes, serveurs/salons, salons vocaux (signalisation), appels + journal,
images et réactions.

Utilisable de deux façons :
    python -m pytest tests/
    python tests/test_server_e2e.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def running_server():
    """Lance papote.server dans un sous-processus et le stoppe à la sortie."""
    port = _free_port()
    db = tempfile.mktemp(suffix=".db")
    env = dict(os.environ, PYTHONPATH=ROOT)
    proc = subprocess.Popen(
        [sys.executable, "-m", "papote.server", "--host", "127.0.0.1",
         "--port", str(port), "--db", db],
        cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/"
        for _ in range(100):
            try:
                if urllib.request.urlopen(url, timeout=0.5).status == 200:
                    break
            except Exception:
                time.sleep(0.1)
        else:
            raise RuntimeError("le serveur de test n'a pas démarré")
        yield f"ws://127.0.0.1:{port}/"
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)
        with contextlib.suppress(FileNotFoundError):
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(db + suffix):
                    os.remove(db + suffix)


async def _recv_until(ws, pred, timeout=3):
    try:
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            if pred(m):
                return m
    except asyncio.TimeoutError:
        return None


async def _send(ws, op, **kw):
    await ws.send(json.dumps({"op": op, **kw}))


async def _drain(ws, t=0.3):
    with contextlib.suppress(asyncio.TimeoutError):
        while True:
            await asyncio.wait_for(ws.recv(), t)


async def _scenario(url):
    checks = []

    def ck(cond, label):
        checks.append((bool(cond), label))

    sana = await websockets.connect(url)
    bob = await websockets.connect(url)
    carol = await websockets.connect(url)

    # --- inscriptions ---
    await _send(sana, "register", username="sana", password="pw123")
    r = await _recv_until(sana, lambda m: m.get("reply") == "register")
    ck(r["ok"], "inscription sana")
    ck("profile" in r and "servers" in r, "auth: profile + servers")

    await _send(bob, "register", username="bob", password="pw123")
    r = await _recv_until(bob, lambda m: m.get("reply") == "register")
    ck(r["ok"], "inscription bob")

    await _send(carol, "register", username="carol", password="pw123")
    await _recv_until(carol, lambda m: m.get("reply") == "register")

    # amitiés
    await _send(sana, "friend_add", username="bob"); await _drain(sana); await _drain(bob)
    await _send(bob, "friend_accept", username="sana"); await _drain(sana); await _drain(bob)
    await _send(sana, "friend_add", username="carol"); await _drain(sana); await _drain(carol)
    await _send(carol, "friend_accept", username="sana"); await _drain(sana); await _drain(carol)

    # --- profil : bannière + statut + couleur ---
    await _send(sana, "profile_update", banner="data:image/png;base64,ZZ",
                status="en stream", accent="#ff6fae", bio="yo")
    r = await _recv_until(sana, lambda m: m.get("reply") == "profile_update")
    ck(r["ok"] and r["profile"]["banner"] and r["profile"]["status"] == "en stream",
       "profil: bannière + statut")
    ev = await _recv_until(bob, lambda m: m.get("ev") == "profile_updated")
    ck(ev and ev["card"]["username"] == "sana", "profile_updated poussé aux amis")

    # --- groupe : ajout multiple ---
    await _send(sana, "group_create", name="Team", members=[])
    r = await _recv_until(sana, lambda m: m.get("reply") == "group_create")
    gid = r["groups"][-1]["id"]
    await _drain(sana)
    await _send(sana, "group_add", group_id=gid, usernames=["bob", "carol"])
    r = await _recv_until(sana, lambda m: m.get("reply") == "group_add")
    ck({"sana", "bob", "carol"} <= set(r["group"]["members"]), "group_add multiple")

    # --- serveur + salons + membres ---
    await _send(sana, "server_create", name="Serveur")
    r = await _recv_until(sana, lambda m: m.get("reply") == "server_create")
    srv = r["server"]; sid = srv["id"]
    voice_ch = [c for c in srv["channels"] if c["kind"] == "voice"][0]
    ck(len(srv["channels"]) == 2, "serveur: 2 salons par défaut")
    await _drain(sana)
    await _send(sana, "server_add", server_id=sid, usernames=["bob", "carol"])
    r = await _recv_until(sana, lambda m: m.get("reply") == "server_add")
    ck(set(r["added"]) == {"bob", "carol"}, "server_add multiple")
    ev = await _recv_until(bob, lambda m: m.get("ev") == "server_added")
    ck(ev is not None, "server_added poussé")

    # --- vocal : join + signalisation relayée ---
    await _send(bob, "voice_join", channel_id=voice_ch["id"])
    r = await _recv_until(bob, lambda m: m.get("reply") == "voice_join")
    ck(r["ok"] and r["peers"] == [], "bob rejoint le vocal")
    await _drain(carol)
    await _send(carol, "voice_join", channel_id=voice_ch["id"])
    r = await _recv_until(carol, lambda m: m.get("reply") == "voice_join")
    ck(r["ok"] and r["peers"] == ["bob"], "carol voit bob dans le vocal")
    await carol.send(json.dumps({"op": "voice_signal", "channel_id": voice_ch["id"],
                                 "to": "bob", "kind": "sdp", "data": "OFFER"}))
    ev = await _recv_until(bob, lambda m: m.get("ev") == "voice_signal")
    ck(ev and ev["from"] == "carol", "signalisation vocale relayée")
    await _send(bob, "voice_leave"); await _drain(bob); await _drain(carol)

    # --- image + réactions ---
    img = "data:image/png;base64,iVBORw0KGgoAAAAN"
    await _send(sana, "send", to_type="dm", to="bob", body="tiens", img=img)
    ev = await _recv_until(bob, lambda m: m.get("ev") == "message")
    ck(ev and ev["msg"]["attachment"] == img, "image reçue")
    mid = ev["msg"]["id"]
    await _drain(sana)
    await _send(bob, "react", message_id=mid, emoji="🔥")
    ev = await _recv_until(sana, lambda m: m.get("ev") == "reaction")
    ck(ev and ev["reactions"][0]["emoji"] == "🔥" and "bob" in ev["reactions"][0]["users"],
       "réaction diffusée")
    await _send(bob, "react", message_id=mid, emoji="🔥")
    ev = await _recv_until(sana, lambda m: m.get("ev") == "reaction")
    ck(ev and ev["reactions"] == [], "réaction retirée (toggle)")
    await _send(carol, "react", message_id=mid, emoji="😀")
    r = await _recv_until(carol, lambda m: m.get("reply") == "react")
    ck(r and r["ok"] is False, "un tiers ne peut pas réagir à un DM privé")

    # --- appel + journal ---
    await sana.send(json.dumps({"op": "call_offer", "to": "bob", "sdp": "O"}))
    await _recv_until(bob, lambda m: m.get("ev") == "call_incoming")
    await bob.send(json.dumps({"op": "call_answer", "to": "sana", "sdp": "A"}))
    await _recv_until(sana, lambda m: m.get("ev") == "call_answered")
    await asyncio.sleep(0.3)
    await sana.send(json.dumps({"op": "call_end", "to": "bob"}))
    await _drain(sana); await _drain(bob)
    await _send(sana, "call_history")
    r = await _recv_until(sana, lambda m: m.get("reply") == "call_history")
    ck(r["calls"] and r["calls"][0]["status"] == "answered", "journal d'appels (répondu)")

    for w in (sana, bob, carol):
        await w.close()
    return checks


def run():
    with running_server() as url:
        checks = asyncio.run(_scenario(url))
    failed = [label for ok, label in checks if not ok]
    for ok, label in checks:
        print(("  OK  " if ok else " FAIL ") + label)
    print(f"\n{len(checks) - len(failed)}/{len(checks)} OK")
    return failed


def test_server_e2e():
    """Point d'entrée pytest."""
    failed = run()
    assert not failed, f"échecs : {failed}"


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
