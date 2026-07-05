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
    if "://" in s:
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


# --- Écran principal ---------------------------------------------------------

class MainScreen(Screen):
    BINDINGS = [
        ("ctrl+a", "add_friend", "Ajouter un ami"),
        ("ctrl+g", "new_group", "Nouveau groupe"),
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
                    yield Button("👥 Groupe", id="btn-group")
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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-add":
            self.action_add_friend()
        elif event.button.id == "btn-group":
            self.action_new_group()

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
    #side-buttons { height: auto; align: center middle; padding: 1 0; }
    #chat-pane { width: 1fr; }
    #chat-header { background: $boost; text-style: bold; height: 1; }
    #log { height: 1fr; background: $surface; padding: 0 1; }
    #composer { dock: bottom; }

    AddFriendModal, NewGroupModal { align: center middle; }
    .modal { width: 56; height: auto; border: round $accent; padding: 1 2; background: $panel; }
    .modal-title { text-style: bold; color: $accent; padding-bottom: 1; }
    .modal-buttons { height: auto; align: center middle; padding-top: 1; }
    Button { margin: 0 1; }
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
        self.login = None   # référence à l'écran de connexion
        self.main = None    # référence à l'écran principal

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
