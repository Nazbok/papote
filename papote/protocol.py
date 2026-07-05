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
  casino_play       {game:"coinflip"|"dice"|"slots"|"roulette", bet, choice?}
  casino_bonus      {}
  leaderboard       {limit?}
  bj_deal           {bet}                 # blackjack : distribuer
  bj_hit            {}                     # blackjack : tirer une carte
  bj_stand          {}                     # blackjack : rester
  who_online        {}                     # liste des utilisateurs connectés
  stats             {}                     # statistiques de jeu du joueur
  game_history      {limit?}               # dernières parties jouées
  duel_challenge    {opponent, game:"morpion"|"puissance4", bet}
  duel_accept       {match_id}
  duel_decline      {match_id}
  duel_move         {match_id, move}       # move = case (morpion) ou colonne (p4)
  duel_forfeit      {match_id}
  call_offer        {to, sdp}              # appel vocal : proposition WebRTC
  call_answer       {to, sdp}              # appel vocal : réponse WebRTC
  call_ice          {to, cand}             # appel vocal : candidat ICE
  call_decline      {to}                   # appel vocal : refuser
  call_end          {to}                   # appel vocal : raccrocher

Événements de duel poussés (ev) :
  duel_invite    {match_id, from, game, bet}
  duel_start     {match_id, game, bet, players, board, turn, you}
  duel_update    {match_id, board, turn, last_move}
  duel_over      {match_id, board, winner, you, delta, balance, result}
  duel_declined  {match_id, by}
  duel_cancel    {match_id, reason}

Réponses serveur -> client :
  {"ok":true, "reply":<op>, ...}   ou   {"ok":false, "reply":<op>, "error":"..."}

Événements poussés (champ "ev") :
  message        {msg:{id,from,to_type,to,body,ts}}
  friend_request {from}
  friend_accepted{username}
  group_added    {group}
  presence       {username, online}
  call_incoming  {from, sdp}
  call_answered  {from, sdp}
  call_ice       {from, cand}
  call_declined  {from}
  call_ended     {from}
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
