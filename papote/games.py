"""Jeux multijoueurs à deux joueurs (morpion, puissance 4).

Logique pure et sans état : le plateau est une simple liste de cases, chaque
case valant "" (vide), "0" (joueur 0) ou "1" (joueur 1). Le serveur orchestre
les tours ; ces fonctions se contentent de valider et d'appliquer les coups.

Indexation ligne par ligne : la case (ligne r, colonne c) est à `r * cols + c`,
la ligne 0 étant en haut. Un « coup » est un entier :
  - morpion    : l'indice de la case (0-8)
  - puissance4 : le numéro de colonne (0-6), le jeton tombe tout en bas.
"""

from __future__ import annotations

GAMES = {"morpion": "Morpion", "puissance4": "Puissance 4"}

_MORPION_LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),      # lignes
    (0, 3, 6), (1, 4, 7), (2, 5, 8),      # colonnes
    (0, 4, 8), (2, 4, 6),                 # diagonales
]

P4_ROWS, P4_COLS = 6, 7


def dims(game: str) -> tuple[int, int]:
    return (3, 3) if game == "morpion" else (P4_ROWS, P4_COLS)


def new_board(game: str) -> list[str]:
    rows, cols = dims(game)
    return [""] * (rows * cols)


def legal_moves(game: str, board: list[str]) -> list[int]:
    if game == "morpion":
        return [i for i in range(9) if board[i] == ""]
    # puissance 4 : une colonne est jouable si sa case du haut est vide
    return [c for c in range(P4_COLS) if board[c] == ""]


def is_legal(game: str, board: list[str], move) -> bool:
    return isinstance(move, int) and move in legal_moves(game, board)


def _p4_landing_row(board: list[str], col: int) -> int | None:
    for r in range(P4_ROWS - 1, -1, -1):
        if board[r * P4_COLS + col] == "":
            return r
    return None


def apply_move(game: str, board: list[str], move: int, player: int) -> list[str]:
    """Renvoie un nouveau plateau avec le coup appliqué (ne modifie pas l'entrée)."""
    b = list(board)
    if game == "morpion":
        b[move] = str(player)
    else:
        row = _p4_landing_row(b, move)
        b[row * P4_COLS + move] = str(player)
    return b


def _morpion_winner(board: list[str]):
    for a, b, c in _MORPION_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return int(board[a])
    return None


def _p4_winner(board: list[str]):
    for r in range(P4_ROWS):
        for c in range(P4_COLS):
            p = board[r * P4_COLS + c]
            if not p:
                continue
            for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                count = 1
                rr, cc = r + dr, c + dc
                while 0 <= rr < P4_ROWS and 0 <= cc < P4_COLS and board[rr * P4_COLS + cc] == p:
                    count += 1
                    if count == 4:
                        return int(p)
                    rr += dr
                    cc += dc
    return None


def winner(game: str, board: list[str]):
    """Indice du joueur gagnant (0 ou 1), ou None si pas de gagnant."""
    return _morpion_winner(board) if game == "morpion" else _p4_winner(board)


def is_full(game: str, board: list[str]) -> bool:
    return len(legal_moves(game, board)) == 0
