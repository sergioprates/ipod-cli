"""Commands for querying, creating, modifying, and deleting iPod playlists."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import List, Optional

import typer
from rich.prompt import Confirm
from rich.table import Table

from iPodDB.library.database import IPodLibrary
from iPodDB.library.models import Track
from iPodDB.library.playlists import Playlist, PlaylistEntry, PlaylistKind, playlist_entries
from iPodDB.library.writing import WriteResources
from ipod_cli.commands.tracks import _format_duration, _load_library
from ipod_cli.storage_ops import commit_transaction
from ipod_cli.ui import console, print_banner, print_error, print_info, print_success, print_warning

app = typer.Typer(
    name="playlists",
    help="Query, create, inspect, edit, and delete iPod playlists.",
    no_args_is_help=False,
)


def _find_playlist(playlists: tuple[Playlist, ...], name_or_id: str) -> Optional[Playlist]:
    """Find a playlist by integer ID or case-insensitive name."""
    try:
        pid = int(name_or_id)
        for p in playlists:
            if p.playlist_id == pid:
                return p
    except ValueError:
        pass

    target_norm = name_or_id.strip().casefold()
    exact = [p for p in playlists if (p.name or "").casefold() == target_norm]
    if exact:
        return exact[0]

    partial = [p for p in playlists if target_norm in (p.name or "").casefold()]
    if len(partial) == 1:
        return partial[0]

    return None


def _resolve_tracks(
    library: IPodLibrary,
    track_ids: Optional[List[int]] = None,
    artist: Optional[str] = None,
    album: Optional[str] = None,
    search: Optional[str] = None,
) -> list[Track]:
    """Collect tracks matching specific IDs or search filters."""
    all_tracks = library.snapshot.tracks
    matched: list[Track] = []

    id_set = set(track_ids) if track_ids else set()

    for t in all_tracks:
        if id_set and t.track_id in id_set:
            matched.append(t)
            continue

        if artist and artist.casefold() not in (t.artist or "").casefold():
            continue
        if album and album.casefold() not in (t.album or "").casefold():
            continue
        if search:
            q = search.casefold()
            fields = f"{t.title} {t.artist} {t.album}".casefold()
            if q not in fields:
                continue

        if artist or album or search:
            matched.append(t)

    return matched


def _commit_playlist_plan(
    mount_path: Path,
    library: IPodLibrary,
    desired_snapshot,
    action_desc: str,
    delete_omissions: bool = False,
    dry_run: bool = False,
) -> None:
    """Analyze, prepare, and optionally commit a playlist update transaction."""
    draft = library.begin_draft(desired_snapshot, delete_omissions=delete_omissions)
    plan = library.analyze(draft)

    if plan.blocked:
        print_error(f"Cannot perform {action_desc}: Operation blocked by database analysis.")
        for issue in plan.issues:
            console.print(f"  [red]• {issue.code}[/red]: {issue.message} ({issue.detail or ''})")
        raise typer.Exit(1)

    resources = WriteResources(pending_playback_sidecars=False)
    prep = library.prepare(plan, resources)

    if prep.issues:
        print_error(f"Playlist preparation failed with {len(prep.issues)} issue(s):")
        for issue in prep.issues:
            console.print(f"  [red]• {issue.code}[/red]: {issue.message} ({issue.detail or ''})")
        raise typer.Exit(1)

    if prep.prepared is None:
        print_error("Prepared library returned no output data.")
        raise typer.Exit(1)

    if dry_run:
        print_info(f"[yellow]Simulation (--dry-run):[/yellow] {action_desc} validated successfully. No disk changes made.")
        return

    commit_transaction(
        mount_path,
        prep.prepared.itunes,
        files_to_copy=[],
        files_to_delete=[],
    )
    print_success(f"Success! {action_desc} committed to iPod database.")


@app.callback(invoke_without_command=True)
def default_callback(
    ctx: typer.Context,
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
    """Query, create, inspect, edit, and delete iPod playlists."""
    if ctx.invoked_subcommand is None:
        list_playlists(mount_point=mount_point, as_json=as_json)


@app.command(name="list")
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


@app.command(name="show")
def show_playlist(
    name_or_id: str = typer.Argument(..., help="Playlist name or integer ID."),
    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="iPod mount point or drive letter.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Output playlist details and tracks as JSON.",
    ),
) -> None:
    """Show details and track listing of a specific playlist."""
    mount_path, library = _load_library(mount_point)
    snapshot = library.snapshot

    playlist = _find_playlist(snapshot.playlists, name_or_id)
    if not playlist:
        print_error(f"Playlist '{name_or_id}' not found.")
        raise typer.Exit(1)

    tracks_by_id = {t.track_id: t for t in snapshot.tracks}
    playlist_tracks: list[Track] = []
    for entry in playlist.entries:
        if entry.track_id in tracks_by_id:
            playlist_tracks.append(tracks_by_id[entry.track_id])

    if as_json:
        data = {
            "id": playlist.playlist_id,
            "name": playlist.name,
            "kind": playlist.kind.value,
            "system_managed": playlist.system_managed,
            "tracks_count": len(playlist_tracks),
            "tracks": [
                {
                    "id": t.track_id,
                    "title": t.title,
                    "artist": t.artist,
                    "album": t.album,
                    "duration_ms": t.length_ms,
                    "location": t.metadata.location,
                }
                for t in playlist_tracks
            ],
        }
        console.print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    print_banner()
    print_info(f"Playlist: [bold white]{playlist.name}[/bold white] (ID: {playlist.playlist_id})")
    print_info(f"Type: {playlist.kind.value.capitalize()} | Tracks: {len(playlist_tracks)}")

    if not playlist_tracks:
        print_warning("This playlist contains no tracks.")
        return

    table = Table(title=f"Tracks in '{playlist.name}'", border_style="cyan")
    table.add_column("#", justify="right", style="dim")
    table.add_column("ID", justify="right", style="dim")
    table.add_column("Title", style="bold white")
    table.add_column("Artist", style="cyan")
    table.add_column("Album", style="green")
    table.add_column("Time", justify="right", style="magenta")

    for idx, track in enumerate(playlist_tracks, start=1):
        table.add_row(
            str(idx),
            str(track.track_id),
            track.title or "[Unknown Title]",
            track.artist or "[Unknown Artist]",
            track.album or "[Unknown Album]",
            _format_duration(track.length_ms),
        )

    console.print(table)


@app.command(name="create")
def create_playlist(
    name: str = typer.Argument(..., help="Name of the new playlist."),
    track_ids: Optional[List[int]] = typer.Argument(None, help="Optional track IDs to add."),
    artist: Optional[str] = typer.Option(None, "--artist", help="Filter tracks by artist."),
    album: Optional[str] = typer.Option(None, "--album", help="Filter tracks by album."),
    search: Optional[str] = typer.Option(None, "--search", help="Filter tracks by general search term."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate creation without writing to iPod."),
    mount_point: Optional[str] = typer.Option(None, "--mount", "-m", help="iPod mount point."),
) -> None:
    """Create a new playlist and optionally add matching tracks to it."""
    mount_path, library = _load_library(mount_point)
    snapshot = library.snapshot

    # Check for name collision
    clean_name = name.strip()
    if not clean_name:
        print_error("Playlist name cannot be empty.")
        raise typer.Exit(1)

    existing = [p for p in snapshot.playlists if (p.name or "").casefold() == clean_name.casefold()]
    if existing:
        print_error(f"A playlist named '{clean_name}' already exists.")
        raise typer.Exit(1)

    # Collect initial tracks
    selected_tracks = _resolve_tracks(library, track_ids=track_ids, artist=artist, album=album, search=search)
    entries = playlist_entries([t.track_id for t in selected_tracks])

    new_pl = Playlist(
        playlist_id=-1,
        name=clean_name,
        kind=PlaylistKind.PLAYLIST,
        entries=entries,
    )

    print_banner()
    print_info(f"Creating playlist [bold white]'{clean_name}'[/bold white] with {len(entries)} track(s)...")

    desired = replace(snapshot, playlists=(*snapshot.playlists, new_pl))
    _commit_playlist_plan(
        mount_path,
        library,
        desired,
        action_desc=f"Playlist '{clean_name}' creation",
        delete_omissions=False,
        dry_run=dry_run,
    )


@app.command(name="add-tracks")
def add_tracks_to_playlist(
    name_or_id: str = typer.Argument(..., help="Playlist name or integer ID."),
    track_ids: Optional[List[int]] = typer.Argument(None, help="Track IDs to append."),
    artist: Optional[str] = typer.Option(None, "--artist", help="Filter tracks to add by artist."),
    album: Optional[str] = typer.Option(None, "--album", help="Filter tracks to add by album."),
    search: Optional[str] = typer.Option(None, "--search", help="Filter tracks to add by search query."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate without writing to iPod."),
    mount_point: Optional[str] = typer.Option(None, "--mount", "-m", help="iPod mount point."),
) -> None:
    """Append tracks to an existing playlist."""
    mount_path, library = _load_library(mount_point)
    snapshot = library.snapshot

    playlist = _find_playlist(snapshot.playlists, name_or_id)
    if not playlist:
        print_error(f"Playlist '{name_or_id}' not found.")
        raise typer.Exit(1)

    if playlist.system_managed:
        print_error(f"Playlist '{playlist.name}' is system-managed and cannot be modified.")
        raise typer.Exit(1)

    selected = _resolve_tracks(library, track_ids=track_ids, artist=artist, album=album, search=search)
    if not selected:
        print_error("No matching tracks specified to add.")
        raise typer.Exit(1)

    # Append new track IDs
    existing_ids = list(playlist.track_ids)
    new_ids = [t.track_id for t in selected]
    combined_entries = playlist_entries(existing_ids + new_ids, previous=playlist.entries)

    updated_pl = replace(playlist, entries=combined_entries)
    desired = replace(
        snapshot,
        playlists=tuple(updated_pl if p.playlist_id == playlist.playlist_id else p for p in snapshot.playlists),
    )

    print_banner()
    print_info(
        f"Adding {len(new_ids)} track(s) to playlist [bold white]'{playlist.name}'[/bold white] "
        f"(total will be {len(combined_entries)})..."
    )

    _commit_playlist_plan(
        mount_path,
        library,
        desired,
        action_desc=f"Adding {len(new_ids)} track(s) to '{playlist.name}'",
        delete_omissions=False,
        dry_run=dry_run,
    )


@app.command(name="remove-tracks")
def remove_tracks_from_playlist(
    name_or_id: str = typer.Argument(..., help="Playlist name or integer ID."),
    track_ids: Optional[List[int]] = typer.Argument(None, help="Track IDs to remove from the playlist."),
    clear_all: bool = typer.Option(False, "--all", help="Remove all tracks from this playlist."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate without writing to iPod."),
    mount_point: Optional[str] = typer.Option(None, "--mount", "-m", help="iPod mount point."),
) -> None:
    """Remove tracks from a playlist (does NOT delete files from iPod)."""
    mount_path, library = _load_library(mount_point)
    snapshot = library.snapshot

    playlist = _find_playlist(snapshot.playlists, name_or_id)
    if not playlist:
        print_error(f"Playlist '{name_or_id}' not found.")
        raise typer.Exit(1)

    if playlist.system_managed:
        print_error(f"Playlist '{playlist.name}' is system-managed and cannot be modified.")
        raise typer.Exit(1)

    if clear_all:
        new_entries = ()
    else:
        if not track_ids:
            print_error("Specify track IDs to remove or use --all to clear the playlist.")
            raise typer.Exit(1)
        remove_set = set(track_ids)
        filtered_ids = [tid for tid in playlist.track_ids if tid not in remove_set]
        new_entries = playlist_entries(filtered_ids, previous=playlist.entries)

    updated_pl = replace(playlist, entries=new_entries)
    desired = replace(
        snapshot,
        playlists=tuple(updated_pl if p.playlist_id == playlist.playlist_id else p for p in snapshot.playlists),
    )

    removed_count = len(playlist.entries) - len(new_entries)
    print_banner()
    print_info(
        f"Removing {removed_count} track occurrence(s) from playlist [bold white]'{playlist.name}'[/bold white]..."
    )

    _commit_playlist_plan(
        mount_path,
        library,
        desired,
        action_desc=f"Removing tracks from '{playlist.name}'",
        delete_omissions=False,
        dry_run=dry_run,
    )


@app.command(name="rename")
def rename_playlist(
    name_or_id: str = typer.Argument(..., help="Current playlist name or integer ID."),
    new_name: str = typer.Argument(..., help="New name for the playlist."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate without writing to iPod."),
    mount_point: Optional[str] = typer.Option(None, "--mount", "-m", help="iPod mount point."),
) -> None:
    """Rename an existing playlist."""
    mount_path, library = _load_library(mount_point)
    snapshot = library.snapshot

    playlist = _find_playlist(snapshot.playlists, name_or_id)
    if not playlist:
        print_error(f"Playlist '{name_or_id}' not found.")
        raise typer.Exit(1)

    if playlist.system_managed:
        print_error(f"Playlist '{playlist.name}' is system-managed and cannot be renamed.")
        raise typer.Exit(1)

    target_name = new_name.strip()
    if not target_name:
        print_error("New playlist name cannot be empty.")
        raise typer.Exit(1)

    # Check for name collision
    existing = [
        p for p in snapshot.playlists
        if p.playlist_id != playlist.playlist_id and (p.name or "").casefold() == target_name.casefold()
    ]
    if existing:
        print_error(f"A playlist named '{target_name}' already exists.")
        raise typer.Exit(1)

    updated_pl = replace(playlist, name=target_name)
    desired = replace(
        snapshot,
        playlists=tuple(updated_pl if p.playlist_id == playlist.playlist_id else p for p in snapshot.playlists),
    )

    print_banner()
    print_info(f"Renaming playlist [bold white]'{playlist.name}'[/bold white] -> [bold cyan]'{target_name}'[/bold cyan]...")

    _commit_playlist_plan(
        mount_path,
        library,
        desired,
        action_desc=f"Renaming playlist '{playlist.name}' to '{target_name}'",
        delete_omissions=False,
        dry_run=dry_run,
    )


@app.command(name="remove")
def remove_playlist(
    name_or_id: str = typer.Argument(..., help="Playlist name or integer ID to remove."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate without writing to iPod."),
    mount_point: Optional[str] = typer.Option(None, "--mount", "-m", help="iPod mount point."),
) -> None:
    """Remove a playlist from the iPod (does NOT delete audio files)."""
    mount_path, library = _load_library(mount_point)
    snapshot = library.snapshot

    playlist = _find_playlist(snapshot.playlists, name_or_id)
    if not playlist:
        print_error(f"Playlist '{name_or_id}' not found.")
        raise typer.Exit(1)

    if playlist.system_managed:
        print_error(f"Playlist '{playlist.name}' is system-managed and cannot be deleted.")
        raise typer.Exit(1)

    print_banner()
    print_warning(f"Playlist to remove: [bold white]'{playlist.name}'[/bold white] ({len(playlist.entries)} tracks)")
    console.print("[dim]Note: Removing this playlist will NOT delete the actual audio files from the iPod.[/dim]")

    if not yes and not dry_run:
        if not Confirm.ask("Are you sure you want to delete this playlist?"):
            console.print("[dim]Operation cancelled.[/dim]")
            raise typer.Exit()

    desired = replace(
        snapshot,
        playlists=tuple(p for p in snapshot.playlists if p.playlist_id != playlist.playlist_id),
    )

    _commit_playlist_plan(
        mount_path,
        library,
        desired,
        action_desc=f"Removal of playlist '{playlist.name}'",
        delete_omissions=True,
        dry_run=dry_run,
    )


# Alias delete -> remove
app.command(name="delete", hidden=True)(remove_playlist)
