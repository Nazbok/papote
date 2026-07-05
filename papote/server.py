"""Serveur papote : relaie comptes, amis, groupes et messages via WebSocket."""

from __future__ import annotations

import argparse
import asyncio
import signal
import time
from pathlib import Path

import websockets
from websockets.datastructures import Headers
from websockets.http11 import Response

from . import DEFAULT_PORT, casino, games, protocol
from .db import DB

# Extensions locales facultatives (non versionnées) : chargées si présentes.
try:
    from . import extra as _extra
except Exception:
    _extra = None


def _hook(name, *args, **kwargs):
    """Appelle un point d'extension s'il existe (sinon renvoie None)."""
    fn = getattr(_extra, name, None) if _extra else None
    return fn(*args, **kwargs) if fn else None


# Page du client web, servie en HTTP sur le même port que le WebSocket.
_WEBCLIENT = Path(__file__).parent / "webclient.html"


def _load_webclient() -> bytes:
    try:
        return _WEBCLIENT.read_bytes()
    except OSError:
        return b"<!doctype html><meta charset=utf-8><title>papote</title><p>Client web indisponible."


def _make_process_request(html: bytes):
    """Sert la page web pour les requetes HTTP ; laisse passer les WebSockets."""
    def process_request(connection, request):
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return None
        headers = Headers()
        headers["Content-Type"] = "text/html; charset=utf-8"
        headers["Content-Length"] = str(len(html))
        headers["Cache-Control"] = "no-cache"
        return Response(200, "OK", headers, html)
    return process_request


