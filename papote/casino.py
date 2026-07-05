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


# --- Roulette (européenne, un seul zéro) ------------------------------------

ROULETTE_RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


def _roulette_color(n: int) -> str:
    if n == 0:
        return "vert"
    return "rouge" if n in ROULETTE_RED else "noir"


def play_roulette(bet: int, choice) -> dict:
    """Roulette européenne. `choice` : un numéro 0-36, ou rouge/noir, pair/impair,
    bas/haut, d1/d2/d3 (douzaines)."""
    choice = str(choice or "").strip().lower()
    spin = random.randint(0, 36)
    color = _roulette_color(spin)
    won = False
    delta = -bet
    label = choice

    if choice.isdigit():
        target = int(choice)
        if not 0 <= target <= 36:
            raise ValueError("Numéro de roulette entre 0 et 36.")
        won = spin == target
        delta = bet * 35 if won else -bet
        label = f"le numéro {target}"
    elif choice in ("rouge", "red", "noir", "black"):
        want = "rouge" if choice in ("rouge", "red") else "noir"
        won = color == want
        delta = bet if won else -bet
        label = want
    elif choice in ("pair", "even", "impair", "odd"):
        want_even = choice in ("pair", "even")
        won = spin != 0 and ((spin % 2 == 0) == want_even)
        delta = bet if won else -bet
        label = "pair" if want_even else "impair"
    elif choice in ("bas", "low", "haut", "high"):
        low = choice in ("bas", "low")
        won = (1 <= spin <= 18) if low else (19 <= spin <= 36)
        delta = bet if won else -bet
        label = "bas (1-18)" if low else "haut (19-36)"
    elif choice in ("d1", "d2", "d3", "douzaine1", "douzaine2", "douzaine3"):
        idx = int(choice[-1])
        lo, hi = (idx - 1) * 12 + 1, idx * 12
        won = lo <= spin <= hi
        delta = bet * 2 if won else -bet
        label = f"la douzaine {lo}-{hi}"
    else:
        raise ValueError(
            "Pari inconnu. Ex : 17, rouge, noir, pair, impair, bas, haut, d1, d2, d3."
        )

    return {
        "game": "roulette",
        "won": won,
        "delta": delta,
        "detail": f"La bille tombe sur {spin} ({color}). Tu misais sur {label}.",
        "visual": f"🎡 {spin} {color}",
    }


_GAMES = {
    "coinflip": play_coinflip,
    "dice": play_dice,
    "slots": play_slots,
    "roulette": play_roulette,
}


def resolve(game: str, bet: int, choice=None) -> dict:
    """Joue une partie (un coup) du jeu demandé et renvoie le résultat."""
    fn = _GAMES.get(game)
    if fn is None:
        raise ValueError("Jeu inconnu.")
    return fn(bet, choice)


# --- Blackjack (jeu à état, piloté par le serveur) --------------------------

RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["♠", "♥", "♦", "♣"]
DEALER_STANDS_ON = 17
HIDDEN_CARD = "🂠"


def _card_points(rank: str) -> int:
    if rank in ("J", "Q", "K"):
        return 10
    if rank == "A":
        return 11
    return int(rank)


def hand_value(cards) -> int:
    """Meilleure valeur d'une main, les as comptant 11 ou 1."""
    total = aces = 0
    for c in cards:
        rank = c[:-1]           # le dernier caractère est la couleur (♠♥♦♣)
        total += _card_points(rank)
        if rank == "A":
            aces += 1
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def is_blackjack(cards) -> bool:
    return len(cards) == 2 and hand_value(cards) == 21


def new_deck() -> list:
    deck = [rank + suit for rank in RANKS for suit in SUITS]
    random.shuffle(deck)
    return deck


def bj_new_game(bet: int) -> dict:
    deck = new_deck()
    state = {
        "bet": bet,
        "deck": deck,
        "player": [deck.pop(), deck.pop()],
        "dealer": [deck.pop(), deck.pop()],
        "done": False,
        "delta": 0,
        "result": None,
    }
    pbj, dbj = is_blackjack(state["player"]), is_blackjack(state["dealer"])
    if pbj or dbj:
        state["done"] = True
        if pbj and dbj:
            state["delta"], state["result"] = 0, "Double blackjack — égalité."
        elif pbj:
            state["delta"] = (bet * 3) // 2
            state["result"] = "Blackjack ! Tu gagnes ×1,5."
        else:
            state["delta"], state["result"] = -bet, "Le croupier a un blackjack."
    return state


def bj_hit(state: dict) -> dict:
    if not state["done"]:
        state["player"].append(state["deck"].pop())
        if hand_value(state["player"]) > 21:
            state["done"] = True
            state["delta"] = -state["bet"]
            state["result"] = f"Tu dépasses 21 ({hand_value(state['player'])}) — perdu."
    return state


def bj_stand(state: dict) -> dict:
    if state["done"]:
        return state
    dealer, deck = state["dealer"], state["deck"]
    while hand_value(dealer) < DEALER_STANDS_ON:
        dealer.append(deck.pop())
    pv, dv = hand_value(state["player"]), hand_value(dealer)
    state["done"] = True
    if dv > 21:
        state["delta"], state["result"] = state["bet"], f"Le croupier crève ({dv}) — tu gagnes !"
    elif pv > dv:
        state["delta"], state["result"] = state["bet"], f"{pv} contre {dv} — tu gagnes !"
    elif pv < dv:
        state["delta"], state["result"] = -state["bet"], f"{pv} contre {dv} — perdu."
    else:
        state["delta"], state["result"] = 0, f"Égalité à {pv}."
    return state


def bj_public(state: dict) -> dict:
    """Vue transmise au client : la carte cachée du croupier reste masquée
    tant que la partie n'est pas finie."""
    if state["done"]:
        dealer, dealer_value = state["dealer"], hand_value(state["dealer"])
    else:
        dealer, dealer_value = [state["dealer"][0], HIDDEN_CARD], None
    return {
        "player": state["player"],
        "player_value": hand_value(state["player"]),
        "dealer": dealer,
        "dealer_value": dealer_value,
        "done": state["done"],
        "delta": state["delta"],
        "result": state["result"],
    }
