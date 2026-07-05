"""Logique des jeux de casino de papote.

Fonctions pures et sans état : l'aléatoire est tiré ici, côté serveur, pour
qu'un client ne puisse jamais décider lui-même s'il a gagné. Chaque fonction
renvoie un dictionnaire décrivant la partie :

    {
      "game":   "coinflip" | "dice" | "slots",
      "won":    bool,
      "delta":  int,     # variation du solde (positif = gain, négatif = perte)
      "detail": str,     # description lisible du résultat
      "visual": str,     # petit rendu (pièce, face de dé, rouleaux…)
    }

`delta` est la variation NETTE : la mise est déjà retranchée. Sur une perte,
delta vaut -mise.
"""

from __future__ import annotations

import random

# Symboles de la machine à sous, du plus commun au plus rare.
SLOT_SYMBOLS = ["🍒", "🍋", "🔔", "⭐", "💎"]

DICE_FACES = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}

# Gain d'un dé deviné juste : mise remboursée + 5× (probabilité 1/6, jeu équitable).
DICE_PAYOUT = 5


def play_coinflip(bet: int, choice) -> dict:
    choice = str(choice or "").strip().lower()
    if choice not in ("pile", "face"):
        raise ValueError("Choisis « pile » ou « face ».")
    result = random.choice(["pile", "face"])
    won = result == choice
    return {
        "game": "coinflip",
        "won": won,
        "delta": bet if won else -bet,
        "detail": f"La pièce tombe sur {result} (tu avais dit {choice}).",
        "visual": "🪙 " + result,
    }


def play_dice(bet: int, choice) -> dict:
    try:
        target = int(choice)
    except (TypeError, ValueError):
        raise ValueError("Choisis un numéro entre 1 et 6.")
    if not 1 <= target <= 6:
        raise ValueError("Le numéro doit être entre 1 et 6.")
    roll = random.randint(1, 6)
    won = roll == target
    return {
        "game": "dice",
        "won": won,
        "delta": bet * DICE_PAYOUT if won else -bet,
        "detail": f"Le dé donne {roll} {DICE_FACES[roll]} (tu avais misé sur {target}).",
        "visual": DICE_FACES[roll],
    }


def _slot_multiplier(reels: list[str]) -> int:
    """Multiplicateur de gain total (mise comprise) selon les 3 rouleaux.

    Table de gains calibrée pour un avantage maison d'environ 6 % :
        💎💎💎 ×30   ·   ⭐⭐⭐ ×15   ·   autre brelan ×4
        💎💎 ×3      ·   🍒🍒 ×2     ·   sinon perdu
    (Payer *toutes* les paires serait intenable : elles sortent ~48 % du temps.)
    """
    counts = {s: reels.count(s) for s in set(reels)}
    if max(counts.values()) == 3:                  # trois identiques
        return {"💎": 30, "⭐": 15}.get(reels[0], 4)
    if counts.get("💎", 0) == 2:                   # deux diamants
        return 3
    if counts.get("🍒", 0) == 2:                   # deux cerises
        return 2
    return 0                                        # rien


def play_slots(bet: int, choice=None) -> dict:
    reels = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
    mult = _slot_multiplier(reels)
    delta = (mult - 1) * bet if mult > 0 else -bet
    line = "  ".join(reels)
    if mult >= 15:
        detail = f"{line}   💰 JACKPOT ×{mult} !"
    elif mult > 0:
        detail = f"{line}   ×{mult}"
    else:
        detail = f"{line}   perdu…"
    return {
        "game": "slots",
        "won": delta > 0,
        "delta": delta,
        "detail": detail,
        "visual": line,
    }


_GAMES = {
    "coinflip": play_coinflip,
    "dice": play_dice,
    "slots": play_slots,
}


def resolve(game: str, bet: int, choice=None) -> dict:
    """Joue une partie du jeu demandé et renvoie le résultat."""
    fn = _GAMES.get(game)
    if fn is None:
        raise ValueError("Jeu inconnu.")
    return fn(bet, choice)
