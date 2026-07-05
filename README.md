# 💬 papote

Une messagerie **terminal + web** : un serveur unique, un client terminal
([Textual](https://textual.textualize.io/)) **et** un client web (dans le
navigateur), tout passe par WebSocket.

Messages privés, groupes, demandes d'ami, présence en ligne, historique,
un **casino** avec monnaie virtuelle et classement 🎰, et des **duels entre
amis** (morpion, puissance 4) avec paris ⚔️.

Et aussi : des **serveurs façon Discord** avec salons écrits et **salons vocaux
toujours ouverts** (+ **partage d'écran** avec le son), des **appels privés**
non bloquants avec chrono et journal, des **profils** (photo, bannière, bio,
couleur, statut), l'**envoi d'images**, des **emojis** et des **réactions** aux
messages. 🎥📞🖼️

## 🌍 Version web (le plus simple)

Le serveur sert aussi une page web sur le même port. Ouvre simplement l'adresse
du serveur **dans un navigateur** (PC ou téléphone) — aucune installation :

```
http://localhost:8765/            # en local
https://xxxx.trycloudflare.com/   # à travers un tunnel (voir plus bas)
```

Tu te connectes, et tu retrouves tout (chat, casino, duels, stats) avec une
interface animée. La session est mémorisée : tu **restes connecté même si le
serveur redémarre** (reconnexion automatique). C'est la façon la plus simple de
jouer avec des amis : tu leur envoies l'URL, ils l'ouvrent, c'est tout.

Astuce : le script `papote-online` (dans `~/.local/bin/`) démarre le serveur +
un tunnel public et affiche l'URL à partager en grand.

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

## 🏠 Serveurs & salons vocaux (façon Discord)

Le rail d'icônes à gauche regroupe **🏠 Messages privés** et tes **serveurs**.
Crée un serveur avec **+**, il arrive avec un salon **écrit** et un salon
**vocal**. Dans un serveur tu peux :

- créer d'autres salons (écrits ou vocaux) avec **＋** ;
- ajouter des amis via une **liste à cocher** ;
- cliquer un **salon vocal** pour le rejoindre — il reste **ouvert en
  permanence**, on entre et sort quand on veut (mesh WebRTC en pair-à-pair).

Pendant que tu es en vocal, une **barre flottante** montre les participants et un
**chrono** ⏱ — et tu continues à utiliser le site (chat, casino…). Depuis cette
barre :

- **🖥️ Partage d'écran** avec le **son** : choisis la **résolution** (720p /
  1080p / 1440p / source) et les **fps** (15 / 30 / 60) ;
- **clic droit** sur un participant : règle son **volume** (0–200 %) ou le rends
  **muet** rien que pour toi ;
- coupe **ton** micro d'un clic.

## 📞 Appels privés

Ouvre une conversation avec un ami et clique **📞**. L'appel est **non bloquant**
(barre flottante avec **chrono**) : tu peux continuer à naviguer. Le bouton
**📞** de la barre du haut ouvre l'**historique des appels** (répondu / manqué /
refusé + durée), avec rappel en un clic.

## 🖼️ Images, emojis & réactions

Dans le composer : **📎** envoie une **image** (compressée automatiquement) et
**😀** ouvre le sélecteur d'**emojis**. Survole un message et clique **＋** pour
y ajouter une **réaction** ; reclique une pastille pour l'enlever.

## 🪪 Profils

Clique ton nom (en haut) pour éditer ton profil : **photo**, **bannière**,
**bio**, **statut** et **couleur de thème**. Clique l'avatar de quelqu'un (liste
en ligne, membres d'un serveur, en-tête d'un DM) pour voir sa carte.


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
python -m papote.server --host 0.0.0.0 --port 8765 --admin sana
```

La base SQLite est créée automatiquement (`--db chemin.db` pour choisir où).
`--admin` (ou la variable d'environnement `PAPOTE_ADMIN`, valeur par défaut
`sana`) désigne le ou les comptes autorisés à voir les IP des connectés
(plusieurs comptes séparés par des virgules).

> ⚠️ Les appels, le vocal et le partage d'écran nécessitent un **contexte
> sécurisé** : `localhost` ou une URL **https** (le tunnel). En http sur une IP
> distante, le navigateur bloque l'accès au micro/écran.

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

Le tunnel est **temporaire** (l'URL change à chaque lancement). Pour une **URL
permanente en ligne 24/7** (Fly.io, VPS + Caddy, Railway…), voir
**[DEPLOY.md](DEPLOY.md)** : le dépôt contient déjà `Dockerfile` et `fly.toml`.

## Architecture

| Fichier | Rôle |
|---------|------|
| `papote/server.py`   | serveur WebSocket : comptes, amis, groupes/serveurs, relai des messages, casino, signalisation WebRTC (appels + vocaux), journal d'appels, vue admin |
| `papote/client.py`   | interface terminal (Textual) |
| `papote/db.py`       | stockage SQLite (messages, profils, serveurs/salons, réactions, journal d'appels…) |
| `papote/casino.py`   | logique des jeux de casino (aléatoire côté serveur) |
| `papote/games.py`    | logique des jeux multijoueur (morpion, puissance 4) |
| `papote/webclient.html` | client web autonome (servi par le serveur) |
| `papote/protocol.py` | format des messages JSON échangés (documenté en tête de fichier) |
| `papote/net.py`      | connexion WebSocket + config côté client |
| `tests/`             | tests de bout en bout du serveur |

Le **partage d'écran** et les **vocaux de groupe** utilisent un **mesh WebRTC**
en pair-à-pair (chaque participant est relié à chaque autre) ; le serveur ne fait
que **relayer la signalisation** — l'audio et la vidéo ne transitent pas par lui.

## Tests

Les tests de bout en bout démarrent un vrai serveur sur un port éphémère (base
temporaire) et pilotent plusieurs clients WebSocket :

```bash
python -m pytest tests/            # ou : python tests/test_server_e2e.py
```

Les tests d'interface (navigateur) utilisent [Playwright](https://playwright.dev/)
avec Firefox et un micro factice — voir `tests/README.md`.
