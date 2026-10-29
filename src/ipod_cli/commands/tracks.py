"""Comando para consulta e listagem de faixas da biblioteca do iPod."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from ipod_cli.device import discover_ipod_mounts, inspect_ipod, is_ipod_mount
from ipod_cli.ui import console, print_banner, print_error, print_info, print_warning
from iPodDB.library import IPodLibrary

app = typer.Typer(name="tracks", help="Consulta e gerencia músicas da biblioteca do iPod.")


def _format_duration(ms: int) -> str:
    """Converte milissegundos para formato MM:SS."""
    seconds = ms // 1000
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"


def _load_library(mount_point: Optional[str]) -> tuple[Path, IPodLibrary]:
    """Descobre o iPod e carrega o objeto IPodLibrary."""
    target_mount: Optional[Path] = None

    if mount_point:
        p = Path(mount_point)
        if not p.exists() or not is_ipod_mount(p):
            print_error(f"O caminho especificado não é um iPod válido: '{mount_point}'")
            raise typer.Exit(1)
        target_mount = p
    else:
        mounts = discover_ipod_mounts()
        if not mounts:
            print_error("Nenhum iPod detectado automaticamente.")
            console.print("[dim]Use [bold]--mount <caminho>[/bold] (ex: -m D:\\) para informar o ponto de montagem.[/dim]")
            raise typer.Exit(1)
        target_mount = mounts[0]

    itunesdb_path = None
    for cand in (
        target_mount / "iPod_Control" / "iTunes" / "iTunesDB",
        target_mount / "ipod_control" / "itunes" / "itunesdb",
        target_mount / "iPod_Control" / "iTunes" / "iTunesCDB",
    ):
        if cand.exists():
            itunesdb_path = cand
            break

    if not itunesdb_path:
        print_error(f"Banco de dados 'iTunesDB' não encontrado em '{target_mount}'.")
        raise typer.Exit(1)

    try:
        db_bytes = itunesdb_path.read_bytes()
        library = IPodLibrary(db_bytes)
        return target_mount, library
    except Exception as e:
        print_error(f"Erro ao analisar a biblioteca do iPod: {e}")
        raise typer.Exit(1)


def list_tracks(
    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="Letra ou caminho de montagem do iPod (ex: 'D:\\').",
    ),

    search: Optional[str] = typer.Option(
        None,
        "--search",
        "-s",
        help="Filtra faixas por busca de texto (título, artista ou álbum).",
    ),
    artist: Optional[str] = typer.Option(
        None,
        "--artist",
        "-a",
        help="Filtra faixas pelo nome do artista.",
    ),
    album: Optional[str] = typer.Option(
        None,
        "--album",
        "-b",
        help="Filtra faixas pelo nome do álbum.",
    ),
    limit: int = typer.Option(
        30,
        "--limit",
        "-n",
        help="Número máximo de faixas a exibir (use 0 para listar todas).",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Gera saída em JSON estruturado.",
    ),
) -> None:
    """Lista as músicas presentes no banco de dados do iPod com opções de filtro."""
    mount_path, library = _load_library(mount_point)
    all_tracks = library.snapshot.tracks

    # Filtros
    filtered = all_tracks
    if search:
        q = search.lower()
        filtered = [
            t for t in filtered
            if q in (t.title or "").lower() or q in (t.artist or "").lower() or q in (t.album or "").lower()
        ]
    if artist:
        q = artist.lower()
        filtered = [t for t in filtered if q in (t.artist or "").lower()]
    if album:
        q = album.lower()
        filtered = [t for t in filtered if q in (t.album or "").lower()]

    total_matches = len(filtered)
    if limit > 0:
        displayed = filtered[:limit]
    else:
        displayed = filtered

    if as_json:
        data = [
            {
                "id": t.track_id,
                "title": t.title,
                "artist": t.artist,
                "album": t.album,
                "genre": t.genre,
                "year": t.year,
                "track_number": t.track_number,
                "duration_ms": t.length_ms,
                "bitrate_kbps": t.bitrate_kbps,
                "location": t.metadata.location,
            }
            for t in displayed
        ]
        console.print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    print_banner()
    print_info(f"Biblioteca de [bold]{mount_path}[/bold] ({len(all_tracks)} faixas no total):")

    if not displayed:
        print_warning("Nenhuma faixa correspondeu aos filtros aplicados.")
        return

    table = Table(title=f"Músicas no iPod ({len(displayed)} de {total_matches} encontradas)", border_style="cyan")
    table.add_column("ID", justify="right", style="dim")
    table.add_column("Título", style="bold white", no_wrap=True)
    table.add_column("Artista", style="bold cyan")
    table.add_column("Álbum", style="italic")
    table.add_column("Duração", justify="right", style="green")
    table.add_column("Ano", justify="right", style="dim")
    table.add_column("Taxa", justify="right", style="magenta")

    for t in displayed:
        table.add_row(
            str(t.track_id),
            t.title or "[Sem Título]",
            t.artist or "[Desconhecido]",
            t.album or "-",
            _format_duration(t.length_ms),
            str(t.year) if t.year else "-",
            f"{t.bitrate_kbps}k" if t.bitrate_kbps else "-",
        )

    console.print(table)
    if limit > 0 and total_matches > limit:
        console.print(f"[dim]Exibindo as primeiras {limit} músicas. Use [bold]--limit 0[/bold] para exibir todas as {total_matches}.[/dim]\n")
