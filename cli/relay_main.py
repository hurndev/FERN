from __future__ import annotations

import asyncio
import logging

import click

from fern.crypto.keys import Keypair
from fern.transport.websocket_server import RelayServer


def _display_relay_url(host: str, port: int) -> str:
    display_host = "localhost" if host in {"0.0.0.0", "::"} else host
    return f"ws://{display_host}:{port}"


class _ColorFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    RED = "\033[31m"

    EVENT_TYPE_COLORS = {
        "genesis": MAGENTA,
        "join": GREEN,
        "leave": DIM,
        "invite": YELLOW,
        "kick": RED,
        "ban": RED,
        "unban": GREEN,
        "mod_add": MAGENTA,
        "mod_remove": MAGENTA,
        "relay_update": CYAN,
        "metadata_update": CYAN,
    }

    def format(self, record):
        color = self.COLORS.get(record.levelname, "")
        time_str = self.formatTime(record, "%H:%M:%S")
        level = f"{color}{record.levelname:<7}{self.RESET}"
        name = f"{self.DIM}{record.name}{self.RESET}"
        msg = self._colorize(record.getMessage())
        return f"{self.DIM}{time_str}{self.RESET} {level} {name}: {msg}"

    def _colorize(self, msg):
        msg = msg.replace("type=", f"{self.DIM}type={self.RESET}")
        for etype, color in self.EVENT_TYPE_COLORS.items():
            msg = msg.replace(f"type={etype}", f"type={color}{etype}{self.RESET}")
        for word in ["genesis", "chat.message", "chat.reaction", "chat.nickname_set"]:
            if word in self.EVENT_TYPE_COLORS:
                color = self.EVENT_TYPE_COLORS[word]
            else:
                color = self.BLUE
            msg = msg.replace(f"type={word}", f"type={color}{word}{self.RESET}")
        for word in ["metadata", "not_found", "invalid JSON"]:
            msg = msg.replace(word, f"{self.YELLOW}{word}{self.RESET}")
        msg = msg.replace("auto-hosting", f"{self.MAGENTA}auto-hosting{self.RESET}")
        msg = msg.replace("broadcast", f"{self.CYAN}broadcast{self.RESET}")
        msg = msg.replace("rejecting", f"{self.RED}rejecting{self.RESET}")
        msg = msg.replace("fraud proof", f"{self.RED}fraud proof{self.RESET}")
        return msg


@click.command()
@click.option("--host", default="0.0.0.0", help="Bind address")
@click.option("--port", default=8765, help="Port to listen on")
@click.option("--name", default="FERN Relay", help="Relay name")
@click.option("--store", default="relay.db", help="SQLite store path")
@click.option("--log-level", default="INFO", help="Log level (DEBUG/INFO/WARNING/ERROR)")
@click.option("--no-color", is_flag=True, help="Disable coloured log output")
def main_fn(host: str, port: int, name: str, store: str, log_level: str, no_color: bool) -> None:
    handler = logging.StreamHandler()
    if no_color:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
        )
    else:
        handler.setFormatter(_ColorFormatter())
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        handlers=[handler],
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)

    BOLD = "\033[1m" if not no_color else ""
    RESET = "\033[0m" if not no_color else ""
    CYAN = "\033[36m" if not no_color else ""
    MAGENTA = "\033[35m" if not no_color else ""
    GREEN = "\033[32m" if not no_color else ""

    click.echo(f"{BOLD}{MAGENTA}Starting FERN relay{RESET} on {host}:{port}")
    click.echo(f"  {CYAN}Name:{RESET}     {name}")
    click.echo(f"  {CYAN}Address:{RESET}  {GREEN}{_display_relay_url(host, port)}{RESET}")
    click.echo(f"  {CYAN}Store:{RESET}    {store}")
    click.echo(f"  {CYAN}Log level:{RESET} {log_level.upper()}")

    keypair = Keypair.generate()
    click.echo(f"  {CYAN}Pubkey:{RESET}    {GREEN}{keypair.pubkey_hex}{RESET}")

    server = RelayServer(
        host=host,
        port=port,
        name=name,
        relay_keypair=keypair,
        store_path=store,
    )

    asyncio.run(server.start())


def main() -> None:
    main_fn()
