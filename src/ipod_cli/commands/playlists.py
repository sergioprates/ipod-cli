"""Command for querying and listing playlists on the iPod."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from ipod_cli.commands.tracks import _load_library
from ipod_cli.ui import console, print_banner, print_info, print_warning

app = typer.Typer(name="playlists", help="Query and list playlists on the iPod.")


def list_playlists(
    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="iPod mount point or drive letter (e.g. 'D:\\').",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Output playlists as structured JSON.",
    ),
) -> None:
    """List playlists existing in the iPod library."""
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
    clean_mount = str(mount_path).rstrip("/\\")
    print_info(f"Playlists found on [bold]{clean_mount}[/bold] ({len(playlists)} total):")

    if not playlists:
        print_warning("No playlists found in the library.")
        return

    table = Table(title="iPod Playlists", border_style="cyan")
    table.add_column("ID", justify="right", style="dim")
    table.add_column("Playlist Name", style="bold white")
    table.add_column("Type", style="cyan")
    table.add_column("Tracks", justify="right", style="magenta")

    for p in playlists:
        kind_str = p.kind.value.capitalize()
        if p.smart:
            kind_str += " (Smart)"
        if p.system_managed:
            kind_str += " [dim](System)[/dim]"

        table.add_row(
            str(p.playlist_id),
            p.name or "[Untitled]",
            kind_str,
            str(len(p.entries)),
        )

    console.print(table)
