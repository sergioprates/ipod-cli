"""Terminal UI utilities powered by the Rich library."""

import sys
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Ensure proper UTF-8 support on Windows terminals (cp1252 / cmd / powershell)
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

console = Console(force_terminal=True if sys.stdout.isatty() else None)
error_console = Console(stderr=True, force_terminal=True if sys.stderr.isatty() else None)


def print_banner() -> None:
    """Print the styled ipod-sync header banner."""
    text = (
        "[bold cyan]iPod Sync CLI[/bold cyan] [dim]v0.1.0[/dim]\n"
        "[italic white]High-performance iPod management without iTunes[/italic white]"
    )
    console.print(Panel(text, border_style="cyan", expand=False))


def print_success(message: str) -> None:
    console.print(f"[bold green][+][/bold green] {message}")


def print_warning(message: str) -> None:
    console.print(f"[bold yellow][!][/bold yellow] {message}")


def print_error(message: str) -> None:
    error_console.print(f"[bold red][x][/bold red] {message}")


def print_info(message: str) -> None:
    console.print(f"[bold cyan][*][/bold cyan] {message}")
