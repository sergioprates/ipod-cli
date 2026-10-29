"""Intelligent music library sync engine from local folders to the iPod."""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Optional

from PIL import Image
import typer
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.table import Table

from iPodDB.library.artwork import ArtworkPixels
from iPodDB.library.media import prepared_audio
from iPodDB.library.models import Track, TrackMetadata
from iPodDB.library.writing import (
    ArtworkAsset,
    FileDependency,
    PreparedMedia,
    SourceFile,
    WriteResources,
    WriteTarget,
)
from ipod_cli.allocator import MusicPathAllocator, count_music_directories
from ipod_cli.device import discover_ipod_mounts, inspect_ipod, is_ipod_mount
from ipod_cli.metadata import AudioMetadata, extract_metadata
from ipod_cli.storage_ops import commit_transaction, load_ipod_library
from ipod_cli.transcode import is_ffmpeg_available, needs_transcoding, transcode_to_alac
from ipod_cli.ui import console, print_error, print_info, print_success, print_warning

SUPPORTED_AUDIO_EXTS = {
    ".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff",
    ".flac", ".ogg", ".opus", ".wma", ".ape", ".wv"
}


def _norm(text: str) -> str:
    """Normalize text for resilient string comparison."""
    return "".join(c for c in text.casefold() if c.isalnum() or c.isspace()).strip()


