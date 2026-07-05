"""Stockage SQLite du serveur papote : comptes, amis, groupes, messages."""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from pathlib import Path

DEFAULT_DB = (
    Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    / "papote"
    / "server.db"
)

_PBKDF_ROUNDS = 120_000

# Casino
STARTING_BALANCE = 1000      # jetons offerts à l'inscription
BONUS_AMOUNT = 100           # jetons du bonus anti-faillite
BONUS_THRESHOLD = 100        # bonus réclamable seulement en dessous de ce solde


def _hash_pw(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF_ROUNDS)


class DB:
    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        self.con.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                salt BLOB NOT NULL,
                pwd BLOB NOT NULL,
                token TEXT,
                created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS friendships(
                requester INTEGER NOT NULL,
                addressee INTEGER NOT NULL,
                status TEXT NOT NULL,          -- 'pending' | 'accepted'
                PRIMARY KEY (requester, addressee)
            );
            CREATE TABLE IF NOT EXISTS groups(
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                owner INTEGER NOT NULL,
                created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS group_members(
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (group_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY,
                sender INTEGER NOT NULL,
                to_type TEXT NOT NULL,         -- 'dm' | 'group'
                to_id INTEGER NOT NULL,
                body TEXT NOT NULL,
                ts REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS game_log(
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,            -- coinflip/dice/slots/roulette/blackjack/morpion/puissance4
                opponent TEXT,                -- adversaire (duels) ou NULL (casino)
                bet INTEGER NOT NULL,
                delta INTEGER NOT NULL,
                ts REAL NOT NULL
            );
            """
        )
        self._migrate_casino()
        self.con.commit()

    def _migrate_casino(self):
        """Ajoute les colonnes casino à la table users si elles manquent."""
        cols = {r["name"] for r in self.con.execute("PRAGMA table_info(users)")}
        additions = [
            ("balance", f"INTEGER NOT NULL DEFAULT {STARTING_BALANCE}"),
            ("biggest_win", "INTEGER NOT NULL DEFAULT 0"),
            ("games_played", "INTEGER NOT NULL DEFAULT 0"),
        ]
        for name, ddl in additions:
            if name not in cols:
                self.con.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")

    # --- utilisateurs -------------------------------------------------------

    def create_user(self, username: str, password: str) -> sqlite3.Row:
        username = username.strip()
        if not username or len(username) > 32 or " " in username:
            raise ValueError("Nom d'utilisateur invalide (pas d'espace, ≤ 32).")
        if len(password) < 3:
            raise ValueError("Mot de passe trop court.")
        if self.get_user(username):
            raise ValueError("Ce nom d'utilisateur est déjà pris.")
        salt = secrets.token_bytes(16)
        pwd = _hash_pw(password, salt)
        token = secrets.token_hex(32)
        self.con.execute(
            "INSERT INTO users(username, salt, pwd, token, created) VALUES(?,?,?,?,?)",
            (username, salt, pwd, token, time.time()),
        )
        self.con.commit()
        return self.get_user(username)

    def verify_user(self, username: str, password: str):
        row = self.get_user(username)
        if not row:
            return None
        if secrets.compare_digest(_hash_pw(password, row["salt"]), row["pwd"]):
            token = secrets.token_hex(32)
            self.con.execute("UPDATE users SET token=? WHERE id=?", (token, row["id"]))
            self.con.commit()
            return self.get_user(username)
        return None

    def get_user(self, username: str):
        return self.con.execute(
            "SELECT * FROM users WHERE username=?", (username,)
        ).fetchone()

    def get_user_by_id(self, uid: int):
        return self.con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

    def get_user_by_token(self, token: str):
        if not token:
            return None
        return self.con.execute(
            "SELECT * FROM users WHERE token=?", (token,)
        ).fetchone()

    # --- amis ---------------------------------------------------------------

    def friend_request(self, from_id: int, to_username: str) -> str:
        target = self.get_user(to_username)
        if not target:
            raise ValueError("Utilisateur introuvable.")
        if target["id"] == from_id:
            raise ValueError("Tu ne peux pas t'ajouter toi-même.")
        tid = target["id"]
        # déjà amis ?
        if self._friendship(from_id, tid) == "accepted":
            raise ValueError("Vous êtes déjà amis.")
        # demande inverse en attente -> on accepte directement
        rev = self.con.execute(
            "SELECT status FROM friendships WHERE requester=? AND addressee=?",
            (tid, from_id),
        ).fetchone()
        if rev and rev["status"] == "pending":
            self.friend_accept(from_id, to_username)
            return "accepted"
        # demande déjà envoyée ?
        existing = self.con.execute(
            "SELECT status FROM friendships WHERE requester=? AND addressee=?",
            (from_id, tid),
        ).fetchone()
        if existing:
            raise ValueError("Demande déjà envoyée.")
        self.con.execute(
            "INSERT INTO friendships(requester, addressee, status) VALUES(?,?,'pending')",
            (from_id, tid),
        )
        self.con.commit()
        return "pending"

    def friend_accept(self, user_id: int, other_username: str) -> None:
        other = self.get_user(other_username)
        if not other:
            raise ValueError("Utilisateur introuvable.")
        oid = other["id"]
        row = self.con.execute(
            "SELECT * FROM friendships WHERE requester=? AND addressee=? AND status='pending'",
            (oid, user_id),
        ).fetchone()
        if not row:
            raise ValueError("Aucune demande en attente de cette personne.")
        self.con.execute(
            "UPDATE friendships SET status='accepted' WHERE requester=? AND addressee=?",
            (oid, user_id),
        )
        self.con.commit()

    def _friendship(self, a: int, b: int):
        row = self.con.execute(
            """SELECT status FROM friendships
               WHERE (requester=? AND addressee=?) OR (requester=? AND addressee=?)""",
            (a, b, b, a),
        ).fetchone()
        return row["status"] if row else None

    def are_friends(self, a: int, b: int) -> bool:
        return self._friendship(a, b) == "accepted"

    def list_friends(self, user_id: int):
        """Renvoie amis acceptés + demandes entrantes/sortantes en attente."""
        out = []
        rows = self.con.execute(
            """SELECT requester, addressee, status FROM friendships
               WHERE requester=? OR addressee=?""",
            (user_id, user_id),
        ).fetchall()
        for r in rows:
            other_id = r["addressee"] if r["requester"] == user_id else r["requester"]
            other = self.get_user_by_id(other_id)
            if not other:
                continue
            if r["status"] == "accepted":
                kind = "friend"
            elif r["requester"] == user_id:
                kind = "outgoing"
            else:
                kind = "incoming"
            out.append({"username": other["username"], "kind": kind})
        return out

    # --- groupes ------------------------------------------------------------

    def create_group(self, name: str, owner_id: int, members):
        name = name.strip()
        if not name or len(name) > 40:
            raise ValueError("Nom de groupe invalide.")
        cur = self.con.execute(
            "INSERT INTO groups(name, owner, created) VALUES(?,?,?)",
            (name, owner_id, time.time()),
        )
        gid = cur.lastrowid
        member_ids = {owner_id}
        for uname in members:
            u = self.get_user(uname.strip())
            if u:
                member_ids.add(u["id"])
        for mid in member_ids:
            self.con.execute(
                "INSERT OR IGNORE INTO group_members(group_id, user_id) VALUES(?,?)",
                (gid, mid),
            )
        self.con.commit()
        return self.get_group(gid)

    def get_group(self, gid: int):
        g = self.con.execute("SELECT * FROM groups WHERE id=?", (gid,)).fetchone()
        if not g:
            return None
        members = [
            self.get_user_by_id(r["user_id"])["username"]
            for r in self.con.execute(
                "SELECT user_id FROM group_members WHERE group_id=?", (gid,)
            ).fetchall()
        ]
        return {"id": g["id"], "name": g["name"], "owner": g["owner"], "members": members}

    def is_group_member(self, gid: int, user_id: int) -> bool:
        return (
            self.con.execute(
                "SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",
                (gid, user_id),
            ).fetchone()
            is not None
        )

    def group_member_ids(self, gid: int):
        return [
            r["user_id"]
            for r in self.con.execute(
                "SELECT user_id FROM group_members WHERE group_id=?", (gid,)
            ).fetchall()
        ]

    def add_group_member(self, gid: int, username: str):
        u = self.get_user(username)
        if not u:
            raise ValueError("Utilisateur introuvable.")
        self.con.execute(
            "INSERT OR IGNORE INTO group_members(group_id, user_id) VALUES(?,?)",
            (gid, u["id"]),
        )
        self.con.commit()
        return u["id"]

    def list_groups(self, user_id: int):
        rows = self.con.execute(
            """SELECT g.id FROM groups g
               JOIN group_members m ON m.group_id=g.id
               WHERE m.user_id=? ORDER BY g.created""",
            (user_id,),
        ).fetchall()
        return [self.get_group(r["id"]) for r in rows]

    # --- messages -----------------------------------------------------------

    def save_message(self, sender_id: int, to_type: str, to_id: int, body: str):
        ts = time.time()
        cur = self.con.execute(
            "INSERT INTO messages(sender, to_type, to_id, body, ts) VALUES(?,?,?,?,?)",
            (sender_id, to_type, to_id, body, ts),
        )
        self.con.commit()
        sender = self.get_user_by_id(sender_id)["username"]
        return {
            "id": cur.lastrowid,
            "from": sender,
            "to_type": to_type,
            "to_id": to_id,
            "body": body,
            "ts": ts,
        }

    def dm_history(self, user_id: int, other_id: int, limit: int = 100):
        rows = self.con.execute(
            """SELECT * FROM messages WHERE to_type='dm'
               AND ((sender=? AND to_id=?) OR (sender=? AND to_id=?))
               ORDER BY ts DESC LIMIT ?""",
            (user_id, other_id, other_id, user_id, limit),
        ).fetchall()
        return [self._msg_row(r) for r in reversed(rows)]

    def group_history(self, gid: int, limit: int = 100):
        rows = self.con.execute(
            "SELECT * FROM messages WHERE to_type='group' AND to_id=? ORDER BY ts DESC LIMIT ?",
            (gid, limit),
        ).fetchall()
        return [self._msg_row(r) for r in reversed(rows)]

    def _msg_row(self, r):
        return {
            "id": r["id"],
            "from": self.get_user_by_id(r["sender"])["username"],
            "to_type": r["to_type"],
            "to_id": r["to_id"],
            "body": r["body"],
            "ts": r["ts"],
        }

    # --- casino -------------------------------------------------------------

    def casino_state(self, uid: int) -> dict:
        r = self.get_user_by_id(uid)
        return {
            "balance": r["balance"],
            "biggest_win": r["biggest_win"],
            "games_played": r["games_played"],
        }

    def record_play(self, uid: int, kind: str, delta: int, bet: int = 0,
                    opponent: str | None = None) -> int:
        """Applique le résultat d'une partie, la journalise, renvoie le nouveau solde.

        Le solde ne descend jamais sous zéro. Met à jour le record de gain, le
        compteur de parties et ajoute une ligne dans game_log, le tout de façon
        atomique. Sert au casino comme aux duels entre amis.
        """
        r = self.get_user_by_id(uid)
        new_balance = max(0, r["balance"] + delta)
        biggest = max(r["biggest_win"], delta)
        self.con.execute(
            "UPDATE users SET balance=?, biggest_win=?, games_played=games_played+1 WHERE id=?",
            (new_balance, biggest, uid),
        )
        self.con.execute(
            "INSERT INTO game_log(user_id, kind, opponent, bet, delta, ts) VALUES(?,?,?,?,?,?)",
            (uid, kind, opponent, bet, delta, time.time()),
        )
        self.con.commit()
        return new_balance

    # rétrocompatibilité : ancien nom
    def apply_casino_result(self, uid: int, delta: int) -> int:
        return self.record_play(uid, "casino", delta)

    def claim_bonus(self, uid: int) -> int:
        """Crédite le bonus anti-faillite ; refuse si le solde est trop élevé."""
        r = self.get_user_by_id(uid)
        if r["balance"] >= BONUS_THRESHOLD:
            raise ValueError(
                f"Bonus réservé aux fauchés (moins de {BONUS_THRESHOLD} jetons)."
            )
        new_balance = r["balance"] + BONUS_AMOUNT
        self.con.execute("UPDATE users SET balance=? WHERE id=?", (new_balance, uid))
        self.con.commit()
        return new_balance

    def leaderboard(self, limit: int = 20):
        rows = self.con.execute(
            """SELECT username, balance, biggest_win, games_played
               FROM users ORDER BY balance DESC, biggest_win DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {
                "username": r["username"],
                "balance": r["balance"],
                "biggest_win": r["biggest_win"],
                "games_played": r["games_played"],
            }
            for r in rows
        ]

    # --- statistiques & historique -----------------------------------------

    def stats(self, uid: int) -> dict:
        u = self.get_user_by_id(uid)
        agg = self.con.execute(
            """SELECT COUNT(*) n,
                      COALESCE(SUM(CASE WHEN delta > 0 THEN 1 ELSE 0 END), 0) wins,
                      COALESCE(SUM(CASE WHEN delta < 0 THEN 1 ELSE 0 END), 0) losses,
                      COALESCE(SUM(CASE WHEN delta = 0 THEN 1 ELSE 0 END), 0) draws,
                      COALESCE(SUM(delta), 0) net,
                      COALESCE(SUM(bet), 0) wagered,
                      COALESCE(MAX(delta), 0) best
               FROM game_log WHERE user_id=?""",
            (uid,),
        ).fetchone()
        per_game = [
            {"kind": r["kind"], "games": r["n"], "net": r["net"], "wins": r["wins"]}
            for r in self.con.execute(
                """SELECT kind, COUNT(*) n, COALESCE(SUM(delta),0) net,
                          COALESCE(SUM(CASE WHEN delta>0 THEN 1 ELSE 0 END),0) wins
                   FROM game_log WHERE user_id=? GROUP BY kind ORDER BY n DESC""",
                (uid,),
            ).fetchall()
        ]
        return {
            "balance": u["balance"],
            "games": agg["n"],
            "wins": agg["wins"],
            "losses": agg["losses"],
            "draws": agg["draws"],
            "net": agg["net"],
            "wagered": agg["wagered"],
            "biggest_win": max(u["biggest_win"], agg["best"]),
            "per_game": per_game,
        }

    def game_history(self, uid: int, limit: int = 30):
        rows = self.con.execute(
            """SELECT kind, opponent, bet, delta, ts FROM game_log
               WHERE user_id=? ORDER BY ts DESC LIMIT ?""",
            (uid, limit),
        ).fetchall()
        return [
            {"kind": r["kind"], "opponent": r["opponent"], "bet": r["bet"],
             "delta": r["delta"], "ts": r["ts"]}
            for r in rows
        ]
