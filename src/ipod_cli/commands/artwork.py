"""Command for managing, optimizing, and compiling artwork into iPod ArtworkDB and media files."""

from __future__ import annotations

import hashlib
import io
import os
from dataclasses import replace
from pathlib import Path
from typing import List, Optional

from PIL import Image
import typer
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.table import Table

from iPodDB.library.artwork import ArtworkPixels
from iPodDB.library.models import Track
from iPodDB.library.writing import (
    ArtworkAsset,
    FileDependency,
    SourceFile,
    WriteResources,
    WriteTarget,
)
from ipod_cli.artwork import (
    DEFAULT_ARTWORK_QUALITY,
    DEFAULT_ARTWORK_SIZE,
    embed_artwork,
    extract_embedded_artwork,
    find_adjacent_artwork,
    process_artwork_image,
    resize_existing_artwork,
)
from ipod_cli.device import discover_ipod_mounts, inspect_ipod, is_ipod_mount
from ipod_cli.storage_ops import commit_transaction, load_ipod_library
from ipod_cli.ui import console, print_banner, print_error, print_info, print_success, print_warning

app = typer.Typer(
    name="artwork",
    help="Embed, resize (500x500 JPEG 85%), compile into iPod ArtworkDB (.ithmb), and manage cover art.",
    no_args_is_help=True,
)


def _resolve_ipod_mount(mount_point: Optional[str]) -> Path:
    """Resolve and validate an iPod mount point."""
    if mount_point:
        p = Path(mount_point)
        if not is_ipod_mount(p):
            print_error(f"The path '{mount_point}' is not a valid iPod.")
            raise typer.Exit(1)
        return p
    mounts = discover_ipod_mounts()
    if not mounts:
        print_error("No iPod detected automatically. Please specify --mount.")
        raise typer.Exit(1)
    return mounts[0]