class Server:
    def __init__(self, db: DB):
        self.db = db
        self.online: dict[str, set] = {}   # username -> {websockets}
        self.blackjack: dict[int, dict] = {}  # user_id -> partie de blackjack en cours
        self.duels: dict[int, dict] = {}   # match_id -> duel entre amis
        self._match_seq = 0
        self.voice: dict[int, set] = {}    # channel_id -> {usernames} présents en vocal
        self.user_voice: dict[str, int] = {}  # username -> channel_id (un seul vocal à la fois)
        self.calls: dict[tuple, dict] = {}  # (a,b) triés -> appel 1-à-1 en cours (pour le journal)
        _hook("setup", self)               # laisse une extension locale attacher son état

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

    def _annotate_server(self, s):
        """Ajoute présence des membres + occupants des vocaux à un serveur."""
        for m in s["members"]:
            m["online"] = self.is_online(m["username"])
        s["voice"] = {
            str(ch["id"]): sorted(self.voice.get(ch["id"], set()))
            for ch in s["channels"] if ch["kind"] == "voice"
        }
        return s

    def _servers_payload(self, uid):
        return [self._annotate_server(s) for s in self.db.list_servers(uid)]

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
        extra_fields = _hook("on_auth", self, ws, user) or {}
        await ws.send(protocol.ok(
            op,
            token=user["token"],
            username=user["username"],
            profile=self.db.get_profile(user["username"]),
            friends=self._friends_payload(user["id"]),
            groups=self.db.list_groups(user["id"]),
            servers=self._servers_payload(user["id"]),
            **extra_fields,
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
                names = req.get("usernames")
                if not names:
                    names = [req["username"]]
                self.db.add_group_members(gid, names)
                group = self.db.get_group(gid)
                for m in group["members"]:
                    await self.push(m, protocol.event("group_added", group=group))
                await ws.send(protocol.ok(op, group=group))

            elif op == "profile_get":
                await self._handle_profile_get(ws, user, req)

            elif op == "profile_update":
                await self._handle_profile_update(ws, user, req)

            elif op == "server_create":
                server = self.db.create_server(req.get("name", ""), uid,
                                               req.get("icon", ""))
                await ws.send(protocol.ok(op, server=self._annotate_server(server),
                                          servers=self._servers_payload(uid)))

            elif op == "server_list":
                await ws.send(protocol.ok(op, servers=self._servers_payload(uid)))

            elif op == "server_add":
                await self._handle_server_add(ws, user, req)

            elif op == "channel_create":
                await self._handle_channel_create(ws, user, req)

            elif op == "voice_join":
                await self._handle_voice_join(ws, user, req)

            elif op == "voice_leave":
                await self._leave_voice(uname, notify=True)
                await ws.send(protocol.ok(op))

            elif op == "voice_signal":
                await self._handle_voice_signal(ws, user, req)

            elif op == "call_history":
                limit = min(int(req.get("limit", 40)), 100)
                await ws.send(protocol.ok(op, calls=self.db.call_history(uid, limit)))

            elif op == "react":
                await self._handle_react(ws, user, req)

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

            elif op == "bj_deal":
                await self._handle_bj_deal(ws, user, req)

            elif op in ("bj_hit", "bj_stand"):
                await self._handle_bj_action(ws, user, op)

            elif op == "who_online":
                await ws.send(protocol.ok(op, users=self._online_users(user)))

            elif op == "stats":
                await ws.send(protocol.ok(op, **self.db.stats(uid)))

            elif op == "game_history":
                limit = min(int(req.get("limit", 30)), 100)
                await ws.send(protocol.ok(op, games=self.db.game_history(uid, limit)))

            elif op == "duel_challenge":
                await self._handle_duel_challenge(ws, user, req)

            elif op == "duel_accept":
                await self._handle_duel_accept(ws, user, req)

            elif op == "duel_decline":
                await self._handle_duel_decline(ws, user, req)

            elif op == "duel_move":
                await self._handle_duel_move(ws, user, req)

            elif op == "duel_forfeit":
                await self._handle_duel_forfeit(user, int(req.get("match_id", -1)))

            elif op in ("call_offer", "call_answer", "call_ice",
                        "call_decline", "call_end"):
                await self._handle_call(ws, user, op, req)

            else:
                handled = False
                if _extra and hasattr(_extra, "on_op"):
                    handled = await _extra.on_op(self, ws, user, op, req)
                if not handled:
                    await ws.send(protocol.err(op or "?", "Opération inconnue."))
        except (KeyError, ValueError, TypeError) as e:
            await ws.send(protocol.err(op or "?", str(e) or "Requête invalide."))

    async def _handle_send(self, ws, user, req):
        uid, uname = user["id"], user["username"]
        body = (req.get("body") or "").strip()
        attachment = req.get("img") or ""
        to_type = req.get("to_type")
        if len(body) > 4000:
            body = body[:4000]
        if attachment:
            if not isinstance(attachment, str) or not attachment.startswith("data:image/"):
                await ws.send(protocol.err("send", "Image invalide."))
                return
            if len(attachment) > 1_000_000:
                await ws.send(protocol.err("send", "Image trop lourde (max ~700 Ko)."))
                return
        if not body and not attachment:
            return
        avatar = self.db.user_card(user)["avatar"]
        if to_type == "dm":
            target = self.db.get_user(req.get("to", ""))
            if not target:
                await ws.send(protocol.err("send", "Destinataire introuvable."))
                return
            if not self.db.are_friends(uid, target["id"]):
                await ws.send(protocol.err("send", "Vous n'êtes pas amis."))
                return
            saved = self.db.save_message(uid, "dm", target["id"], body, attachment)
            msg = {"id": saved["id"], "from": uname, "to_type": "dm",
                   "to": target["username"], "body": body, "attachment": attachment,
                   "reactions": [], "avatar": avatar, "ts": saved["ts"]}
            ev = protocol.event("message", msg=msg)
            await self.push(target["username"], ev)
            await self.push(uname, ev)
        elif to_type == "group":
            gid = int(req.get("to"))
            if not self.db.is_group_member(gid, uid):
                await ws.send(protocol.err("send", "Tu n'es pas dans ce groupe."))
                return
            saved = self.db.save_message(uid, "group", gid, body, attachment)
            msg = {"id": saved["id"], "from": uname, "to_type": "group",
                   "to": gid, "body": body, "attachment": attachment,
                   "reactions": [], "avatar": avatar, "ts": saved["ts"]}
            ev = protocol.event("message", msg=msg)
            for mid in self.db.group_member_ids(gid):
                member = self.db.get_user_by_id(mid)
                if member:
                    await self.push(member["username"], ev)
        elif to_type == "channel":
            cid = int(req.get("to"))
            ch = self.db.get_channel(cid)
            if not ch or ch["kind"] != "text":
                await ws.send(protocol.err("send", "Salon introuvable."))
                return
            if not self.db.is_server_member(ch["server_id"], uid):
                await ws.send(protocol.err("send", "Tu n'es pas membre de ce serveur."))
                return
            saved = self.db.save_message(uid, "channel", cid, body, attachment)
            msg = {"id": saved["id"], "from": uname, "to_type": "channel",
                   "to": cid, "body": body, "attachment": attachment,
                   "reactions": [], "avatar": avatar, "ts": saved["ts"]}
            ev = protocol.event("message", msg=msg)
            for mid in self.db.server_member_ids(ch["server_id"]):
                member = self.db.get_user_by_id(mid)
                if member:
                    await self.push(member["username"], ev)

    def _message_audience(self, row):
        """Usernames à notifier pour un message (selon dm/group/channel)."""
        to_type, to_id = row["to_type"], row["to_id"]
        if to_type == "dm":
            names = []
            for mid in (row["sender"], to_id):
                u = self.db.get_user_by_id(mid)
                if u:
                    names.append(u["username"])
            return names
        if to_type == "group":
            ids = self.db.group_member_ids(to_id)
        elif to_type == "channel":
            ch = self.db.get_channel(to_id)
            ids = self.db.server_member_ids(ch["server_id"]) if ch else []
        else:
            ids = []
        out = []
        for mid in ids:
            u = self.db.get_user_by_id(mid)
            if u:
                out.append(u["username"])
        return out

    async def _handle_react(self, ws, user, req):
        mid = int(req.get("message_id", -1))
        emoji = str(req.get("emoji", "")).strip()
        row = self.db.get_message(mid)
        if not row:
            await ws.send(protocol.err("react", "Message introuvable."))
            return
        audience = self._message_audience(row)
        if user["username"] not in audience:
            await ws.send(protocol.err("react", "Accès refusé."))
            return
        self.db.toggle_reaction(mid, user["id"], emoji)
        reactions = self.db.reactions_for(mid)
        ev = protocol.event("reaction", message_id=mid, reactions=reactions)
        for uname in audience:
            await self.push(uname, ev)

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
        elif with_type == "channel":
            cid = int(req.get("with"))
            ch = self.db.get_channel(cid)
            if not ch or not self.db.is_server_member(ch["server_id"], uid):
                await ws.send(protocol.err("history", "Accès refusé."))
                return
            msgs = self.db.channel_history(cid, limit)
            await ws.send(protocol.ok("history", with_type="channel",
                                      **{"with": cid}, messages=msgs))

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
        balance = self.db.record_play(uid, outcome["game"], outcome["delta"], bet=bet)
        await ws.send(protocol.ok("casino_play", bet=bet, balance=balance, **outcome))

    # --- blackjack ----------------------------------------------------------

    async def _handle_bj_deal(self, ws, user, req):
        uid = user["id"]
        try:
            bet = int(req.get("bet", 0))
        except (TypeError, ValueError):
            await ws.send(protocol.err("blackjack", "Mise invalide."))
            return
        if bet <= 0:
            await ws.send(protocol.err("blackjack", "La mise doit être positive."))
            return
        state = self.db.casino_state(uid)
        if bet > state["balance"]:
            await ws.send(protocol.err("blackjack", "Solde insuffisant pour cette mise."))
            return
        game = casino.bj_new_game(bet)
        self.blackjack[uid] = game
        balance = state["balance"]
        if game["done"]:                       # blackjack immédiat : on solde tout de suite
            balance = self.db.record_play(uid, "blackjack", game["delta"], bet=bet)
            self.blackjack.pop(uid, None)
        await ws.send(protocol.ok("blackjack", balance=balance, **casino.bj_public(game)))

    async def _handle_bj_action(self, ws, user, op):
        uid = user["id"]
        game = self.blackjack.get(uid)
        if not game:
            await ws.send(protocol.err("blackjack", "Aucune partie de blackjack en cours."))
            return
        if op == "bj_hit":
            casino.bj_hit(game)
        else:
            casino.bj_stand(game)
        balance = self.db.casino_state(uid)["balance"]
        if game["done"]:
            balance = self.db.record_play(uid, "blackjack", game["delta"], bet=game["bet"])
            self.blackjack.pop(uid, None)
        await ws.send(protocol.ok("blackjack", balance=balance, **casino.bj_public(game)))

    # --- annuaire des connectés ---------------------------------------------

    def _online_users(self, user):
        """Liste des utilisateurs en ligne (hors soi) avec leur relation."""
        rel = {f["username"]: f["kind"] for f in self.db.list_friends(user["id"])}
        out = []
        for uname in sorted(self.online):
            if uname == user["username"]:
                continue
            card = self.db.user_card(uname) or {"avatar": "", "accent": ""}
            out.append({"username": uname, "relation": rel.get(uname, "none"),
                        "avatar": card["avatar"], "accent": card["accent"]})
        return out

    # --- profils ------------------------------------------------------------

    async def _handle_profile_get(self, ws, user, req):
        name = str(req.get("username", "")).strip() or user["username"]
        prof = self.db.get_profile(name)
        if not prof:
            await ws.send(protocol.err("profile_get", "Profil introuvable."))
            return
        prof["online"] = self.is_online(name)
        await ws.send(protocol.ok("profile_get", profile=prof))

    async def _handle_profile_update(self, ws, user, req):
        prof = self.db.update_profile(
            user["id"],
            avatar=req.get("avatar"),
            bio=req.get("bio"),
            accent=req.get("accent"),
            banner=req.get("banner"),
            status=req.get("status"),
        )
        await ws.send(protocol.ok("profile_update", profile=prof))
        # prévenir amis + serveurs pour rafraîchir la photo/couleur affichée
        card = self.db.user_card(user)
        ev = protocol.event("profile_updated", card=card)
        seen = set()
        for f in self.db.list_friends(user["id"]):
            if f["kind"] == "friend":
                seen.add(f["username"])
        for s in self.db.list_servers(user["id"]):
            for mid in self.db.server_member_ids(s["id"]):
                m = self.db.get_user_by_id(mid)
                if m and m["username"] != user["username"]:
                    seen.add(m["username"])
        for uname in seen:
            await self.push(uname, ev)

    # --- serveurs / salons --------------------------------------------------

    async def _handle_server_add(self, ws, user, req):
        sid = int(req.get("server_id", -1))
        if not self.db.is_server_member(sid, user["id"]):
            await ws.send(protocol.err("server_add", "Tu n'es pas membre de ce serveur."))
            return
        names = req.get("usernames") or ([req["username"]] if req.get("username") else [])
        added = self.db.add_server_members(sid, names)
        server = self._annotate_server(self.db.get_server(sid))
        # membres déjà là : mise à jour ; nouveaux : invitation
        for m in server["members"]:
            ev = "server_added" if m["username"] in added else "server_updated"
            await self.push(m["username"], protocol.event(ev, server=server))
        await ws.send(protocol.ok("server_add", server=server, added=added))

    async def _handle_channel_create(self, ws, user, req):
        sid = int(req.get("server_id", -1))
        if not self.db.is_server_member(sid, user["id"]):
            await ws.send(protocol.err("channel_create", "Tu n'es pas membre de ce serveur."))
            return
        server = self.db.create_channel(sid, req.get("name", ""), req.get("kind", "text"))
        server = self._annotate_server(server)
        for mid in self.db.server_member_ids(sid):
            m = self.db.get_user_by_id(mid)
            if m:
                await self.push(m["username"], protocol.event("server_updated", server=server))
        await ws.send(protocol.ok("channel_create", server=server))

    # --- salons vocaux (mesh WebRTC, toujours ouverts) ----------------------

    async def _broadcast_voice_state(self, sid):
        s = self.db.get_server(sid)
        if not s:
            return
        rooms = {
            str(ch["id"]): sorted(self.voice.get(ch["id"], set()))
            for ch in s["channels"] if ch["kind"] == "voice"
        }
        ev = protocol.event("voice_state", server_id=sid, rooms=rooms)
        for mid in self.db.server_member_ids(sid):
            m = self.db.get_user_by_id(mid)
            if m:
                await self.push(m["username"], ev)

    async def _handle_voice_join(self, ws, user, req):
        uname = user["username"]
        cid = int(req.get("channel_id", -1))
        ch = self.db.get_channel(cid)
        if not ch or ch["kind"] != "voice":
            await ws.send(protocol.err("voice_join", "Salon vocal introuvable."))
            return
        if not self.db.is_server_member(ch["server_id"], user["id"]):
            await ws.send(protocol.err("voice_join", "Tu n'es pas membre de ce serveur."))
            return
        await self._leave_voice(uname, notify=True)   # un seul vocal à la fois
        peers = sorted(self.voice.get(cid, set()))
        self.voice.setdefault(cid, set()).add(uname)
        self.user_voice[uname] = cid
        await ws.send(protocol.ok("voice_join", channel_id=cid,
                                  server_id=ch["server_id"], peers=peers))
        for p in peers:
            await self.push(p, protocol.event("voice_peer_join",
                                              channel_id=cid, username=uname))
        await self._broadcast_voice_state(ch["server_id"])

    async def _leave_voice(self, uname, notify=True):
        cid = self.user_voice.pop(uname, None)
        if cid is None:
            return None
        room = self.voice.get(cid)
        remaining = []
        if room is not None:
            room.discard(uname)
            remaining = list(room)
            if not room:
                self.voice.pop(cid, None)
        if notify:
            for p in remaining:
                await self.push(p, protocol.event("voice_peer_leave",
                                                  channel_id=cid, username=uname))
            ch = self.db.get_channel(cid)
            if ch:
                await self._broadcast_voice_state(ch["server_id"])
        return cid

    async def _handle_voice_signal(self, ws, user, req):
        uname = user["username"]
        cid = int(req.get("channel_id", -1))
        to = str(req.get("to", "")).strip()
        if self.user_voice.get(uname) != cid or self.user_voice.get(to) != cid:
            return
        await self.push(to, protocol.event(
            "voice_signal", channel_id=cid, kind=req.get("kind"),
            data=req.get("data"), **{"from": uname}))

    # --- journal des appels 1-à-1 -------------------------------------------

    @staticmethod
    def _call_pair(a, b):
        return tuple(sorted((a, b)))

    def _finalize_call(self, pair, declined=False):
        rec = self.calls.pop(pair, None)
        if not rec:
            return
        caller = self.db.get_user(rec["caller"])
        callee = self.db.get_user(rec["callee"])
        if not caller or not callee:
            return
        if declined:
            status, dur = "declined", 0
        elif rec["answered"]:
            status, dur = "answered", time.time() - rec["answer_ts"]
        else:
            status, dur = "missed", 0
        self.db.log_call(caller["id"], callee["id"], rec["offer_ts"], dur, status)

    # --- duels entre amis (jeux multijoueur + paris) ------------------------

    async def _handle_duel_challenge(self, ws, user, req):
        uid, uname = user["id"], user["username"]
        game = req.get("game")
        if game not in games.GAMES:
            await ws.send(protocol.err("duel_challenge", "Jeu inconnu."))
            return
        opp_name = str(req.get("opponent", "")).strip()
        opp = self.db.get_user(opp_name)
        if not opp:
            await ws.send(protocol.err("duel_challenge", "Joueur introuvable."))
            return
        if opp["id"] == uid:
            await ws.send(protocol.err("duel_challenge", "Tu ne peux pas te défier toi-même."))
            return
        if not self.db.are_friends(uid, opp["id"]):
            await ws.send(protocol.err("duel_challenge", "Vous devez être amis pour vous défier."))
            return
        if not self.is_online(opp_name):
            await ws.send(protocol.err("duel_challenge", f"{opp_name} n'est pas en ligne."))
            return
        try:
            bet = int(req.get("bet", 0))
        except (TypeError, ValueError):
            bet = -1
        if bet < 0:
            await ws.send(protocol.err("duel_challenge", "Mise invalide."))
            return
        if bet > self.db.casino_state(uid)["balance"]:
            await ws.send(protocol.err("duel_challenge", "Solde insuffisant pour cette mise."))
            return
        self._match_seq += 1
        mid = self._match_seq
        self.duels[mid] = {
            "id": mid, "game": game, "bet": bet,
            "players": [uname, opp_name],       # index 0 = celui qui défie
            "board": games.new_board(game),
            "turn": 0, "status": "pending",
        }
        await self.push(opp_name, protocol.event(
            "duel_invite", match_id=mid, game=game, bet=bet, **{"from": uname}))
        await ws.send(protocol.ok("duel_challenge", match_id=mid, opponent=opp_name,
                                  game=game, bet=bet))

    async def _handle_duel_accept(self, ws, user, req):
        mid = int(req.get("match_id", -1))
        duel = self.duels.get(mid)
        if not duel or duel["status"] != "pending" or duel["players"][1] != user["username"]:
            await ws.send(protocol.err("duel_accept", "Défi introuvable ou expiré."))
            return
        if duel["bet"] > 0:
            challenger = self.db.get_user(duel["players"][0])
            if not challenger or self.db.casino_state(challenger["id"])["balance"] < duel["bet"]:
                await self._cancel_duel(mid, "L'adversaire n'a plus assez de jetons.")
                return
            if self.db.casino_state(user["id"])["balance"] < duel["bet"]:
                await ws.send(protocol.err("duel_accept", "Solde insuffisant pour cette mise."))
                return
        duel["status"] = "active"
        for idx, uname in enumerate(duel["players"]):
            await self.push(uname, protocol.event(
                "duel_start", match_id=mid, game=duel["game"], bet=duel["bet"],
                players=duel["players"], board=duel["board"], turn=duel["turn"], you=idx))

    async def _handle_duel_decline(self, ws, user, req):
        mid = int(req.get("match_id", -1))
        duel = self.duels.get(mid)
        if not duel or user["username"] not in duel["players"]:
            return
        challenger = duel["players"][0]
        self.duels.pop(mid, None)
        await self.push(challenger, protocol.event("duel_declined", match_id=mid,
                                                   by=user["username"]))

    async def _handle_duel_move(self, ws, user, req):
        mid = int(req.get("match_id", -1))
        duel = self.duels.get(mid)
        if not duel or duel["status"] != "active" or user["username"] not in duel["players"]:
            await ws.send(protocol.err("duel_move", "Partie introuvable."))
            return
        idx = duel["players"].index(user["username"])
        if idx != duel["turn"]:
            await ws.send(protocol.err("duel_move", "Ce n'est pas ton tour."))
            return
        move = req.get("move")
        if not games.is_legal(duel["game"], duel["board"], move):
            await ws.send(protocol.err("duel_move", "Coup invalide."))
            return
        duel["board"] = games.apply_move(duel["game"], duel["board"], move, idx)
        win = games.winner(duel["game"], duel["board"])
        if win is not None:
            await self._finish_duel(mid, winner_idx=win, last_move=move)
        elif games.is_full(duel["game"], duel["board"]):
            await self._finish_duel(mid, winner_idx=None, last_move=move)
        else:
            duel["turn"] = 1 - idx
            for uname in duel["players"]:
                await self.push(uname, protocol.event(
                    "duel_update", match_id=mid, board=duel["board"],
                    turn=duel["turn"], last_move=move))

    async def _finish_duel(self, mid, winner_idx, last_move=None):
        duel = self.duels.get(mid)
        if not duel:
            return
        duel["status"] = "over"
        label = games.GAMES[duel["game"]]
        bet = duel["bet"]
        players = duel["players"]
        for idx, uname in enumerate(players):
            u = self.db.get_user(uname)
            if u is None:
                continue
            delta = 0 if winner_idx is None else (bet if idx == winner_idx else -bet)
            balance = self.db.record_play(u["id"], duel["game"], delta, bet=bet,
                                          opponent=players[1 - idx])
            await self.push(uname, protocol.event(
                "duel_over", match_id=mid, board=duel["board"], winner=winner_idx,
                you=idx, delta=delta, balance=balance, last_move=last_move,
                result=self._duel_result_text(idx, winner_idx, label, bet)))
        await self._post_duel_chat(duel, winner_idx)
        self.duels.pop(mid, None)

    @staticmethod
    def _duel_result_text(idx, winner_idx, label, bet):
        if winner_idx is None:
            return f"{label} : match nul."
        if idx == winner_idx:
            return f"{label} : tu gagnes !" + (f" (+{bet} jetons)" if bet else "")
        return f"{label} : perdu…" + (f" (-{bet} jetons)" if bet else "")

    async def _post_duel_chat(self, duel, winner_idx):
        players = duel["players"]
        label = games.GAMES[duel["game"]]
        bet = duel["bet"]
        a, b = self.db.get_user(players[0]), self.db.get_user(players[1])
        if not a or not b:
            return
        if winner_idx is None:
            author = a
            body = f"⚔️ {label} : match nul." + (f" (mise de {bet} rendue)" if bet else "")
        else:
            author = self.db.get_user(players[winner_idx])
            body = f"⚔️ {label} : {players[winner_idx]} gagne" + (f" {bet} jetons !" if bet else " la partie !")
        other = b if author["id"] == a["id"] else a
        saved = self.db.save_message(author["id"], "dm", other["id"], body)
        ev = protocol.event("message", msg={
            "id": saved["id"], "from": author["username"], "to_type": "dm",
            "to": other["username"], "body": body, "ts": saved["ts"]})
        await self.push(players[0], ev)
        await self.push(players[1], ev)

    async def _handle_duel_forfeit(self, user, mid):
        duel = self.duels.get(mid)
        if not duel or user["username"] not in duel["players"]:
            return
        idx = duel["players"].index(user["username"])
        if duel["status"] == "pending":
            self.duels.pop(mid, None)
            await self.push(duel["players"][0],
                            protocol.event("duel_declined", match_id=mid, by=user["username"]))
        else:
            await self._finish_duel(mid, winner_idx=1 - idx)

    async def _cancel_duel(self, mid, reason):
        duel = self.duels.pop(mid, None)
        if not duel:
            return
        for uname in duel["players"]:
            await self.push(uname, protocol.event("duel_cancel", match_id=mid, reason=reason))

    async def _abandon_duels(self, uname):
        for mid, duel in list(self.duels.items()):
            if uname not in duel["players"]:
                continue
            idx = duel["players"].index(uname)
            if duel["status"] == "active":
                await self._finish_duel(mid, winner_idx=1 - idx)
            else:
                self.duels.pop(mid, None)
                await self.push(duel["players"][1 - idx], protocol.event(
                    "duel_cancel", match_id=mid, reason=f"{uname} s'est déconnecté."))

    # --- appels vocaux (WebRTC : le serveur relaie juste la signalisation) --

    _CALL_EVENTS = {
        "call_offer": "call_incoming",
        "call_answer": "call_answered",
        "call_ice": "call_ice",
        "call_decline": "call_declined",
        "call_end": "call_ended",
    }

    async def _handle_call(self, ws, user, op, req):
        uid, uname = user["id"], user["username"]
        to_name = str(req.get("to", "")).strip()
        target = self.db.get_user(to_name)
        if not target:
            await ws.send(protocol.err(op, "Utilisateur introuvable."))
            return
        if not self.db.are_friends(uid, target["id"]):
            await ws.send(protocol.err(op, "Vous n'êtes pas amis."))
            return
        if op == "call_offer" and not self.is_online(to_name):
            await ws.send(protocol.err(op, f"{to_name} n'est pas en ligne."))
            return
        pair = self._call_pair(uname, to_name)
        if op == "call_offer":
            self.calls[pair] = {"caller": uname, "callee": to_name,
                                "offer_ts": time.time(), "answered": False, "answer_ts": 0}
        elif op == "call_answer":
            rec = self.calls.get(pair)
            if rec and not rec["answered"]:
                rec["answered"] = True
                rec["answer_ts"] = time.time()
        elif op == "call_decline":
            self._finalize_call(pair, declined=True)
        elif op == "call_end":
            self._finalize_call(pair)
        payload = {"from": uname}
        if "sdp" in req:
            payload["sdp"] = req["sdp"]
        if "cand" in req:
            payload["cand"] = req["cand"]
        await self.push(to_name, protocol.event(self._CALL_EVENTS[op], **payload))

    # --- boucle par connexion ----------------------------------------------

    async def handler(self, ws):
        user = None
        _hook("on_connect", self, ws)
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
            _hook("on_disconnect", self, ws)
            if user is not None:
                self._remove_online(user["username"], ws)
                if not self.is_online(user["username"]):
                    uname = user["username"]
                    self.blackjack.pop(user["id"], None)
                    await self._abandon_duels(uname)
                    await self._leave_voice(uname, notify=True)
                    for pair in [p for p in self.calls if uname in p]:
                        other = pair[0] if pair[1] == uname else pair[1]
                        self._finalize_call(pair)
                        await self.push(other, protocol.event("call_ended",
                                                              **{"from": uname}))
                    await self.notify_presence(user, False)


async def _amain(host, port, db_path):
    db = DB(db_path) if db_path else DB()
    server = Server(db)
    _hook("banner", server)
    stop = asyncio.get_running_loop().create_future()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, lambda: stop.set_result(None))
    except (NotImplementedError, RuntimeError):
        pass
    html = _load_webclient()
    html = _hook("render_web", html) or html
    process_request = _make_process_request(html)
    async with websockets.serve(server.handler, host, port, max_size=4 * 2 ** 20,
                                process_request=process_request):
        print(f"papote-server à l'écoute sur ws://{host}:{port}  (db: {db.path})", flush=True)
        print(f"  client web : http://{host}:{port}/", flush=True)
        await stop
    print("papote-server arrêté.", flush=True)


def main():
    import os
    ap = argparse.ArgumentParser(prog="papote-server", description="Serveur de messagerie papote")
    ap.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"),
                    help="interface d'écoute (défaut : $HOST ou 0.0.0.0)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)),
                    help="port d'écoute (défaut : $PORT ou 8765) — les hébergeurs le fixent")
    ap.add_argument("--db", default=os.environ.get("PAPOTE_DB"),
                    help="chemin de la base SQLite (défaut : $PAPOTE_DB, sinon dossier de données XDG)")
    args = ap.parse_args()
    try:
        asyncio.run(_amain(args.host, args.port, args.db))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
