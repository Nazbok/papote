"""Client papote : messagerie en terminal avec une interface Textual."""

from __future__ import annotations

import argparse
import time

from rich.markup import escape
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
)

from . import DEFAULT_PORT, net, protocol


def normalize_url(s: str) -> str:
    s = s.strip()
    if not s:
        return ""
    # Convertit un éventuel schéma http(s) en ws(s) : on colle souvent l'URL
    # https:// donnée par Cloudflare, mais WebSocket exige ws:// ou wss://.
    if "://" in s:
        scheme, _, rest = s.partition("://")
        scheme = scheme.lower()
        if scheme in ("http", "ws"):
            return "ws://" + rest
        if scheme in ("https", "wss"):
            return "wss://" + rest
        return s
    host = s.split("/")[0].split(":")[0]
    if host in ("localhost", "127.0.0.1"):
        return "ws://" + s
    return "wss://" + s


# --- Écran de connexion ------------------------------------------------------

class LoginScreen(Screen):
    def compose(self) -> ComposeResult:
        with Vertical(id="login-box"):
            yield Static("💬  p a p o t e", id="logo")
            yield Static("Messagerie de terminal", id="subtitle")
            yield Input(placeholder="Serveur (ex: wss://xxx.trycloudflare.com)", id="server")
            yield Input(placeholder="Nom d'utilisateur", id="username")
            yield Input(placeholder="Mot de passe", password=True, id="password")
            with Horizontal(id="login-buttons"):
                yield Button("Se connecter", variant="primary", id="login")
                yield Button("Créer un compte", id="register")
            yield Static("", id="status")

    def on_mount(self) -> None:
        cfg = net.load_config()
        self.query_one("#server", Input).value = cfg.get("server", "")
        self.query_one("#username", Input).value = cfg.get("username", "")

    def _submit(self, mode: str) -> None:
        server = self.query_one("#server", Input).value
        u = self.query_one("#username", Input).value.strip()
        p = self.query_one("#password", Input).value
        if not server or not u or not p:
            self.query_one("#status", Static).update("Remplis serveur, utilisateur et mot de passe.")
            return
        self.query_one("#status", Static).update("Connexion…")
        self.app.do_auth(server, u, p, mode)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self._submit("login" if event.button.id == "login" else "register")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit("login")


# --- Fenêtres modales --------------------------------------------------------

class AddFriendModal(ModalScreen):
    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static("➕  Ajouter un ami", classes="modal-title")
            yield Input(placeholder="nom d'utilisateur", id="fname")
            with Horizontal(classes="modal-buttons"):
                yield Button("Ajouter", variant="primary", id="ok")
                yield Button("Annuler", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.dismiss(self.query_one("#fname", Input).value.strip())
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())


