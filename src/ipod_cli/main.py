"""Ponto de entrada principal da CLI ipod-sync."""

from typing import Optional

import typer

from ipod_cli import __version__
from ipod_cli.ui import console, print_banner, print_error, print_info, print_success

app = typer.Typer(
    name="ipod",
    help="iPod Sync CLI: Gerencie e sincronize seu iPod (Classic, Video, Mini, Nano) no terminal.",
    add_completion=False,
    no_args_is_help=True,
)


def version_callback(value: bool) -> None:
    if value:
        console.print(f"[bold cyan]ipod-sync[/bold cyan] versão [bold green]{__version__}[/bold green]")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        help="Exibe a versão do ipod-sync e encerra.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """Ferramenta CLI moderna para gerenciar iPods sem iTunes."""
    pass


@app.command(name="detect")
def detect_cmd() -> None:
    """Detecta automaticamente os iPods conectados ao computador."""
    print_banner()
    print_info("Procurando iPods conectados...")
    # Será expandido na Fase 2
    console.print("[dim]Comando em implementação para a Fase 2.[/dim]")


@app.command(name="info")
def info_cmd(
    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="Letra da unidade ou ponto de montagem do iPod (ex: 'E:\\' ou '/media/ipod').",
    ),
) -> None:
    """Exibe informações detalhadas de hardware e do banco de dados do iPod."""
    print_banner()
    print_info(f"Obtendo informações do iPod (mount: {mount_point or 'automático'})...")
    console.print("[dim]Comando em implementação para a Fase 2.[/dim]")


if __name__ == "__main__":
    app()
