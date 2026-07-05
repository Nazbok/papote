"""Serveur papote : relaie comptes, amis, groupes et messages via WebSocket."""

from __future__ import annotations

import argparse
import asyncio
import signal
from pathlib import Path

import websockets
from websockets.datastructures import Headers
from websockets.http11 import Response

from . import DEFAULT_PORT, casino, games, protocol
from .db import DB

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
        return [
            {"username": uname, "relation": rel.get(uname, "none")}
            for uname in sorted(self.online)
            if uname != user["username"]
        ]

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
                    self.blackjack.pop(user["id"], None)
                    await self._abandon_duels(user["username"])
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
    process_request = _make_process_request(_load_webclient())
    async with websockets.serve(server.handler, host, port, max_size=2 ** 20,
                                process_request=process_request):
        print(f"papote-server à l'écoute sur ws://{host}:{port}  (db: {db.path})", flush=True)
        print(f"  client web : http://{host}:{port}/", flush=True)
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
