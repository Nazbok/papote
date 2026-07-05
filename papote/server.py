"""Serveur papote : relaie comptes, amis, groupes et messages via WebSocket."""

from __future__ import annotations

import argparse
import asyncio
import signal

import websockets

from . import DEFAULT_PORT, casino, protocol
from .db import DB


class Server:
    def __init__(self, db: DB):
        self.db = db
        self.online: dict[str, set] = {}   # username -> {websockets}

    # --- présence / envoi ---------------------------------------------------

    def _add_online(self, username, ws):
        self.online.setdefault(username, set()).add(ws)

    def _remove_online(self, username, ws):
        socks = self.online.get(username)
        if socks:
            socks.discard(ws)
            if not socks:
                self.online.pop(username, None)

    def is_online(self, username) -> bool:
        return username in self.online

    async def push(self, username: str, text: str):
        for ws in list(self.online.get(username, ())):
            try:
                await ws.send(text)
            except websockets.WebSocketException:
                pass

    async def notify_presence(self, user, online: bool):
        for f in self.db.list_friends(user["id"]):
            if f["kind"] == "friend":
                await self.push(f["username"],
                                protocol.event("presence", username=user["username"],
                                               online=online))

    def _friends_payload(self, uid):
        friends = self.db.list_friends(uid)
        for f in friends:
            f["online"] = self.is_online(f["username"])
        return friends

    # --- authentification ---------------------------------------------------

    async def handle_auth(self, ws, op, req, prev_user):
        try:
            if op == "register":
                user = self.db.create_user(req.get("username", ""), req.get("password", ""))
            elif op == "login":
                user = self.db.verify_user(req.get("username", ""), req.get("password", ""))
                if not user:
                    await ws.send(protocol.err(op, "Identifiants incorrects."))
                    return prev_user
            elif op == "auth":
                user = self.db.get_user_by_token(req.get("token", ""))
                if not user:
                    await ws.send(protocol.err(op, "Jeton invalide."))
                    return prev_user
            else:
                return prev_user
        except ValueError as e:
            await ws.send(protocol.err(op, str(e)))
            return prev_user

        self._add_online(user["username"], ws)
        await ws.send(protocol.ok(
            op,
            token=user["token"],
            username=user["username"],
            friends=self._friends_payload(user["id"]),
            groups=self.db.list_groups(user["id"]),
        ))
        await self.notify_presence(user, True)
        return user

    # --- dispatch des requêtes ---------------------------------------------

    async def dispatch(self, ws, user, op, req):
        uid = user["id"]
        uname = user["username"]
        try:
            if op == "friend_add":
                status = self.db.friend_request(uid, req["username"])
                if status == "pending":
                    await self.push(req["username"],
                                    protocol.event("friend_request", **{"from": uname}))
                else:  # accepté (demande inverse existait)
                    await self.push(req["username"],
                                    protocol.event("friend_accepted", username=uname))
                    other = self.db.get_user(req["username"])
                    await self.push(req["username"],
                                    protocol.event("presence", username=uname,
                                                   online=True))
                    await self.push(uname,
                                    protocol.event("presence", username=req["username"],
                                                   online=self.is_online(req["username"])))
                await ws.send(protocol.ok(op, status=status,
                                          friends=self._friends_payload(uid)))

            elif op == "friend_accept":
                self.db.friend_accept(uid, req["username"])
                await self.push(req["username"],
                                protocol.event("friend_accepted", username=uname))
                await self.push(req["username"],
                                protocol.event("presence", username=uname, online=True))
                await ws.send(protocol.ok(op, friends=self._friends_payload(uid)))

            elif op == "friend_list":
                await ws.send(protocol.ok(op, friends=self._friends_payload(uid)))

            elif op == "group_create":
                group = self.db.create_group(req["name"], uid, req.get("members", []))
                for m in group["members"]:
                    await self.push(m, protocol.event("group_added", group=group))
                await ws.send(protocol.ok(op, group=group,
                                          groups=self.db.list_groups(uid)))

            elif op == "group_list":
                await ws.send(protocol.ok(op, groups=self.db.list_groups(uid)))

            elif op == "group_add":
                gid = int(req["group_id"])
                if not self.db.is_group_member(gid, uid):
                    raise ValueError("Tu ne fais pas partie de ce groupe.")
                self.db.add_group_member(gid, req["username"])
                group = self.db.get_group(gid)
                for m in group["members"]:
                    await self.push(m, protocol.event("group_added", group=group))
                await ws.send(protocol.ok(op, group=group))

            elif op == "send":
                await self._handle_send(ws, user, req)

            elif op == "history":
                await self._handle_history(ws, user, req)

            elif op == "casino_state":
                await ws.send(protocol.ok(op, **self.db.casino_state(uid)))

            elif op == "casino_play":
                await self._handle_casino_play(ws, user, req)

            elif op == "casino_bonus":
                balance = self.db.claim_bonus(uid)
                await ws.send(protocol.ok(op, balance=balance))

            elif op == "leaderboard":
                limit = min(int(req.get("limit", 20)), 100)
                await ws.send(protocol.ok(op, players=self.db.leaderboard(limit)))

            else:
                await ws.send(protocol.err(op or "?", "Opération inconnue."))
        except (KeyError, ValueError) as e:
            await ws.send(protocol.err(op or "?", str(e) or "Requête invalide."))

    async def _handle_send(self, ws, user, req):
        uid, uname = user["id"], user["username"]
        body = (req.get("body") or "").strip()
        to_type = req.get("to_type")
        if not body:
            return
        if len(body) > 4000:
            body = body[:4000]
        if to_type == "dm":
            target = self.db.get_user(req.get("to", ""))
            if not target:
                await ws.send(protocol.err("send", "Destinataire introuvable."))
                return
            if not self.db.are_friends(uid, target["id"]):
                await ws.send(protocol.err("send", "Vous n'êtes pas amis."))
                return
            saved = self.db.save_message(uid, "dm", target["id"], body)
            msg = {"id": saved["id"], "from": uname, "to_type": "dm",
                   "to": target["username"], "body": body, "ts": saved["ts"]}
            ev = protocol.event("message", msg=msg)
            await self.push(target["username"], ev)
            await self.push(uname, ev)
        elif to_type == "group":
            gid = int(req.get("to"))
            if not self.db.is_group_member(gid, uid):
                await ws.send(protocol.err("send", "Tu n'es pas dans ce groupe."))
                return
            saved = self.db.save_message(uid, "group", gid, body)
            msg = {"id": saved["id"], "from": uname, "to_type": "group",
                   "to": gid, "body": body, "ts": saved["ts"]}
            ev = protocol.event("message", msg=msg)
            for mid in self.db.group_member_ids(gid):
                member = self.db.get_user_by_id(mid)
                if member:
                    await self.push(member["username"], ev)

    async def _handle_history(self, ws, user, req):
        uid = user["id"]
        with_type = req.get("with_type")
        limit = min(int(req.get("limit", 100)), 200)
        if with_type == "dm":
            other = self.db.get_user(req.get("with", ""))
            if not other:
                await ws.send(protocol.err("history", "Utilisateur introuvable."))
                return
            msgs = self.db.dm_history(uid, other["id"], limit)
            await ws.send(protocol.ok("history", with_type="dm",
                                      **{"with": other["username"]}, messages=msgs))
        elif with_type == "group":
            gid = int(req.get("with"))
            if not self.db.is_group_member(gid, uid):
                await ws.send(protocol.err("history", "Accès refusé."))
                return
            msgs = self.db.group_history(gid, limit)
            await ws.send(protocol.ok("history", with_type="group",
                                      **{"with": gid}, messages=msgs))

    async def _handle_casino_play(self, ws, user, req):
        uid = user["id"]
        try:
            bet = int(req.get("bet", 0))
        except (TypeError, ValueError):
            await ws.send(protocol.err("casino_play", "Mise invalide."))
            return
        if bet <= 0:
            await ws.send(protocol.err("casino_play", "La mise doit être positive."))
            return
        state = self.db.casino_state(uid)
        if bet > state["balance"]:
            await ws.send(protocol.err("casino_play", "Solde insuffisant pour cette mise."))
            return
        try:
            outcome = casino.resolve(req.get("game", ""), bet, req.get("choice"))
        except ValueError as e:
            await ws.send(protocol.err("casino_play", str(e)))
            return
        balance = self.db.apply_casino_result(uid, outcome["delta"])
        await ws.send(protocol.ok("casino_play", bet=bet, balance=balance, **outcome))

    # --- boucle par connexion ----------------------------------------------

    async def handler(self, ws):
        user = None
        try:
            async for raw in ws:
                try:
                    req = protocol.decode(raw)
                except ValueError:
                    await ws.send(protocol.err("?", "JSON invalide."))
                    continue
                op = req.get("op")
                if op in ("register", "login", "auth"):
                    user = await self.handle_auth(ws, op, req, user)
                    continue
                if user is None:
                    await ws.send(protocol.err(op or "?", "Non authentifié."))
                    continue
                await self.dispatch(ws, user, op, req)
        except websockets.WebSocketException:
            pass
        finally:
            if user is not None:
                self._remove_online(user["username"], ws)
                if not self.is_online(user["username"]):
                    await self.notify_presence(user, False)


async def _amain(host, port, db_path):
    db = DB(db_path) if db_path else DB()
    server = Server(db)
    stop = asyncio.get_running_loop().create_future()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, lambda: stop.set_result(None))
    except (NotImplementedError, RuntimeError):
        pass
    async with websockets.serve(server.handler, host, port, max_size=2 ** 20):
        print(f"papote-server à l'écoute sur ws://{host}:{port}  (db: {db.path})", flush=True)
        await stop
    print("papote-server arrêté.", flush=True)


def main():
    ap = argparse.ArgumentParser(prog="papote-server", description="Serveur de messagerie papote")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--db", default=None, help="chemin de la base SQLite")
    args = ap.parse_args()
    try:
        asyncio.run(_amain(args.host, args.port, args.db))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