@app.command(name="set")
def set_artwork_cmd(
    target: str = typer.Argument(
        ...,
        help="Track ID on iPod (e.g. 780), path to file on iPod, or local media file.",
    ),
    image_file: str = typer.Argument(..., help="Path to cover image file (JPEG, PNG, WebP)."),
    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="iPod mount point or drive letter (auto-detected if omitted).",
    ),
    size: int = typer.Option(DEFAULT_ARTWORK_SIZE, "--size", "-s", help="Max width/height in px (default: 500)."),
    quality: int = typer.Option(DEFAULT_ARTWORK_QUALITY, "--quality", "-q", help="JPEG quality 1-100 (default: 85)."),
) -> None:
    """Resize image to 500x500 JPEG 85% and apply to a local file or compile into iPod ArtworkDB."""
    i_path = Path(image_file)
    if not i_path.is_file():
        print_error(f"Image file '{image_file}' not found.")
        raise typer.Exit(1)

    print_banner()

    # Check if target is a track ID on the iPod
    is_track_id = target.isdigit()
    t_path = Path(target)

    # If it is a track ID or exists under an iPod mount, compile into iPod database
    detected_mount: Optional[Path] = None
    if is_track_id or mount_point or "iPod_Control" in target or is_ipod_mount(t_path.anchor):
        try:
            detected_mount = _resolve_ipod_mount(mount_point)
        except Exception:
            detected_mount = None

    if detected_mount and (is_track_id or (detected_mount in t_path.resolve().parents)):
        # Target is on the iPod! Compile into native iPod ArtworkDB + .ithmb
        clean_mount = str(detected_mount).rstrip("/\\")
        print_info(f"Target is on iPod [bold]{clean_mount}[/bold]. Preparing native ArtworkDB compilation...")

        device = inspect_ipod(detected_mount)
        if not device.supports_artwork or not device.cover_formats:
            print_error(f"Connected iPod ({device.generation.name if device.generation else 'Unknown'}) does not support artwork.")
            raise typer.Exit(1)

        lib = load_ipod_library(detected_mount)
        target_track: Optional[Track] = None

        if is_track_id:
            track_num = int(target)
            for t in lib.snapshot.tracks:
                if t.track_id == track_num:
                    target_track = t
                    break
        else:
            rel_str = str(t_path.resolve().relative_to(detected_mount)).replace("\\", "/")
            for t in lib.snapshot.tracks:
                if t.metadata.location and t.metadata.location.replace("\\", "/").casefold() == rel_str.casefold():
                    target_track = t
                    break

        if not target_track:
            print_error(f"Track '{target}' not found in iPod database on {clean_mount}.")
            raise typer.Exit(1)

        print_info(f"Updating track: [bold white]{target_track.artist} - {target_track.title}[/bold white]")

        # 1. Optimize image into 500x500 JPEG 85%
        optimized_jpeg = process_artwork_image(i_path, target_size=size, quality=quality)

        # 2. If physical file exists on the iPod, also tag it
        if target_track.metadata.location:
            abs_media = detected_mount / target_track.metadata.location.replace("/", os.sep)
            if abs_media.is_file():
                try:
                    embed_artwork(abs_media, i_path, target_size=size, quality=quality)
                except Exception:
                    pass

        # 3. Create ArtworkPixels for ArtworkDB compiler
        img = Image.open(io.BytesIO(optimized_jpeg))
        if img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        pixels = ArtworkPixels(w, h, img.tobytes())
        assigned_art_id = -1
        art_asset = ArtworkAsset(assigned_art_id, pixels)

        # 4. Prepare iPod database update
        new_tracks = tuple(
            replace(t, artwork_id=assigned_art_id) if t.track_id == target_track.track_id else t
            for t in lib.snapshot.tracks
        )
        desired = replace(lib.snapshot, tracks=new_tracks)
        draft = lib.begin_draft(desired)
        write_target = WriteTarget(
            cover_formats=device.cover_formats,
            supports_sparse_artwork=device.supports_sparse_artwork,
        )
        plan = lib.analyze(draft, target=write_target)

        # 5. Collect existing .ithmb files
        ithmb_inventory: list[FileDependency] = []
        ithmb_sources: list[SourceFile] = []
        artwork_dir = detected_mount / "iPod_Control" / "Artwork"
        if artwork_dir.exists():
            for item in artwork_dir.iterdir():
                if item.is_file() and item.suffix.lower() == ".ithmb":
                    rel = f"iPod_Control/Artwork/{item.name}"
                    data = item.read_bytes()
                    dep = FileDependency(rel, len(data), hashlib.sha256(data).hexdigest())
                    ithmb_inventory.append(dep)
                    ithmb_sources.append(SourceFile(dep, data))

        write_resources = WriteResources(
            artwork=(art_asset,),
            files=tuple(ithmb_sources),
            file_inventory=tuple(ithmb_inventory) if ithmb_inventory else None,
            pending_playback_sidecars=False,
        )
        write_result = lib.prepare(plan, write_resources)

        if write_result.issues:
            print_error(f"Failed to prepare artwork transaction: {write_result.issues[0].message}")
            raise typer.Exit(1)

        # 6. Commit to iPod
        commit_transaction(
            detected_mount,
            write_result.prepared.itunes,
            files_to_copy=[],
            files_to_delete=[],
            prepared_artwork=write_result.prepared.artwork,
            prepared_artwork_files=write_result.prepared.artwork_files,
        )

        print_success(
            f"Success! Artwork compiled into ArtworkDB (.ithmb) and iTunesDB for "
            f"[bold green]{target_track.artist} - {target_track.title}[/bold green]."
        )
        return

    # Fallback: standard local media file tagging
    if not t_path.is_file():
        print_error(f"Media file or iPod track '{target}' not found.")
        raise typer.Exit(1)

    print_info(f"Optimizing artwork: [bold white]{i_path.name}[/bold white] -> {size}x{size} JPEG (quality {quality})")
    print_info(f"Target media file: [cyan]{t_path.name}[/cyan]")

    try:
        success = embed_artwork(t_path, i_path, target_size=size, quality=quality)
        if success:
            print_success(f"Success! Artwork embedded into [bold green]{t_path.name}[/bold green].")
        else:
            print_warning(f"File format '{t_path.suffix}' does not support embedded artwork tagging.")
    except Exception as exc:
        print_error(f"Failed to embed artwork: {exc}")
        raise typer.Exit(1)


