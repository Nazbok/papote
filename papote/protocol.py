"""Protocole papote : messages JSON échangés sur la connexion WebSocket.

Chaque message WebSocket est un objet JSON.

Requêtes client -> serveur (champ "op") :
  register / login  {username, password}
  auth              {token}
  friend_add        {username}
  friend_accept     {username}
  friend_list       {}
  group_create      {name, members:[username,...]}
  group_list        {}
  group_add         {group_id, username}
  send              {to_type:"dm"|"group", to:username|group_id, body}
  history           {with_type, with, limit?}
  casino_state      {}
  casino_play       {game:"coinflip"|"dice"|"slots", bet, choice?}
  casino_bonus      {}
  leaderboard       {limit?}

Réponses serveur -> client :
  {"ok":true, "reply":<op>, ...}   ou   {"ok":false, "reply":<op>, "error":"..."}

Événements poussés (champ "ev") :
  message        {msg:{id,from,to_type,to,body,ts}}
  friend_request {from}
  friend_accepted{username}
  group_added    {group}
  presence       {username, online}
"""

from __future__ import annotations

import json


def encode(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def decode(raw: str):
    return json.loads(raw)


def ok(op: str, **kw) -> str:
    return encode({"ok": True, "reply": op, **kw})


def err(op: str, message: str) -> str:
    return encode({"ok": False, "reply": op, "error": message})


def event(ev: str, **kw) -> str:
    return encode({"ev": ev, **kw})
