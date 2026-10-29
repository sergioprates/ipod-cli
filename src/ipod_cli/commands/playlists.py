"""Comando para consulta e listagem de playlists do iPod."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from ipod_cli.commands.tracks import _load_library
from ipod_cli.ui import console, print_banner, print_info, print_warning

app = typer.Typer(name="playlists", help="Consulta e gerencia playlists no iPod.")


def list_playlists(
    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="Letra ou caminho de montagem do iPod (ex: 'D:\\').",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Gera saída em JSON estruturado.",
    ),
) -> None:
    """Lista as playlists existentes na biblioteca do iPod."""


    mount_path, library = _load_library(mount_point)
    playlists = library.snapshot.playlists

    if as_json:
        data = [
            {
                "id": p.playlist_id,
                "name": p.name,
                "kind": p.kind.value,
                "tracks_count": len(p.entries),
                "is_smart": p.smart is not None,
                "system_managed": p.system_managed,
            }
            for p in playlists
        ]
        console.print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    print_banner()
    print_info(f"Playlists encontradas em [bold]{mount_path}[/bold] ({len(playlists)} no total):")

    if not playlists:
        print_warning("Nenhuma playlist encontrada na biblioteca.")
        return

    table = Table(title="Playlists no iPod", border_style="cyan")
    table.add_column("ID", justify="right", style="dim")
    table.add_column("Nome da Playlist", style="bold white")
    table.add_column("Tipo", style="cyan")
    table.add_column("Músicas", justify="right", style="magenta")

    for p in playlists:
        tipo = p.kind.value.capitalize()
        if p.smart:
            tipo += " (Smart)"
        if p.system_managed:
            tipo += " [dim](Sistema)[/dim]"

        table.add_row(
            str(p.playlist_id),
            p.name or "[Sem Nome]",
            tipo,
            str(len(p.entries)),
        )

    console.print(table)