@app.command(name="sync")
def sync_artwork_cmd(
    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="iPod mount point or drive letter (auto-detected if omitted).",
    ),
    folder: Optional[str] = typer.Option(
        None,
        "--folder",
        "-f",
        help="Local directory to scan for album covers (folder.jpg, cover.jpg) or tagged tracks.",
    ),
    all_tracks: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Recompile artwork for all tracks, not just tracks missing covers.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Simulate artwork compilation without writing changes to the iPod.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Confirm and apply artwork changes non-interactively.",
    ),
    size: int = typer.Option(DEFAULT_ARTWORK_SIZE, "--size", "-s", help="Max width/height in px (default: 500)."),
    quality: int = typer.Option(DEFAULT_ARTWORK_QUALITY, "--quality", "-q", help="JPEG quality 1-100 (default: 85)."),
) -> None:
    """Scan existing iPod tracks, extract/match covers, and compile into ArtworkDB (.ithmb)."""
    print_banner()
    target_mount = _resolve_ipod_mount(mount_point)
    clean_mount = str(target_mount).rstrip("/\\")

    device = inspect_ipod(target_mount)
    if not device.supports_artwork or not device.cover_formats:
        print_error(f"Connected iPod ({device.generation.name if device.generation else 'Unknown'}) does not support color artwork.")
        raise typer.Exit(1)

    print_info(f"Reading iPod library on [bold]{clean_mount}[/bold]...")
    try:
        lib = load_ipod_library(target_mount)
    except Exception as e:
        print_error(f"Failed to load iPod library: {e}")
        raise typer.Exit(1)

    tracks = lib.snapshot.tracks
    print_info(f"Found [bold]{len(tracks)}[/bold] total track(s) in iPod library.")

    # Build local folder cache if provided
    local_album_art: dict[tuple[str, str], bytes] = {}
    if folder:
        f_path = Path(folder)
        if f_path.is_dir():
            print_info(f"Indexing local cover art from [bold]{folder}[/bold]...")
            for img_item in f_path.rglob("*"):
                if img_item.is_file() and img_item.suffix.lower() in (".jpg", ".jpeg", ".png"):
                    parent_name = img_item.parent.name.casefold().strip()
                    if img_item.stem.lower() in ("folder", "cover", "front", "album"):
                        try:
                            data = process_artwork_image(img_item, target_size=size, quality=quality)
                            local_album_art[("", parent_name)] = data
                        except Exception:
                            pass

    # Determine candidate tracks
    needs_full_recompile = all_tracks
    if not needs_full_recompile and not device.supports_sparse_artwork:
        artwork_db_path = target_mount / "iPod_Control" / "Artwork" / "ArtworkDB"
        if artwork_db_path.exists():
            try:
                from iPodDB.ArtworkDB.parser.parse_ArtworkDB import parse_ArtworkDB
                from iPodDB.ArtworkDB.shared.chunk_defs.mhii import MhiiHeader

                parsed_art = parse_ArtworkDB(artwork_db_path.read_bytes())
                mhii_count = len([s for s in parsed_art.find_chunks(MhiiHeader)])
                tracks_with_art = sum(1 for t in tracks if t.artwork_id > 0)
                if mhii_count < tracks_with_art:
                    print_info(
                        f"Non-sparse hardware detected ({device.model_name}): "
                        f"recompiling individual track artwork links ({mhii_count} -> {tracks_with_art})..."
                    )
                    needs_full_recompile = True
            except Exception:
                pass

    candidate_tracks = [t for t in tracks if (needs_full_recompile or t.artwork_id <= 0)]
    if not candidate_tracks:
        print_success("All tracks already have artwork registered in iPod database! (Use --all to force recompile).")
        return

    print_info(f"Scanning [bold]{len(candidate_tracks)}[/bold] candidate track(s) for cover art...")

    prepared_artworks: list[ArtworkAsset] = []
    artwork_cache_by_digest: dict[str, int] = {}
    track_to_artwork_id: dict[int, int] = {}
    matched_tracks: list[Track] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Checking artwork...", total=len(candidate_tracks))

        for t in candidate_tracks:
            progress.update(task, description=f"[cyan]Scanning:[/cyan] {t.artist} - {t.title}")
            art_bytes: Optional[bytes] = None

            # 1. Check embedded artwork in physical file on the iPod
            if t.metadata.location:
                abs_file = target_mount / t.metadata.location.replace("/", os.sep)
                if abs_file.is_file():
                    try:
                        art_bytes = extract_embedded_artwork(abs_file)
                    except Exception:
                        pass

            # 2. Check local folder if not found
            if not art_bytes and local_album_art and t.album:
                alb_key = ("", t.album.casefold().strip())
                if alb_key in local_album_art:
                    art_bytes = local_album_art[alb_key]

            # 3. If artwork found, process and prepare ArtworkAsset
            if art_bytes:
                try:
                    digest = hashlib.sha256(art_bytes).hexdigest()
                    if digest in artwork_cache_by_digest:
                        assigned_id = artwork_cache_by_digest[digest]
                    else:
                        img = Image.open(io.BytesIO(art_bytes))
                        if img.mode != "RGB":
                            img = img.convert("RGB")
                        # Ensure reasonable dimensions
                        if img.width > size or img.height > size:
                            img.thumbnail((size, size), Image.Resampling.LANCZOS)
                        w, h = img.size
                        pixels = ArtworkPixels(w, h, img.tobytes())
                        assigned_id = -(len(prepared_artworks) + 1)
                        prepared_artworks.append(ArtworkAsset(assigned_id, pixels))
                        artwork_cache_by_digest[digest] = assigned_id

                    track_to_artwork_id[t.track_id] = assigned_id
                    matched_tracks.append(t)
                except Exception:
                    pass

            progress.advance(task)

    if not track_to_artwork_id:
        print_warning(
            "No cover art found embedded in tracks or matching local folder. "
            "Use 'ipod artwork set <track_id> <cover.jpg>' to assign covers."
        )
        return

    # Summary table
    table = Table(title="Artwork Compilation Plan", border_style="cyan")
    table.add_column("ID", justify="right", style="dim")
    table.add_column("Artist", style="cyan")
    table.add_column("Album", style="green")
    table.add_column("Title", style="bold white")
    table.add_column("Assigned Artwork", justify="right", style="magenta")

    preview_tracks = matched_tracks[:10]
    for t in preview_tracks:
        art_id = track_to_artwork_id[t.track_id]
        table.add_row(
            str(t.track_id),
            t.artist or "-",
            t.album or "-",
            t.title or "-",
            f"Asset {abs(art_id)}",
        )

    console.print(table)
    if len(matched_tracks) > 10:
        print_info(f"... and [bold]{len(matched_tracks) - 10}[/bold] more tracks.")

    print_info(
        f"Summary: [bold green]{len(matched_tracks)}[/bold green] tracks will be linked to "
        f"[bold cyan]{len(prepared_artworks)}[/bold cyan] unique compiled covers."
    )

    if dry_run:
        print_warning("[Dry Run Mode] No changes applied to the iPod.")
        return

    if not yes:
        if not typer.confirm(f"Compile {len(prepared_artworks)} cover(s) and write to iPod?", default=True):
            print_info("Cancelled by user.")
            return

    # Update tracks in snapshot
    print_info("Preparing iPod database and .ithmb compilation...")
    new_tracks = tuple(
        replace(t, artwork_id=track_to_artwork_id[t.track_id]) if t.track_id in track_to_artwork_id else t
        for t in lib.snapshot.tracks
    )
    desired = replace(lib.snapshot, tracks=new_tracks)
    draft = lib.begin_draft(desired)
    write_target = WriteTarget(
        cover_formats=device.cover_formats,
        supports_sparse_artwork=device.supports_sparse_artwork,
    )
    plan = lib.analyze(draft, target=write_target)

    # Collect existing .ithmb files
    ithmb_inventory: list[FileDependency] = []
    ithmb_sources: list[SourceFile] = []
    artwork_dir = target_mount / "iPod_Control" / "Artwork"
    if artwork_dir.exists():
        for item in artwork_dir.iterdir():
            if item.is_file() and item.suffix.lower() == ".ithmb":
                rel = f"iPod_Control/Artwork/{item.name}"
                data = item.read_bytes()
                dep = FileDependency(rel, len(data), hashlib.sha256(data).hexdigest())
                ithmb_inventory.append(dep)
                ithmb_sources.append(SourceFile(dep, data))

    write_resources = WriteResources(
        artwork=tuple(prepared_artworks),
        files=tuple(ithmb_sources),
        file_inventory=tuple(ithmb_inventory) if ithmb_inventory else None,
        pending_playback_sidecars=False,
    )
    write_result = lib.prepare(plan, write_resources)

    if write_result.issues:
        errors = [i for i in write_result.issues if i.severity.name == "ERROR"]
        if errors:
            print_error(f"Artwork preparation failed with {len(errors)} error(s):")
            for issue in errors:
                console.print(f"  [red]• {issue.code}[/red]: {issue.message} ({issue.detail or ''})")
            raise typer.Exit(1)

    if write_result.prepared is None:
        print_error("Prepared library returned no output data.")
        raise typer.Exit(1)

    print_info(f"Writing ArtworkDB and .ithmb files to iPod ({clean_mount})...")
    commit_transaction(
        target_mount,
        write_result.prepared.itunes,
        files_to_copy=[],
        files_to_delete=[],
        prepared_artwork=write_result.prepared.artwork,
        prepared_artwork_files=write_result.prepared.artwork_files,
    )

    print_success(
        f"Artwork synchronization complete! [bold green]{len(matched_tracks)}[/bold green] tracks "
        f"updated with [bold cyan]{len(prepared_artworks)}[/bold cyan] native iPod cover(s)."
    )


