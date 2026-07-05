"""Test d'interface (navigateur) du client web papote.

Démarre un vrai serveur, ouvre deux navigateurs Firefox (micro factice) et
vérifie les parcours clés côté UI : profil (bannière/statut), serveur + salon
vocal, barre d'appel non bloquante avec chrono, partage d'écran (sélecteur
résolution/fps), envoi d'image et réactions.

Ignoré automatiquement si Playwright (ou son navigateur Firefox) n'est pas
installé :

    pip install playwright && playwright install firefox
    python tests/test_ui_playwright.py      # ou : python -m pytest tests/
"""

from __future__ import annotations

import asyncio
import binascii
import os
import struct
import sys
import tempfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_server_e2e import running_server  # noqa: E402

try:
    from playwright.async_api import async_playwright
    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False


def _tiny_png(path):
    w = h = 64
    raw = b"".join(b"\x00" + bytes([220, 60, 90, 255]) * w for _ in range(h))

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


async def _signup(page, url, user):
    await page.goto(url)
    await page.click("#tab-reg")
    await page.fill("#in-user", user)
    await page.fill("#in-pass", "pw12345")
    await page.click("#do-auth")
    await page.wait_for_selector("#app:not(.hidden)", timeout=8000)


async def _scenario(http_url):
    pic = os.path.join(tempfile.gettempdir(), "papote_test_pic.png")
    _tiny_png(pic)
    fails, errors = [], []

    def ck(cond, label):
        fails.append(label) if not cond else None

    async with async_playwright() as p:
        browser = await p.firefox.launch(firefox_user_prefs={
            "media.navigator.streams.fake": True,
            "media.navigator.permission.disabled": True,
        })
        c1 = await browser.new_context()
        c2 = await browser.new_context()
        sana = await c1.new_page()
        bob = await c2.new_page()
        for pg in (sana, bob):
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        await _signup(sana, http_url, "sana")
        await _signup(bob, http_url, "bob")

        # profil : bannière + statut
        await sana.click("#me-chip")
        await sana.wait_for_selector("#pe-status")
        ck(await sana.locator("#pe-bfile").count() == 1, "éditeur a le champ bannière")
        await sana.fill("#pe-status", "en stream")
        await sana.click("#pe-save")
        await sana.wait_for_selector("#pe-save", state="detached", timeout=5000)

        # amitié sana <-> bob
        await sana.click("#nav-online")
        await sana.wait_for_selector("#online-list .list-row", timeout=5000)
        await sana.click("#online-list .list-row .btn")
        await sana.click("#ov-close")
        await bob.wait_for_selector(".convo .btn.green", timeout=5000)
        await bob.click(".convo .btn.green")
        await asyncio.sleep(0.5)
        await sana.wait_for_selector(".convo .dot.on", timeout=5000)

        # image + réaction
        await sana.click(".convo")
        await sana.wait_for_selector("#composer")
        await sana.set_input_files("#img-input", pic)
        await sana.wait_for_selector(".msg .msg-img", timeout=6000)
        await bob.click(".convo")
        await bob.wait_for_selector(".msg .msg-img", timeout=6000)
        ck(True, "image transmise")
        await bob.locator(".msg").last.hover()
        await bob.locator(".msg .react-add").last.click()
        await bob.wait_for_selector("#reactpop", timeout=3000)
        await bob.locator("#reactpop .ep-emoji >> nth=3").click()
        await asyncio.sleep(0.6)
        ck(await sana.locator(".react-chip").count() >= 1, "réaction propagée")

        # serveur + vocal : barre flottante + chrono + partage d'écran
        await sana.click(".srv-ico.add")
        await sana.fill("#pm-in", "Stream")
        await sana.click("#pm-ok")
        await sana.wait_for_selector("#server-side .chan.voice", timeout=5000)
        await sana.click("#server-side .chan.voice >> nth=0")
        await sana.wait_for_selector("#voicebar", timeout=6000)
        ck(await sana.locator("#vb-screen").count() == 1, "bouton partage d'écran")
        ck(await sana.locator("#vb-time").count() == 1, "chrono vocal")
        await asyncio.sleep(1.2)
        ck((await sana.locator("#vb-time").inner_text()).strip() != "⏱ 00:00", "le chrono avance")
        await sana.click("#vb-screen")
        await sana.wait_for_selector("#ss-res", timeout=3000)
        ck(await sana.locator("#ss-res button").count() >= 3
           and await sana.locator("#ss-fps button").count() == 3, "choix résolution + fps")
        await sana.click("#ss-cancel")
        await sana.click("#vb-leave")

        real = [e for e in errors if "favicon" not in e.lower()]
        ck(not real, f"aucune erreur JS ({real[:2]})")
        await browser.close()

    if os.path.exists(pic):
        os.remove(pic)
    return fails


def run():
    with running_server() as ws_url:
        http = ws_url.replace("ws://", "http://")
        fails = asyncio.run(_scenario(http))
    print("\n".join((" FAIL " + f) for f in fails) or "  tous les checks UI OK")
    return fails


def test_ui():
    if not HAVE_PLAYWRIGHT:
        import pytest
        pytest.skip("Playwright non installé")
    assert not run(), "des vérifications UI ont échoué"


if __name__ == "__main__":
    if not HAVE_PLAYWRIGHT:
        print("Playwright non installé — test UI ignoré "
              "(pip install playwright && playwright install firefox)")
        sys.exit(0)
    sys.exit(1 if run() else 0)