def sync_library(
    source_dir: Path = typer.Argument(
        ...,
        help="Local computer directory containing music files to synchronize.",
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
    ),
    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="iPod mount point or drive letter (e.g. 'D:\\').",
    ),
    delete: bool = typer.Option(
        False,
        "--delete",
        help="Remove tracks from iPod that are no longer present in the local directory.",
    ),
    force_alac: bool = typer.Option(
        False,
        "--force-alac",
        help="Force conversion of all local tracks to ALAC 44.1kHz.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Simulate synchronization without applying changes to the iPod.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Confirm synchronization non-interactively.",
    ),
) -> None:
    """Synchronize a local music folder with the iPod intelligently."""
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

    # 2. Load iPod library
    clean_mount = str(target_mount).rstrip("/\\")
    print_info(f"Reading iPod library on [bold]{clean_mount}[/bold]...")
    try:
        lib = load_ipod_library(target_mount)
    except Exception as e:
        print_error(f"Failed to load iPod library: {e}")
        raise typer.Exit(1)

    device = inspect_ipod(target_mount)
    ipod_tracks = lib.snapshot.tracks
    # Create lookup index by (artist, title)
    ipod_index: dict[tuple[str, str], Track] = {}
    album_artwork_map: dict[tuple[str, str], int] = {}
    for t in ipod_tracks:
        key = (_norm(t.artist), _norm(t.title))
        ipod_index[key] = t
        if t.artwork_id > 0 and t.album:
            alb_key = (t.artist.casefold().strip(), t.album.casefold().strip())
            album_artwork_map[alb_key] = t.artwork_id

    # 3. Scan local directory
    clean_source = str(source_dir).rstrip("/\\")
    print_info(f"Scanning local directory [bold]{clean_source}[/bold]...")
    local_files: list[Path] = []
    for item in source_dir.rglob("*"):
        if item.is_file() and item.suffix.lower() in SUPPORTED_AUDIO_EXTS:
            local_files.append(item.resolve())

    if not local_files:
        print_warning(f"No supported audio files found in '{source_dir}'.")
        raise typer.Exit(0)

    print_info(f"Found [bold]{len(local_files)}[/bold] audio file(s) in local directory.")

    # 4. Compare local collection against iPod
    files_to_add: list[Path] = []
    matched_ipod_ids: set[int] = set()

    for f in local_files:
        try:
            m = extract_metadata(f)
            key = (_norm(m.artist), _norm(m.title))
            if key in ipod_index:
                matched_ipod_ids.add(ipod_index[key].track_id)
            else:
                files_to_add.append(f)
        except Exception:
            files_to_add.append(f)

    tracks_to_delete: list[Track] = []
    if delete:
        tracks_to_delete = [t for t in ipod_tracks if t.track_id not in matched_ipod_ids]

    # 5. Summary plan table
    summary_table = Table(title="Synchronization Plan Summary", border_style="cyan")
    summary_table.add_column("Operation", style="bold")
    summary_table.add_column("Count", justify="right", style="bold")
    summary_table.add_column("Description")

    summary_table.add_row(
        "[green]+ Add[/green]",
        str(len(files_to_add)),
        "New local tracks to copy/convert to iPod",
    )
    if delete:
        summary_table.add_row(
            "[red]- Remove[/red]",
            str(len(tracks_to_delete)),
            "Tracks on iPod no longer present locally",
        )
    else:
        summary_table.add_row(
            "[dim]= Preserve[/dim]",
            str(len(ipod_tracks) - len(matched_ipod_ids)),
            "Extra tracks on iPod kept (pass --delete to remove)",
        )
    summary_table.add_row(
        "[blue]= In Sync[/blue]",
        str(len(matched_ipod_ids)),
        "Tracks already in sync",
    )

    console.print(summary_table)

    if not files_to_add and not tracks_to_delete:
        print_success("iPod library is already perfectly in sync!")
        return

    if dry_run:
        print_warning("[Dry Run Mode] No changes applied.")
        return

    if not yes:
        if not typer.confirm("Apply synchronization changes to the iPod?", default=True):
            print_info("Synchronization cancelled by user.")
            return

    # 6. Set up allocator
    existing_locations = [t.metadata.location for t in ipod_tracks if t.metadata.location]
    dir_count = count_music_directories(target_mount)
    allocator = MusicPathAllocator(directory_count=dir_count, occupied_paths=existing_locations)

    temp_dir = Path(tempfile.mkdtemp(prefix="ipod_sync_"))
    files_to_copy: list[tuple[Path, Path]] = []
    prepared_medias: list[PreparedMedia] = []
    prepared_artworks: list[ArtworkAsset] = []
    artwork_cache_by_digest: dict[str, int] = {}
    new_tracks: list[Track] = []

    try:
        # Process additions
        if files_to_add:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Processing new tracks...", total=len(files_to_add))

                for idx, src_file in enumerate(files_to_add, start=1):
                    progress.update(task, description=f"[cyan]Preparing ({idx}/{len(files_to_add)}):[/cyan] {src_file.name}")

                    should_transcode = force_alac or needs_transcoding(src_file)
                    process_file = src_file

                    if should_transcode:
                        if not is_ffmpeg_available():
                            print_error("FFmpeg not found in system PATH. Required for transcoding.")
                            raise typer.Exit(1)
                        alac_temp = temp_dir / f"{src_file.stem}_{idx}.m4a"
                        transcode_to_alac(src_file, alac_temp)
                        process_file = alac_temp

                    meta = extract_metadata(process_file)
                    ext = process_file.suffix.lstrip(".").lower()
                    ipod_rel_path = allocator.allocate(ext)
                    ipod_abs_dst = target_mount / ipod_rel_path.replace("/", os.sep)

                    temp_track_id = -idx
                    file_dep = FileDependency(ipod_rel_path, meta.size_bytes, meta.sha256)
                    media = prepared_audio(temp_track_id, file_dep, meta.encoding)
                    prepared_medias.append(media)

                    assigned_artwork_id = 0
                    if meta.artwork_data and device.supports_artwork and device.cover_formats:
                        art_digest = hashlib.sha256(meta.artwork_data).hexdigest()
                        if art_digest in artwork_cache_by_digest:
                            assigned_artwork_id = artwork_cache_by_digest[art_digest]
                        else:
                            try:
                                img = Image.open(io.BytesIO(meta.artwork_data))
                                if img.mode != "RGB":
                                    img = img.convert("RGB")
                                w, h = img.size
                                pixels = ArtworkPixels(w, h, img.tobytes())
                                assigned_artwork_id = -(len(prepared_artworks) + 1)
                                prepared_artworks.append(ArtworkAsset(assigned_artwork_id, pixels))
                                artwork_cache_by_digest[art_digest] = assigned_artwork_id
                            except Exception:
                                pass
                    else:
                        alb_key = (meta.artist.casefold().strip(), meta.album.casefold().strip())
                        if alb_key in album_artwork_map:
                            assigned_artwork_id = album_artwork_map[alb_key]

                    track_meta = TrackMetadata(
                        location=ipod_rel_path,
                        sample_rate_hz=meta.sample_rate_hz,
                        file_format=meta.file_format,
                        disc_number=meta.disc_number,
                        total_discs=meta.total_discs,
                        total_tracks=meta.total_tracks,
                        composer=meta.composer,
                    )

                    new_tracks.append(
                        Track(
                            track_id=temp_track_id,
                            title=meta.title,
                            artist=meta.artist,
                            album=meta.album,
                            album_artist=meta.album_artist,
                            length_ms=meta.length_ms,
                            size_bytes=meta.size_bytes,
                            bitrate_kbps=meta.bitrate_kbps,
                            year=meta.year,
                            track_number=meta.track_number,
                            genre=meta.genre,
                            artwork_id=assigned_artwork_id,
                            metadata=track_meta,
                        )
                    )
                    files_to_copy.append((process_file, ipod_abs_dst))
                    progress.advance(task)

        # Prepare desired snapshot
        delete_ids = {t.track_id for t in tracks_to_delete}
        base_tracks = tuple(t for t in ipod_tracks if t.track_id not in delete_ids)
        desired_snapshot = replace(lib.snapshot, tracks=(*base_tracks, *new_tracks))

        print_info("Validating and preparing iPod database...")
        draft = lib.begin_draft(desired_snapshot, delete_omissions=bool(tracks_to_delete))
        write_target = WriteTarget(
            cover_formats=device.cover_formats,
            supports_sparse_artwork=device.supports_sparse_artwork,
        )
        plan = lib.analyze(draft, target=write_target)

        # Collect existing .ithmb files for artwork inventory and sources
        ithmb_inventory: list[FileDependency] = []
        ithmb_sources: list[SourceFile] = []
        artwork_dir = target_mount / "iPod_Control" / "Artwork"
        if artwork_dir.exists() and prepared_artworks:
            for item in artwork_dir.iterdir():
                if item.is_file() and item.suffix.lower() == ".ithmb":
                    rel = f"iPod_Control/Artwork/{item.name}"
                    data = item.read_bytes()
                    dep = FileDependency(rel, len(data), hashlib.sha256(data).hexdigest())
                    ithmb_inventory.append(dep)
                    ithmb_sources.append(SourceFile(dep, data))

        write_resources = WriteResources(
            media=tuple(prepared_medias),
            artwork=tuple(prepared_artworks),
            files=tuple(ithmb_sources),
            file_inventory=tuple(ithmb_inventory) if prepared_artworks else None,
            pending_playback_sidecars=False,
        )
        write_result = lib.prepare(plan, write_resources)

        if write_result.issues:
            print_error(f"Sync validation failed with {len(write_result.issues)} issue(s):")
            for issue in write_result.issues:
                console.print(f"  [red]• {issue.code}[/red]: {issue.message} ({issue.detail or ''})")
            raise typer.Exit(1)

        if write_result.prepared is None:
            print_error("Prepared library returned no output data.")
            raise typer.Exit(1)

        # Physical files to delete
        files_to_delete: list[Path] = []
        for t in tracks_to_delete:
            if t.metadata.location:
                files_to_delete.append(target_mount / t.metadata.location.replace("/", os.sep))

        # Transaction commit
        print_info(f"Committing synchronization to iPod ({clean_mount})...")
        commit_transaction(
            target_mount,
            write_result.prepared.itunes,
            files_to_copy=files_to_copy,
            files_to_delete=files_to_delete,
            prepared_artwork=write_result.prepared.artwork,
            prepared_artwork_files=write_result.prepared.artwork_files,
        )

        print_success(
            f"Synchronization completed successfully! "
            f"[bold green]+{len(new_tracks)}[/bold green] added, "
            f"[bold red]-{len(tracks_to_delete)}[/bold red] removed."
        )

    finally:
        if temp_dir.exists():
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
