# 💬 papote

Une messagerie pour le **terminal** : un serveur, un client avec une interface
[Textual](https://textual.textualize.io/), et tout passe par WebSocket.

Messages privés, groupes, demandes d'ami, présence en ligne, historique…
et un **casino** avec de la monnaie virtuelle et un classement 🎰.

## 🎰 Casino

Chaque compte démarre avec **1000 jetons**. Ouvre le casino depuis la barre
latérale (bouton **🎰 Casino** ou `Ctrl+J`) :

| Jeu | Comment | Gain |
|-----|---------|------|
| 🪙 Pile ou Face | choisis pile ou face | ×2 |
| 🎲 Dés | devine le résultat (1-6) | ×6 |
| 🎰 Machine à sous | trois rouleaux | jusqu'à ×30 (💎💎💎) |
| 🎡 Roulette | numéro, rouge/noir, pair/impair, bas/haut, douzaine | ×2 à ×35 |
| 🃏 Blackjack | tire ou reste, bats le croupier | ×2 (blackjack ×2,5) |

Le **classement** (bouton 🏆 ou `Ctrl+P`) montre les plus gros portefeuilles de
tous les joueurs. À court de jetons ? Le bouton **🎁 Bonus** te redonne 100
jetons quand tu es fauché. Tout le hasard et les soldes sont gérés côté serveur.

## 🌐 Qui est en ligne

Le bouton **🌐 En ligne** (ou `Ctrl+O`) ouvre l'annuaire des utilisateurs
connectés : clique sur quelqu'un pour lui envoyer une demande d'ami (ou accepter
la sienne) sans avoir à taper son nom.

## ⚔️ Duels entre amis (jeux multijoueur + paris)

Ouvre une conversation privée avec un ami en ligne et clique **⚔️ Défier** :
choisis le jeu et une mise en jetons (0 = pour l'honneur). Ton ami reçoit
l'invitation, l'accepte, et vous jouez en temps réel :

- **Morpion** — le premier à aligner trois symboles.
- **Puissance 4** — aligne quatre jetons (horizontal, vertical ou diagonale).

Le gagnant rafle la mise, et le résultat est posté dans votre conversation.
Tout se passe côté serveur : impossible de tricher sur les coups ou l'argent.

## 📊 Statistiques

Le bouton **📊** (ou `Ctrl+T`) ouvre tes stats : parties jouées, victoires /
défaites, taux de réussite, gain net, plus gros gain, détail par jeu et
historique de tes dernières parties (casino comme duels). Ton solde est toujours
visible dans la barre du haut.

## Installation

```bash
git clone https://github.com/Nazbok/papote
cd papote
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Lancer le serveur

```bash
python -m papote.server --host 0.0.0.0 --port 8765
```

La base SQLite est créée automatiquement (`--db chemin.db` pour choisir où).

## Lancer le client

```bash
python -m papote.client
```

À l'écran de connexion : indique l'adresse du serveur, ton nom d'utilisateur et
un mot de passe, puis **Créer un compte** (ou **Se connecter**). Le jeton de
session est mémorisé dans `~/.config/papote/config.json` pour te reconnecter
automatiquement.

## Discuter avec quelqu'un sur Internet

Le serveur n'écoute que sur ta machine. Pour l'exposer sans configurer ta box,
ouvre un tunnel :

```bash
# serveur lancé sur le port 8765
cloudflared tunnel --url http://localhost:8765
```

Cloudflare renvoie une URL `https://xxxx.trycloudflare.com` : donne-la à ton
correspondant, qui la saisit comme **serveur** dans son client (le `wss://` est
ajouté automatiquement).

## Architecture

| Fichier | Rôle |
|---------|------|
| `papote/server.py`   | serveur WebSocket : comptes, amis, groupes, relai des messages, casino |
| `papote/client.py`   | interface terminal (Textual) |
| `papote/db.py`       | stockage SQLite (messages + soldes du casino) |
| `papote/casino.py`   | logique des jeux de casino (aléatoire côté serveur) |
| `papote/games.py`    | logique des jeux multijoueur (morpion, puissance 4) |
| `papote/protocol.py` | format des messages JSON échangés |
| `papote/net.py`      | connexion WebSocket + config côté client |