@app.command(name="resize")
def resize_artwork_cmd(
    media_files: List[str] = typer.Argument(..., help="Audio or video file(s) whose embedded artwork should be resized."),
    size: int = typer.Option(DEFAULT_ARTWORK_SIZE, "--size", "-s", help="Max width/height in px (default: 500)."),
    quality: int = typer.Option(DEFAULT_ARTWORK_QUALITY, "--quality", "-q", help="JPEG quality 1-100 (default: 85)."),
) -> None:
    """Resize existing embedded artwork in media files to iPod-optimal 500x500 JPEG 85%."""
    targets: list[Path] = []
    for m in media_files:
        p = Path(m)
        if p.is_file():
            targets.append(p)
        elif p.is_dir():
            for item in p.rglob("*"):
                if item.is_file() and item.suffix.lower() in (".mp3", ".m4a", ".m4v", ".flac"):
                    targets.append(item)

    if not targets:
        print_warning("No matching media files found.")
        raise typer.Exit(0)

    print_banner()
    print_info(f"Resizing embedded artwork in {len(targets)} file(s) to {size}x{size} JPEG {quality}%...")

    updated = 0
    skipped = 0

    for f in targets:
        try:
            if resize_existing_artwork(f, target_size=size, quality=quality):
                updated += 1
                console.print(f"  [green]✓[/green] Resized artwork: [white]{f.name}[/white]")
            else:
                skipped += 1
        except Exception as e:
            console.print(f"  [red]✗[/red] Failed on {f.name}: {e}")

    print_success(f"Complete: {updated} file(s) updated, {skipped} skipped (no artwork found).")


@app.command(name="extract")
def extract_artwork_cmd(
    media_file: str = typer.Argument(..., help="Path to audio or video file."),
    output_image: Optional[str] = typer.Argument(None, help="Output image file path (defaults to '<file_stem>_cover.jpg')."),
) -> None:
    """Extract embedded cover artwork from a media file to a JPEG image."""
    m_path = Path(media_file)
    if not m_path.is_file():
        print_error(f"Media file '{media_file}' not found.")
        raise typer.Exit(1)

    data = extract_embedded_artwork(m_path)
    if not data:
        print_warning(f"No embedded artwork found in '{m_path.name}'.")
        raise typer.Exit(1)

    out_path = Path(output_image) if output_image else m_path.with_name(f"{m_path.stem}_cover.jpg")
    out_path.write_bytes(data)
    print_success(f"Extracted artwork saved to: [bold green]{out_path}[/bold green] ({len(data)} bytes).")
