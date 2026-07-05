# 🌍 Héberger papote en ligne (24/7)

papote a besoin d'un **serveur qui tourne en permanence** (WebSocket + base
SQLite) : un hébergeur de fichiers statiques comme GitHub Pages ne suffit **pas**.
Ci-dessous, trois façons de le mettre en ligne avec une **URL permanente en
https** (obligatoire pour le micro, le vocal et le partage d'écran).

Le dépôt contient déjà tout le nécessaire : `Dockerfile`, `.dockerignore` et
`fly.toml`.

---

## Option A — Fly.io (le plus simple, recommandé)

Machine toujours allumée + volume persistant pour la base. ~quelques €/mois pour
la plus petite VM (`shared-cpu-1x`, 512 Mo).

```bash
# 1. Installer flyctl puis se connecter
curl -L https://fly.io/install.sh | sh
fly auth login

# 2. Depuis le dossier du projet : créer l'app (garde le nom du fly.toml ou change-le)
fly launch --no-deploy --copy-config --name papote

# 3. Créer le volume persistant pour la base (une seule fois, même région que l'app)
fly volumes create papote_data --size 1 --region cdg

# 4. Déployer
fly deploy

# 5. Ouvrir
fly open        # -> https://papote.fly.dev
```

- **Compte admin (voir les IP)** : déjà `sana` via `fly.toml`. Pour le changer :
  `fly secrets set PAPOTE_ADMIN=tonpseudo` (les secrets écrasent l'`[env]`).
- **Domaine perso** (optionnel) : `fly certs add chat.tondomaine.fr` puis ajoute
  les enregistrements DNS indiqués.
- **Mettre à jour** : `git push` puis `fly deploy`.
- **Logs** : `fly logs`.

> Le `fly.toml` fixe `auto_stop_machines = false` + `min_machines_running = 1`
> pour que le chat reste joignable (présence, vocal). C'est ce qui a un petit
> coût — une app qui s'endort couperait les connexions.

---

## Option B — Ton propre VPS + Caddy (robuste, HTTPS auto gratuit)

Si tu as un petit serveur Linux et un nom de domaine. Caddy s'occupe du
certificat HTTPS **et** laisse passer les WebSockets tout seul.

**1. Récupérer le code et lancer le serveur en service systemd**
`/etc/systemd/system/papote.service` :

```ini
[Unit]
Description=papote server
After=network.target

[Service]
User=papote
WorkingDirectory=/opt/papote
Environment=PORT=8765
Environment=PAPOTE_ADMIN=sana
Environment=PAPOTE_DB=/opt/papote/data/server.db
ExecStart=/opt/papote/.venv/bin/python -m papote.server
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd -r -m -d /opt/papote papote
sudo -u papote git clone https://github.com/Nazbok/papote /opt/papote
cd /opt/papote && sudo -u papote python -m venv .venv
sudo -u papote .venv/bin/pip install websockets
sudo -u papote mkdir -p /opt/papote/data
sudo systemctl enable --now papote
```

**2. Caddy en reverse-proxy HTTPS** — `/etc/caddy/Caddyfile` :

```
chat.tondomaine.fr {
    reverse_proxy localhost:8765
}
```

```bash
sudo systemctl reload caddy
```

C'est tout : `https://chat.tondomaine.fr` est en ligne, en https, WebSockets
compris. Caddy transmet la vraie IP des visiteurs (`X-Forwarded-For`), donc la
vue admin affiche les bonnes IP.

---

## Option C — Render / Railway (rapide, avec réserves)

- **Railway** : « New Project → Deploy from repo », il détecte le `Dockerfile`.
  Ajoute un **volume** monté sur `/data` et les variables `PAPOTE_DB=/data/server.db`,
  `PAPOTE_ADMIN=sana`. Fonctionne bien ; crédit mensuel offert puis à l'usage.
- **Render** : « New → Web Service » depuis le repo (Docker). ⚠️ Le plan gratuit
  **s'endort** après inactivité (coupe les WebSockets) et le disque gratuit
  **n'est pas persistant** (la base est perdue au redéploiement). Prends un plan
  payant + un disque persistant si tu veux garder l'historique.

---

## Bon à savoir

- **HTTPS obligatoire** pour le micro/vocal/partage d'écran — les trois options
  ci-dessus le fournissent. En http sur une IP publique, le navigateur bloque.
- **Persistance** : sans volume monté sur la base (`PAPOTE_DB`), les comptes et
  messages disparaissent au redéploiement. Les options A et B en ont un.
- **Vocal derrière NAT strict** : le mesh WebRTC utilise des serveurs STUN
  publics (Google). Ça marche dans la grande majorité des cas ; si deux personnes
  n'arrivent jamais à s'entendre, il faudrait ajouter un serveur **TURN** (non
  inclus).
- **Tester l'image en local** :

  ```bash
  docker build -t papote .
  docker run --rm -p 8765:8765 -e PAPOTE_ADMIN=sana papote
  # -> http://localhost:8765/
  ```
