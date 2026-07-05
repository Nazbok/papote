# 💬 papote

Une messagerie pour le **terminal** : un serveur, un client avec une interface
[Textual](https://textual.textualize.io/), et tout passe par WebSocket.

Messages privés, groupes, demandes d'ami, présence en ligne, historique.

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
| `papote/server.py`   | serveur WebSocket : comptes, amis, groupes, relai des messages |
| `papote/client.py`   | interface terminal (Textual) |
| `papote/db.py`       | stockage SQLite |
| `papote/protocol.py` | format des messages JSON échangés |
| `papote/net.py`      | connexion WebSocket + config côté client |
