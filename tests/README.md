# Tests de papote

Deux niveaux de tests, chacun démarre **son propre serveur** sur un port
éphémère avec une base SQLite temporaire (rien à lancer à la main).

## Serveur (bout en bout)

`test_server_e2e.py` pilote plusieurs clients WebSocket et vérifie les flux :
profils, groupes, serveurs/salons, signalisation vocale, appels + journal,
images et réactions.

```bash
pip install websockets
python tests/test_server_e2e.py     # ou : python -m pytest tests/test_server_e2e.py
```

## Interface (navigateur)

`test_ui_playwright.py` ouvre deux Firefox (micro factice) et rejoue les
parcours clés de l'UI. Il est **ignoré automatiquement** si Playwright n'est pas
installé.

```bash
pip install playwright pytest
playwright install firefox
python tests/test_ui_playwright.py  # ou : python -m pytest tests/
```

> Les deux fichiers sont utilisables directement (`python tests/…`) ou via
> `pytest`.