class NewGroupModal(ModalScreen):
    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static("👥  Nouveau groupe", classes="modal-title")
            yield Input(placeholder="nom du groupe", id="gname")
            yield Input(placeholder="membres (séparés par des virgules)", id="gmembers")
            with Horizontal(classes="modal-buttons"):
                yield Button("Créer", variant="primary", id="ok")
                yield Button("Annuler", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            name = self.query_one("#gname", Input).value.strip()
            raw = self.query_one("#gmembers", Input).value
            members = [m.strip() for m in raw.split(",") if m.strip()]
            self.dismiss((name, members))
        else:
            self.dismiss(None)


# --- Casino ------------------------------------------------------------------

class CasinoScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Fermer")]

    def compose(self) -> ComposeResult:
        with Vertical(id="casino-box"):
            yield Static("🎰  C A S I N O", id="casino-title")
            yield Static("", id="casino-balance")
            yield Input(value="100", placeholder="mise", id="casino-bet", type="integer")
            with Horizontal(classes="casino-row"):
                yield Button("🪙 Pile", id="cf-pile")
                yield Button("🪙 Face", id="cf-face")
            with Horizontal(classes="casino-row"):
                yield Input(value="6", id="dice-num", type="integer")
                yield Button("🎲 Lancer le dé (×6)", id="dice-roll")
            with Horizontal(classes="casino-row"):
                yield Button("🎰 Machine à sous", variant="success", id="slots")
                yield Button("🎡 Roulette", id="roulette")
                yield Button("🃏 Blackjack", id="blackjack")
            with Horizontal(classes="casino-row"):
                yield Button("🎁 Bonus", id="bonus")
                yield Button("🏆 Classement", id="board")
                yield Button("✖ Fermer", id="close")
            yield RichLog(id="casino-log", markup=True, wrap=True)

    def on_mount(self) -> None:
        self.app.net_send(op="casino_state")
        self.refresh_balance()
        log = self.query_one("#casino-log", RichLog)
        log.write("[dim]Mise tes jetons et tente ta chance ![/dim]")
        log.write("[dim]Pile ou Face ×2  ·  Dé ×6  ·  Machine à sous jusqu'à ×30[/dim]")

    def refresh_balance(self) -> None:
        self.query_one("#casino-balance", Static).update(
            f"Solde : [b yellow]{self.app.balance}[/b yellow] jetons"
        )

    def _bet(self) -> int:
        try:
            return int(self.query_one("#casino-bet", Input).value)
        except ValueError:
            return 0

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "close":
            self.dismiss(None)
        elif bid == "board":
            self.app.open_leaderboard()
        elif bid == "roulette":
            self.app.open_roulette()
        elif bid == "blackjack":
            self.app.open_blackjack()
        elif bid == "bonus":
            self.app.net_send(op="casino_bonus")
        elif bid == "cf-pile":
            self.app.net_send(op="casino_play", game="coinflip", bet=self._bet(), choice="pile")
        elif bid == "cf-face":
            self.app.net_send(op="casino_play", game="coinflip", bet=self._bet(), choice="face")
        elif bid == "dice-roll":
            num = self.query_one("#dice-num", Input).value.strip()
            self.app.net_send(op="casino_play", game="dice", bet=self._bet(), choice=num)
        elif bid == "slots":
            self.app.net_send(op="casino_play", game="slots", bet=self._bet())

    # --- retours du serveur (appelés par l'app) ---------------------------

    def on_result(self, msg: dict) -> None:
        self.refresh_balance()
        log = self.query_one("#casino-log", RichLog)
        delta = msg.get("delta", 0)
        detail = escape(str(msg.get("detail", "")))
        if msg.get("won"):
            log.write(f"[green]✔[/green] {detail}   [b green]{delta:+d}[/b green] jetons")
        else:
            log.write(f"[red]✘[/red] {detail}   [b red]{delta:+d}[/b red] jetons")

    def on_bonus(self) -> None:
        self.refresh_balance()
        self.query_one("#casino-log", RichLog).write(
            "[b magenta]🎁 Bonus encaissé ![/b magenta] Rejoue !"
        )


class LeaderboardScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Fermer")]

    def compose(self) -> ComposeResult:
        with Vertical(id="board-box"):
            yield Static("🏆  Classement du casino", id="board-title")
            yield RichLog(id="board-log", markup=True, wrap=True)
            with Horizontal(classes="modal-buttons"):
                yield Button("Fermer", variant="primary", id="close")

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def show(self, players: list) -> None:
        log = self.query_one("#board-log", RichLog)
        log.clear()
        if not players:
            log.write("[dim]Personne n'a encore joué.[/dim]")
            return
        medals = {0: "🥇", 1: "🥈", 2: "🥉"}
        for i, p in enumerate(players):
            rank = medals.get(i, f"[dim]{i + 1}.[/dim]")
            me = " [b cyan](toi)[/b cyan]" if p["username"] == self.app.username else ""
            log.write(
                f"{rank} [b]{escape(p['username'])}[/b]{me} — "
                f"[yellow]{p['balance']}[/yellow] jetons   "
                f"[dim](record {p['biggest_win']:+d}, {p['games_played']} parties)[/dim]"
            )


class RouletteScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Fermer")]

    BETS = [
        ("🔴 Rouge", "rouge"), ("⚫ Noir", "noir"),
        ("Pair", "pair"), ("Impair", "impair"),
        ("Bas 1-18", "bas"), ("Haut 19-36", "haut"),
        ("Douzaine 1", "d1"), ("Douzaine 2", "d2"), ("Douzaine 3", "d3"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="roulette-box"):
            yield Static("🎡  R O U L E T T E", id="roulette-title")
            yield Static("", id="roulette-balance")
            with Horizontal(classes="casino-row"):
                yield Input(value="100", id="roulette-bet", type="integer")
                yield Input(value="17", id="roulette-num", type="integer")
                yield Button("🎯 Miser sur le n°", id="roulette-number")
            with Horizontal(classes="casino-row"):
                for label, code in self.BETS[:5]:
                    yield Button(label, id=f"r-{code}")
            with Horizontal(classes="casino-row"):
                for label, code in self.BETS[5:]:
                    yield Button(label, id=f"r-{code}")
            with Horizontal(classes="casino-row"):
                yield Button("✖ Fermer", id="close")
            yield RichLog(id="roulette-log", markup=True, wrap=True)

    def on_mount(self) -> None:
        self.app.net_send(op="casino_state")
        self.refresh_balance()
        self.query_one("#roulette-log", RichLog).write(
            "[dim]Numéro plein ×35 · rouge/noir/pair/impair/bas/haut ×2 · douzaine ×3[/dim]"
        )

    def refresh_balance(self) -> None:
        self.query_one("#roulette-balance", Static).update(
            f"Solde : [b yellow]{self.app.balance}[/b yellow] jetons"
        )

    def _bet(self) -> int:
        try:
            return int(self.query_one("#roulette-bet", Input).value)
        except ValueError:
            return 0

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "close":
            self.dismiss(None)
        elif bid == "roulette-number":
            num = self.query_one("#roulette-num", Input).value.strip()
            self.app.net_send(op="casino_play", game="roulette", bet=self._bet(), choice=num)
        elif bid and bid.startswith("r-"):
            self.app.net_send(op="casino_play", game="roulette", bet=self._bet(), choice=bid[2:])

    def on_result(self, msg: dict) -> None:
        self.refresh_balance()
        log = self.query_one("#roulette-log", RichLog)
        delta = msg.get("delta", 0)
        detail = escape(str(msg.get("detail", "")))
        tag = "green" if msg.get("won") else "red"
        mark = "✔" if msg.get("won") else "✘"
        log.write(f"[{tag}]{mark}[/{tag}] {detail}   [b {tag}]{delta:+d}[/b {tag}] jetons")


class BlackjackScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Fermer")]

    def compose(self) -> ComposeResult:
        with Vertical(id="bj-box"):
            yield Static("🃏  B L A C K J A C K", id="bj-title")
            yield Static("", id="bj-balance")
            yield Static("Croupier :", id="bj-dealer")
            yield Static("Toi :", id="bj-player")
            yield Static("", id="bj-result")
            with Horizontal(classes="casino-row"):
                yield Input(value="100", id="bj-bet", type="integer")
                yield Button("🃏 Distribuer", variant="success", id="bj-deal")
            with Horizontal(classes="casino-row"):
                yield Button("➕ Tirer", id="bj-hit", disabled=True)
                yield Button("✋ Rester", id="bj-stand", disabled=True)
                yield Button("✖ Fermer", id="close")

    def on_mount(self) -> None:
        self.app.net_send(op="casino_state")
        self.refresh_balance()

    def refresh_balance(self) -> None:
        self.query_one("#bj-balance", Static).update(
            f"Solde : [b yellow]{self.app.balance}[/b yellow] jetons"
        )

    def _bet(self) -> int:
        try:
            return int(self.query_one("#bj-bet", Input).value)
        except ValueError:
            return 0

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "close":
            self.dismiss(None)
        elif bid == "bj-deal":
            self.app.net_send(op="bj_deal", bet=self._bet())
        elif bid == "bj-hit":
            self.app.net_send(op="bj_hit")
        elif bid == "bj-stand":
            self.app.net_send(op="bj_stand")

    def render_state(self, msg: dict) -> None:
        self.refresh_balance()
        dealer = "  ".join(msg.get("dealer", []))
        dval = msg.get("dealer_value")
        dtxt = f"  [b]({dval})[/b]" if dval is not None else ""
        self.query_one("#bj-dealer", Static).update(f"Croupier :  [red]{escape(dealer)}[/red]{dtxt}")
        player = "  ".join(msg.get("player", []))
        pval = msg.get("player_value")
        self.query_one("#bj-player", Static).update(
            f"Toi :  [cyan]{escape(player)}[/cyan]  [b]({pval})[/b]"
        )
        done = msg.get("done")
        self.query_one("#bj-hit", Button).disabled = done
        self.query_one("#bj-stand", Button).disabled = done
        self.query_one("#bj-deal", Button).disabled = not done
        result = self.query_one("#bj-result", Static)
        if done and msg.get("result"):
            delta = msg.get("delta", 0)
            tag = "green" if delta > 0 else ("red" if delta < 0 else "yellow")
            result.update(f"[b {tag}]{escape(msg['result'])}   ({delta:+d} jetons)[/b {tag}]")
        else:
            result.update("[dim]À toi de jouer : tire ou reste.[/dim]")


class OnlineScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Fermer")]

    REL_LABEL = {
        "friend": "[green]déjà ami[/green]",
        "outgoing": "[dim]demande envoyée[/dim]",
        "incoming": "[yellow]t'a ajouté — accepte ![/yellow]",
        "none": "",
    }

    def compose(self) -> ComposeResult:
        with Vertical(id="online-box"):
            yield Static("🌐  Qui est en ligne", id="online-title")
            yield ListView(id="online-list")
            with Horizontal(classes="modal-buttons"):
                yield Button("🔄 Rafraîchir", id="refresh")
                yield Button("Fermer", variant="primary", id="close")

    def on_mount(self) -> None:
        self.app.net_send(op="who_online")

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh":
            self.app.net_send(op="who_online")
        else:
            self.dismiss(None)

    async def show(self, users: list) -> None:
        lv = self.query_one("#online-list", ListView)
        await lv.clear()
        if not users:
            await lv.append(ListItem(Label("[dim]Personne d'autre en ligne.[/dim]")))
            return
        for u in users:
            rel = u.get("relation", "none")
            note = self.REL_LABEL.get(rel, "")
            action = "" if rel in ("friend", "outgoing") else "  [b cyan](Entrée = ajouter)[/b cyan]"
            item = ListItem(Label(f"[green]●[/green] [b]{escape(u['username'])}[/b]  {note}{action}"))
            item.online_user = u
            await lv.append(item)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        u = getattr(event.item, "online_user", None)
        if not u:
            return
        rel = u.get("relation", "none")
        if rel == "friend":
            self.app.notify(f"{u['username']} est déjà ton ami.")
        elif rel == "outgoing":
            self.app.notify("Demande déjà envoyée.")
        elif rel == "incoming":
            self.app.net_send(op="friend_accept", username=u["username"])
            self.app.notify(f"Tu as accepté {u['username']} !")
            self.dismiss(None)
        else:
            self.app.net_send(op="friend_add", username=u["username"])
            self.app.notify(f"Demande d'ami envoyée à {u['username']}.")
            self.dismiss(None)


# --- Écran principal ---------------------------------------------------------

class MainScreen(Screen):
    BINDINGS = [
        ("ctrl+a", "add_friend", "Ajouter un ami"),
        ("ctrl+o", "online", "🌐 En ligne"),
        ("ctrl+g", "new_group", "Nouveau groupe"),
        ("ctrl+j", "casino", "🎰 Casino"),
        ("ctrl+p", "leaderboard", "🏆 Classement"),
        ("ctrl+q", "quit", "Quitter"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="sidebar-pane"):
                yield Static(" Conversations", id="side-title")
                yield ListView(id="sidebar")
                with Horizontal(id="side-buttons"):
                    yield Button("➕ Ami", variant="primary", id="btn-add")
                    yield Button("🌐 En ligne", id="btn-online")
                with Horizontal(id="side-buttons2"):
                    yield Button("👥 Groupe", id="btn-group")
                    yield Button("🎰 Casino", variant="success", id="btn-casino")
                    yield Button("🏆", id="btn-board")
            with Vertical(id="chat-pane"):
                yield Static(" Choisis une conversation à gauche", id="chat-header")
                yield RichLog(id="log", wrap=True, markup=True, highlight=False)
                yield Input(placeholder="Écris un message puis Entrée…", id="composer")
        yield Footer()

    def on_mount(self) -> None:
        self.app.refresh_sidebar()

    def action_add_friend(self) -> None:
        self.app.push_screen(AddFriendModal(), self.app.on_add_friend)

    def action_new_group(self) -> None:
        self.app.push_screen(NewGroupModal(), self.app.on_new_group)

    def action_casino(self) -> None:
        self.app.open_casino()

    def action_leaderboard(self) -> None:
        self.app.open_leaderboard()

    def action_online(self) -> None:
        self.app.open_online()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-add":
            self.action_add_friend()
        elif event.button.id == "btn-online":
            self.action_online()
        elif event.button.id == "btn-group":
            self.action_new_group()
        elif event.button.id == "btn-casino":
            self.action_casino()
        elif event.button.id == "btn-board":
            self.action_leaderboard()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        convo = getattr(event.item, "convo", None)
        if convo is None:
            return
        if convo[0] == "req":
            self.app.net_send(op="friend_accept", username=convo[1])
        elif convo[0] in ("dm", "grp"):
            self.app.open_conversation(convo)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "composer":
            self.app.send_message(event.value)
            event.input.value = ""


# --- Application -------------------------------------------------------------

class PapoteApp(App):
    CSS = """
    LoginScreen { align: center middle; }
    #login-box { width: 62; height: auto; border: round $accent; padding: 1 2; background: $panel; }
    #logo { text-align: center; text-style: bold; color: $accent; }
    #subtitle { text-align: center; color: $text-muted; padding-bottom: 1; }
    #login-buttons { height: auto; align: center middle; padding-top: 1; }
    #status { text-align: center; color: $warning; padding-top: 1; }

    #body { height: 1fr; }
    #sidebar-pane { width: 34; border-right: solid $accent; }
    #side-title { background: $accent; color: $text; text-style: bold; }
    #sidebar { height: 1fr; background: $surface; }
    #side-buttons { height: auto; align: center middle; padding: 1 0 0 0; }
    #side-buttons2 { height: auto; align: center middle; padding: 1 0; }
    #chat-pane { width: 1fr; }
    #chat-header { background: $boost; text-style: bold; height: 1; }
    #log { height: 1fr; background: $surface; padding: 0 1; }
    #composer { dock: bottom; }

    AddFriendModal, NewGroupModal { align: center middle; }
    .modal { width: 56; height: auto; border: round $accent; padding: 1 2; background: $panel; }
    .modal-title { text-style: bold; color: $accent; padding-bottom: 1; }
    .modal-buttons { height: auto; align: center middle; padding-top: 1; }
    Button { margin: 0 1; }

    CasinoScreen, LeaderboardScreen, RouletteScreen, BlackjackScreen { align: center middle; }
    #casino-box { width: 72; height: auto; border: round $success; padding: 1 2; background: $panel; }
    #casino-title { text-align: center; text-style: bold; color: $success; }
    #casino-balance { text-align: center; padding-bottom: 1; }
    .casino-row { height: auto; align: center middle; padding-top: 1; }
    #casino-bet { width: 20; }
    #dice-num { width: 8; }
    #casino-log { height: 9; background: $surface; margin-top: 1; padding: 0 1; }
    #board-box { width: 72; height: auto; border: round $warning; padding: 1 2; background: $panel; }
    #board-title { text-align: center; text-style: bold; color: $warning; padding-bottom: 1; }
    #board-log { height: 16; background: $surface; padding: 0 1; }

    #roulette-box { width: 80; height: auto; border: round $error; padding: 1 2; background: $panel; }
    #roulette-title { text-align: center; text-style: bold; color: $error; }
    #roulette-balance { text-align: center; padding-bottom: 1; }
    #roulette-bet, #roulette-num { width: 12; }
    #roulette-log { height: 8; background: $surface; margin-top: 1; padding: 0 1; }

    #bj-box { width: 66; height: auto; border: round $success; padding: 1 2; background: $panel; }
    #bj-title { text-align: center; text-style: bold; color: $success; padding-bottom: 1; }
    #bj-balance { text-align: center; padding-bottom: 1; }
    #bj-dealer, #bj-player { padding: 1 0 0 0; }
    #bj-result { text-align: center; padding-top: 1; min-height: 1; }
    #bj-bet { width: 12; }

    OnlineScreen { align: center middle; }
    #online-box { width: 60; height: auto; border: round $accent; padding: 1 2; background: $panel; }
    #online-title { text-align: center; text-style: bold; color: $accent; padding-bottom: 1; }
    #online-list { height: 14; background: $surface; }
    """

    def __init__(self, server_arg=None):
        super().__init__()
        self.ws = None
        self.username = ""
        self.friends = []
        self.groups = []
        self.current = None
        self.unread = {}
        self._server_arg = server_arg
        self.login = None    # référence à l'écran de connexion
        self.main = None     # référence à l'écran principal
        self.balance = 0     # solde de jetons du casino
        self.casino = None   # référence à l'écran casino (si ouvert)
        self.board = None    # référence à l'écran classement (si ouvert)
        self.roulette = None       # écran roulette (si ouvert)
        self.blackjack = None      # écran blackjack (si ouvert)
        self.online_screen = None  # écran « qui est en ligne » (si ouvert)

    def on_mount(self) -> None:
        self.login = LoginScreen()
        self.push_screen(self.login)
        cfg = net.load_config()
        server = self._server_arg or cfg.get("server")
        if cfg.get("token") and server:
            self.do_auth(server, "", "", "token", token=cfg["token"])
        elif self._server_arg:
            self.call_after_refresh(self._prefill_server)

    def _prefill_server(self) -> None:
        try:
            self.login.query_one("#server", Input).value = self._server_arg
        except Exception:
            pass

    # --- authentification (worker asynchrone) ------------------------------

    @work(exclusive=True, group="auth")
    async def do_auth(self, server, username, password, mode, token="") -> None:
        url = normalize_url(server)
        try:
            ws = await net.open_connection(url)
        except Exception as e:  # noqa: BLE001 (on veut afficher n'importe quelle erreur)
            self._login_status(f"Connexion impossible : {e}")
            return
        try:
            reply = await net.auth(ws, mode, username, password, token)
        except Exception as e:  # noqa: BLE001
            self._login_status(f"Erreur : {e}")
            await ws.close()
            return
        if not reply.get("ok"):
            self._login_status(reply.get("error", "Échec de la connexion."))
            await ws.close()
            return

        self.ws = ws
        self.username = reply["username"]
        self.friends = reply.get("friends", [])
        self.groups = reply.get("groups", [])
        net.save_config(server=server, username=self.username, token=reply.get("token", ""))
        self.main = MainScreen()
        await self.switch_screen(self.main)
        self.refresh_sidebar()
        self.net_send(op="casino_state")
        self._receiver()

    def _login_status(self, text: str) -> None:
        try:
            self.login.query_one("#status", Static).update(text)
        except Exception:
            self.notify(text, severity="error")

    # --- réception des messages du serveur ---------------------------------

    @work(group="receiver")
    async def _receiver(self) -> None:
        try:
            async for raw in self.ws:
                self.handle(protocol.decode(raw))
        except Exception:  # noqa: BLE001
            pass
        if self.ws is not None:
            self.notify("Déconnecté du serveur.", severity="error")

    async def on_unmount(self) -> None:
        ws, self.ws = self.ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    def handle(self, msg: dict) -> None:
        if "ev" in msg:
            self._on_event(msg)
        elif "reply" in msg:
            self._on_reply(msg)

    def _on_event(self, msg: dict) -> None:
        ev = msg["ev"]
        if ev == "message":
            self._incoming_message(msg["msg"])
        elif ev == "friend_request":
            self.notify(f"Demande d'ami de {msg['from']}")
            self.net_send(op="friend_list")
        elif ev == "friend_accepted":
            self.notify(f"{msg['username']} a accepté ta demande !")
            self.net_send(op="friend_list")
        elif ev == "group_added":
            g = msg["group"]
            self.groups = [x for x in self.groups if x["id"] != g["id"]] + [g]
            self.notify(f"Ajouté au groupe « {g['name']} »")
            self.refresh_sidebar()
        elif ev == "presence":
            for f in self.friends:
                if f["username"] == msg["username"]:
                    f["online"] = msg["online"]
            self.refresh_sidebar()

    def _on_reply(self, msg: dict) -> None:
        reply = msg.get("reply")
        if not msg.get("ok"):
            self.notify(msg.get("error", "Erreur."), severity="error")
            return
        if reply in ("friend_list", "friend_add", "friend_accept"):
            if "friends" in msg:
                self.friends = msg["friends"]
                self.refresh_sidebar()
        elif reply == "group_create":
            self.groups = msg.get("groups", self.groups)
            self.refresh_sidebar()
        elif reply == "history":
            key = ("dm", msg["with"]) if msg["with_type"] == "dm" else ("grp", msg["with"])
            if key == self.current and self.main:
                log = self.main.query_one("#log", RichLog)
                log.clear()
                for m in msg["messages"]:
                    self._write_message(m)
        elif reply == "casino_state":
            self.balance = msg.get("balance", self.balance)
            if self.casino:
                self.casino.refresh_balance()
        elif reply == "casino_play":
            self.balance = msg.get("balance", self.balance)
            if msg.get("game") == "roulette" and self.roulette:
                self.roulette.on_result(msg)
            elif self.casino:
                self.casino.on_result(msg)
        elif reply == "casino_bonus":
            self.balance = msg.get("balance", self.balance)
            if self.casino:
                self.casino.on_bonus()
        elif reply == "blackjack":
            self.balance = msg.get("balance", self.balance)
            if self.blackjack:
                self.blackjack.render_state(msg)
        elif reply == "leaderboard":
            if self.board:
                self.board.show(msg.get("players", []))
        elif reply == "who_online":
            if self.online_screen:
                self.run_worker(self.online_screen.show(msg.get("users", [])),
                                exclusive=True, group="online")

    def _incoming_message(self, m: dict) -> None:
        if m["to_type"] == "dm":
            partner = m["to"] if m["from"] == self.username else m["from"]
            key = ("dm", partner)
        else:
            key = ("grp", m["to"])
        if key == self.current:
            self._write_message(m)
        else:
            self.unread[key] = self.unread.get(key, 0) + 1
            self.refresh_sidebar()

    def _write_message(self, m: dict) -> None:
        if not self.main:
            return
        try:
            log = self.main.query_one("#log", RichLog)
        except Exception:
            return
        ts = time.strftime("%H:%M", time.localtime(m["ts"]))
        who = m["from"]
        style = "cyan" if who == self.username else "green"
        log.write(f"[dim]{ts}[/dim] [b {style}]{escape(who)}[/b {style}] : {escape(m['body'])}")

    # --- envoi / navigation ------------------------------------------------

    def net_send(self, **payload) -> None:
        if self.ws is not None:
            self.run_worker(self.ws.send(protocol.encode(payload)),
                            exclusive=False, group="net")

    def send_message(self, text: str) -> None:
        if not text.strip() or self.current is None:
            return
        kind, target = self.current
        to_type = "dm" if kind == "dm" else "group"
        self.net_send(op="send", to_type=to_type, to=target, body=text)

    def open_conversation(self, convo) -> None:
        self.current = convo
        self.unread.pop(convo, None)
        if not self.main:
            return
        header = self.main.query_one("#chat-header", Static)
        self.main.query_one("#log", RichLog).clear()
        if convo[0] == "dm":
            header.update(f" 💬  {convo[1]}")
            self.net_send(op="history", with_type="dm", **{"with": convo[1]})
        else:
            g = next((x for x in self.groups if x["id"] == convo[1]), None)
            name = g["name"] if g else str(convo[1])
            members = ", ".join(g["members"]) if g else ""
            header.update(f" 👥  {name}   [dim]({members})[/dim]")
            self.net_send(op="history", with_type="group", **{"with": convo[1]})
        self.refresh_sidebar()
        self.main.query_one("#composer", Input).focus()

    def on_add_friend(self, username) -> None:
        if username:
            self.net_send(op="friend_add", username=username)

    def on_new_group(self, result) -> None:
        if result:
            name, members = result
            if name:
                self.net_send(op="group_create", name=name, members=members)

    # --- casino -------------------------------------------------------------

    def open_casino(self) -> None:
        if not self.main:
            return
        self.casino = CasinoScreen()
        self.push_screen(self.casino, lambda _=None: setattr(self, "casino", None))

    def open_leaderboard(self) -> None:
        self.board = LeaderboardScreen()
        self.push_screen(self.board, lambda _=None: setattr(self, "board", None))
        self.net_send(op="leaderboard")

    def open_roulette(self) -> None:
        if not self.main:
            return
        self.roulette = RouletteScreen()
        self.push_screen(self.roulette, lambda _=None: setattr(self, "roulette", None))

    def open_blackjack(self) -> None:
        if not self.main:
            return
        self.blackjack = BlackjackScreen()
        self.push_screen(self.blackjack, lambda _=None: setattr(self, "blackjack", None))

    def open_online(self) -> None:
        if not self.main:
            return
        self.online_screen = OnlineScreen()
        self.push_screen(self.online_screen, lambda _=None: setattr(self, "online_screen", None))

    # --- barre latérale -----------------------------------------------------

    def refresh_sidebar(self) -> None:
        self.run_worker(self._refresh_sidebar(), exclusive=True, group="sidebar")

    async def _refresh_sidebar(self) -> None:
        if not self.main:
            return
        try:
            lv = self.main.query_one("#sidebar", ListView)
        except Exception:
            return
        await lv.clear()
        rows = []
        for f in self.friends:
            if f["kind"] == "incoming":
                rows.append((f"📨 [b]{escape(f['username'])}[/b] — accepter", ("req", f["username"])))
        for f in self.friends:
            if f["kind"] == "friend":
                dot = "[green]●[/green]" if f.get("online") else "[dim]○[/dim]"
                key = ("dm", f["username"])
                u = self.unread.get(key, 0)
                badge = f"  [b yellow]({u})[/b yellow]" if u else ""
                rows.append((f"{dot} {escape(f['username'])}{badge}", key))
        for f in self.friends:
            if f["kind"] == "outgoing":
                rows.append((f"[dim]⏳ {escape(f['username'])} (en attente)[/dim]", ("out", f["username"])))
        for g in self.groups:
            key = ("grp", g["id"])
            u = self.unread.get(key, 0)
            badge = f"  [b yellow]({u})[/b yellow]" if u else ""
            rows.append((f"👥 {escape(g['name'])}{badge}", key))
        for label, convo in rows:
            item = ListItem(Label(label))
            item.convo = convo
            await lv.append(item)


def main() -> None:
    ap = argparse.ArgumentParser(prog="papote", description="Messagerie de terminal papote")
    ap.add_argument("--server", default=None, help="URL du serveur (ex: wss://…)")
    args = ap.parse_args()
    PapoteApp(server_arg=args.server).run()


if __name__ == "__main__":
    main()
