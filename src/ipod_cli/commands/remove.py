"""Command for removing tracks from iPod library with physical file cleanup."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import List, Optional

import typer
from rich.table import Table

from iPodDB.library.models import Track
from iPodDB.library.writing import WriteResources
from ipod_cli.device import discover_ipod_mounts, is_ipod_mount
from ipod_cli.storage_ops import commit_transaction, load_ipod_library
from ipod_cli.ui import console, print_error, print_info, print_success, print_warning


def remove_tracks(
    track_ids: Optional[List[int]] = typer.Argument(
        None,
        help="Numeric IDs of tracks to remove.",
    ),
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
        help="Search query to find and remove tracks by title, artist, or album.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Confirm deletion non-interactively.",
    ),
    keep_files: bool = typer.Option(
        False,
        "--keep-files",
        help="Keep physical audio files on disk, only removing them from the database.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Simulate track removal without deleting files or updating database.",
    ),
) -> None:
    """Remove tracks from the iPod library and delete matching physical files."""
    # 1. Resolve iPod mount
    target_mount: Optional[Path] = None
    if mount_point:
        p = Path(mount_point)
        if not is_ipod_mount(p):
            print_error(f"The path '{mount_point}' is not a valid iPod.")
            raise typer.Exit(1)
        target_mount = p
    else:
        mounts = discover_ipod_mounts()
        if not mounts:
            print_error("No iPod detected automatically.")
            raise typer.Exit(1)
        target_mount = mounts[0]

    # 2. Load library
    try:
        lib = load_ipod_library(target_mount)
    except Exception as e:
        print_error(f"Failed to load iPod library: {e}")
        raise typer.Exit(1)

    all_tracks = lib.snapshot.tracks
    target_tracks: list[Track] = []

    # 3. Filter tracks
    if track_ids:
        id_set = set(track_ids)
        target_tracks = [t for t in all_tracks if t.track_id in id_set]
    elif search:
        query = search.casefold().strip()
        target_tracks = [
            t for t in all_tracks
            if query in t.title.casefold()
            or query in t.artist.casefold()
            or query in t.album.casefold()
        ]
    else:
        print_error("Specify at least one track ID or use the --search option.")
        raise typer.Exit(1)

    if not target_tracks:
        print_warning("No tracks matched the specified criteria.")
        raise typer.Exit(0)

    # 4. Preview table
    table = Table(title=f"Tracks Selected for Removal ({len(target_tracks)})", border_style="red")
    table.add_column("ID", justify="right", style="cyan")
    table.add_column("Title", style="bold white")
    table.add_column("Artist", style="green")
    table.add_column("Album", style="magenta")
    table.add_column("File on iPod", style="dim")

    for t in target_tracks:
        table.add_row(
            str(t.track_id),
            t.title,
            t.artist,
            t.album,
            t.metadata.location,
        )

    console.print(table)

    if dry_run:
        print_warning("[Dry Run Mode] No tracks or files were deleted.")
        return

    if not yes:
        confirmed = typer.confirm(
            f"Are you sure you want to permanently remove {len(target_tracks)} track(s) from the iPod?",
            default=False,
        )
        if not confirmed:
            print_info("Operation cancelled by user.")
            return

    # 5. Prepare deletion with delete_omissions=True
    remove_ids = {t.track_id for t in target_tracks}
    remaining_tracks = tuple(t for t in all_tracks if t.track_id not in remove_ids)
    desired_snapshot = replace(lib.snapshot, tracks=remaining_tracks)

    draft = lib.begin_draft(desired_snapshot, delete_omissions=True)
    plan = lib.analyze(draft)
    write_result = lib.prepare(plan, WriteResources(pending_playback_sidecars=False))

    if write_result.issues:
        print_error(f"Track removal validation failed with {len(write_result.issues)} issue(s):")
        for issue in write_result.issues:
            console.print(f"  [red]• {issue.code}[/red]: {issue.message} ({issue.detail or ''})")
        raise typer.Exit(1)

    if write_result.prepared is None:
        print_error("Prepared library returned no output data.")
        raise typer.Exit(1)

    # 6. Collect physical files to delete
    files_to_delete: list[Path] = []
    if not keep_files:
        for t in target_tracks:
            if t.metadata.location:
                abs_f = target_mount / t.metadata.location.replace("/", os.sep)
                files_to_delete.append(abs_f)

    # 7. Transactional commit
    print_info("Committing database changes and removing physical files from iPod...")
    commit_transaction(
        target_mount,
        write_result.prepared.itunes,
        files_to_copy=[],
        files_to_delete=files_to_delete,
    )

    print_success(
        f"Success! [bold red]{len(target_tracks)}[/bold red] track(s) removed from iPod library."
    )
