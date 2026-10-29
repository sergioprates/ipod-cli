"""Command for querying and listing tracks in the iPod library."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from ipod_cli.device import discover_ipod_mounts, inspect_ipod, is_ipod_mount
from ipod_cli.ui import console, print_banner, print_error, print_info, print_warning
from iPodDB.library import IPodLibrary

app = typer.Typer(name="tracks", help="Query and list tracks from the iPod library.")


def _format_duration(ms: int) -> str:
    """Format duration from milliseconds to MM:SS."""
    seconds = ms // 1000
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"


def _load_library(mount_point: Optional[str]) -> tuple[Path, IPodLibrary]:
    """Discover the iPod mount point and load the IPodLibrary instance."""
    target_mount: Optional[Path] = None

    if mount_point:
        p = Path(mount_point)
        if not p.exists() or not is_ipod_mount(p):
            print_error(f"The specified path is not a valid iPod mount: '{mount_point}'")
            raise typer.Exit(1)
        target_mount = p
    else:
        mounts = discover_ipod_mounts()
        if not mounts:
            print_error("No iPod detected automatically.")
            console.print("[dim]Use [bold]--mount <path>[/bold] (e.g. -m D:\\) to specify the iPod drive.[/dim]")
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
        print_error(f"Database 'iTunesDB' not found on '{target_mount}'.")
        raise typer.Exit(1)

    try:
        db_bytes = itunesdb_path.read_bytes()
        library = IPodLibrary(db_bytes)
        return target_mount, library
    except Exception as e:
        print_error(f"Failed to parse iPod library: {e}")
        raise typer.Exit(1)


def list_tracks(
    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="iPod mount point or drive letter (e.g. 'D:\\').",
    ),
    search: Optional[str] = typer.Option(
        None,
        "--search",
        "-s",
        help="Filter tracks by free text search (title, artist, or album).",
    ),
    artist: Optional[str] = typer.Option(
        None,
        "--artist",
        "-a",
        help="Filter tracks by artist name.",
    ),
    album: Optional[str] = typer.Option(
        None,
        "--album",
        "-b",
        help="Filter tracks by album name.",
    ),
    limit: int = typer.Option(
        30,
        "--limit",
        "-n",
        help="Maximum number of tracks to display (use 0 for all).",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Output tracks as structured JSON.",
    ),
) -> None:
    """List tracks in the iPod database with filtering options."""
    mount_path, library = _load_library(mount_point)
    all_tracks = library.snapshot.tracks

    # Filters
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
    clean_mount = str(mount_path).rstrip("/\\")
    print_info(f"Library on [bold]{clean_mount}[/bold] ({len(all_tracks)} tracks total):")

    if not displayed:
        print_warning("No tracks matched the applied filters.")
        return

    table = Table(title=f"iPod Tracks ({len(displayed)} of {total_matches} shown)", border_style="cyan")
    table.add_column("ID", justify="right", style="dim")
    table.add_column("Title", style="bold white", no_wrap=True)
    table.add_column("Artist", style="bold cyan")
    table.add_column("Album", style="italic")
    table.add_column("Duration", justify="right", style="green")
    table.add_column("Year", justify="right", style="dim")
    table.add_column("Bitrate", justify="right", style="magenta")

    for t in displayed:
        table.add_row(
            str(t.track_id),
            t.title or "[Untitled]",
            t.artist or "[Unknown]",
            t.album or "-",
            _format_duration(t.length_ms),
            str(t.year) if t.year else "-",
            f"{t.bitrate_kbps}k" if t.bitrate_kbps else "-",
        )

    console.print(table)
    if limit > 0 and total_matches > limit:
        console.print(f"[dim]Showing first {limit} tracks. Use [bold]--limit 0[/bold] to display all {total_matches}.[/dim]\n")
