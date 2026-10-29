import hashlib
import io
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import List, Optional

from PIL import Image
import typer
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.table import Table

from iPodDB.library.artwork import ArtworkPixels
from iPodDB.library.media import AudioEncoding, prepared_audio, prepared_video
from iPodDB.library.models import MediaType, Track, TrackMetadata
from iPodDB.library.video import is_video_track
from iPodDB.library.writing import (
    ArtworkAsset,
    FileDependency,
    PreparedMedia,
    SourceFile,
    WriteResources,
    WriteTarget,
)
from ipod_cli.allocator import MusicPathAllocator, count_music_directories
from ipod_cli.commands.tracks import _format_duration
from ipod_cli.device import discover_ipod_mounts, inspect_ipod, is_ipod_mount
from ipod_cli.metadata import AudioMetadata, extract_metadata
from ipod_cli.storage_ops import commit_transaction, load_ipod_library
from ipod_cli.transcode import (
    SUPPORTED_VIDEO_EXTS,
    is_ffmpeg_available,
    is_video_file,
    needs_transcoding,
    transcode_to_alac,
    transcode_video,
)
from ipod_cli.ui import console, print_error, print_info, print_success, print_warning

SUPPORTED_AUDIO_EXTS = {
    ".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff",
    ".flac", ".ogg", ".opus", ".wma", ".ape", ".wv",
}
SUPPORTED_MEDIA_EXTS = SUPPORTED_AUDIO_EXTS | SUPPORTED_VIDEO_EXTS


def _collect_files(paths: list[str]) -> list[Path]:
    """Collect all valid audio and video files from paths and directories."""
    results: list[Path] = []
    for p_str in paths:
        p = Path(p_str)
        if p.is_file() and p.suffix.lower() in SUPPORTED_MEDIA_EXTS:
            results.append(p.resolve())
        elif p.is_dir():
            for item in p.rglob("*"):
                if item.is_file() and item.suffix.lower() in SUPPORTED_MEDIA_EXTS:
                    results.append(item.resolve())
    return sorted(list(set(results)))


def add_tracks(
    paths: List[str] = typer.Argument(
        ...,
        help="Audio or video files or directories to add to the iPod.",
    ),
    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="iPod mount point or drive letter (e.g. 'D:\\').",
    ),
    force_alac: bool = typer.Option(
        False,
        "--force-alac",
        help="Force conversion of all audio files to ALAC 44.1kHz (iPod 5.5 Wolfson DAC).",
    ),
    video_res: str = typer.Option(
        "320x240",
        "--video-res",
        "--res",
        help="Target video resolution for videos: '320x240' (native) or '640x480'.",
    ),
    video_bitrate: int = typer.Option(
        768,
        "--video-bitrate",
        help="Video bitrate in kbps (default: 768, max: 1500).",
    ),
    video_kind: str = typer.Option(
        "movie",
        "--video-kind",
        "-k",
        help="Classification for video files: 'movie', 'music-video', or 'tv-show'.",
    ),
    artwork: Optional[str] = typer.Option(
        None,
        "--artwork",
        "--cover",
        help="Image file (JPEG/PNG) to apply as album artwork (auto-resized to 500x500 JPEG).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Simulate track addition without copying files or modifying iPod.",
    ),
) -> None:
    """Add audio tracks and videos to iPod, automatically converting non-native formats."""
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

    device = inspect_ipod(target_mount)

    # 2. Collect input files
    input_files = _collect_files(paths)
    if not input_files:
        print_warning("No supported audio or video files found in provided paths.")
        raise typer.Exit(0)

    print_info(f"Found [bold]{len(input_files)}[/bold] media file(s) to process.")

    # Check artwork override if provided
    artwork_override_bytes: Optional[bytes] = None
    if artwork:
        art_path = Path(artwork)
        if not art_path.is_file():
            print_error(f"Artwork file '{artwork}' not found.")
            raise typer.Exit(1)
        from ipod_cli.artwork import process_artwork_image
        artwork_override_bytes = process_artwork_image(art_path)
        print_info(f"Using artwork override from [bold cyan]{art_path.name}[/bold cyan] (500x500 JPEG).")

    # 3. Load iPod database
    try:
        lib = load_ipod_library(target_mount)
    except Exception as e:
        print_error(f"Failed to load iPod library: {e}")
        raise typer.Exit(1)

    # 4. Set up music path allocator
    existing_locations = [t.metadata.location for t in lib.snapshot.tracks if t.metadata.location]
    dir_count = count_music_directories(target_mount)
    allocator = MusicPathAllocator(directory_count=dir_count, occupied_paths=existing_locations)

    # Album cover mapping for existing artwork reuse
    album_artwork_map: dict[tuple[str, str], int] = {}
    for t in lib.snapshot.tracks:
        if t.artwork_id > 0 and t.album:
            key = (t.artist.casefold().strip(), t.album.casefold().strip())
            album_artwork_map[key] = t.artwork_id

    # Resolve video classification
    vkind_lower = video_kind.lower().strip()
    if vkind_lower in ("music-video", "music_video", "mv"):
        target_video_type = MediaType.MUSIC_VIDEO
    elif vkind_lower in ("tv-show", "tv_show", "tv"):
        target_video_type = MediaType.TV_SHOW
    else:
        target_video_type = MediaType.VIDEO

    # 5. Process files
    temp_dir = Path(tempfile.mkdtemp(prefix="ipod_add_"))
    files_to_copy: list[tuple[Path, Path]] = []
    prepared_medias: list[PreparedMedia] = []
    prepared_artworks: list[ArtworkAsset] = []
    artwork_cache_by_digest: dict[str, int] = {}
    new_tracks: list[Track] = []

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Processing media...", total=len(input_files))

            for idx, src_file in enumerate(input_files, start=1):
                is_video = is_video_file(src_file)
                media_label = "video" if is_video else "audio"
                progress.update(task, description=f"[cyan]Processing {media_label} ({idx}/{len(input_files)}):[/cyan] {src_file.name}")

                temp_track_id = -idx
                process_file = src_file

                if is_video:
                    if needs_transcoding(src_file):
                        if not is_ffmpeg_available():
                            print_error("FFmpeg not found in system PATH. Required for video transcoding.")
                            raise typer.Exit(1)
                        vid_temp = temp_dir / f"vid_{idx}.m4v"
                        transcode_video(
                            source_path=src_file,
                            output_path=vid_temp,
                            resolution=video_res,
                            video_bitrate_kbps=video_bitrate,
                        )
                        process_file = vid_temp

                    meta = extract_metadata(process_file, artwork_override=artwork_override_bytes)
                    ipod_rel_path = allocator.allocate("m4v")
                    ipod_abs_dst = target_mount / ipod_rel_path.replace("/", os.sep)

                    file_dep = FileDependency(ipod_rel_path, meta.size_bytes, meta.sha256)
                    media = prepared_video(temp_track_id, file_dep, has_audio=True)
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

                    track_meta = TrackMetadata(
                        location=ipod_rel_path,
                        sample_rate_hz=48000,
                        file_format="M4V",
                    )

                    new_track = Track(
                        track_id=temp_track_id,
                        title=meta.title or src_file.stem,
                        artist=meta.artist,
                        album=meta.album,
                        album_artist=meta.album_artist,
                        length_ms=meta.length_ms,
                        size_bytes=meta.size_bytes,
                        bitrate_kbps=video_bitrate or meta.bitrate_kbps,
                        year=meta.year,
                        media_types=(target_video_type,),
                        show=meta.show,
                        season_number=meta.season_number,
                        episode_number=meta.episode_number,
                        artwork_id=assigned_artwork_id,
                        metadata=track_meta,
                    )
                else:
                    should_transcode = force_alac or needs_transcoding(src_file)
                    if should_transcode:
                        if not is_ffmpeg_available():
                            print_error("FFmpeg not found in system PATH. Required for transcoding.")
                            raise typer.Exit(1)
                        alac_temp = temp_dir / f"{src_file.stem}_{idx}.m4a"
                        transcode_to_alac(src_file, alac_temp)
                        process_file = alac_temp

                    meta = extract_metadata(process_file, artwork_override=artwork_override_bytes)
                    ext = process_file.suffix.lstrip(".").lower()
                    ipod_rel_path = allocator.allocate(ext)
                    ipod_abs_dst = target_mount / ipod_rel_path.replace("/", os.sep)

                    album_key = (meta.artist.casefold().strip(), meta.album.casefold().strip())
                    inherited_artwork_id = album_artwork_map.get(album_key, 0)

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
                    elif inherited_artwork_id > 0:
                        assigned_artwork_id = inherited_artwork_id

                    file_dep = FileDependency(ipod_rel_path, meta.size_bytes, meta.sha256)
                    media = prepared_audio(temp_track_id, file_dep, meta.encoding)
                    prepared_medias.append(media)

                    track_meta = TrackMetadata(
                        location=ipod_rel_path,
                        sample_rate_hz=meta.sample_rate_hz,
                        file_format=meta.file_format,
                        disc_number=meta.disc_number,
                        total_discs=meta.total_discs,
                        total_tracks=meta.total_tracks,
                        composer=meta.composer,
                    )

                    new_track = Track(
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

                new_tracks.append(new_track)
                files_to_copy.append((process_file, ipod_abs_dst))
                progress.advance(task)

        # 6. Prepare iPodDB snapshot
        print_info("Preparing iPod database transaction...")
        desired_snapshot = replace(lib.snapshot, tracks=(*lib.snapshot.tracks, *new_tracks))
        draft = lib.begin_draft(desired_snapshot)
        write_target = WriteTarget(cover_formats=device.cover_formats)
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
            errors = [i for i in write_result.issues if i.severity.name == "ERROR"]
            if errors:
                print_error(f"Library validation failed with {len(errors)} error(s):")
                for issue in errors:
                    console.print(f"  [red]• {issue.code}[/red]: {issue.message} ({issue.detail or ''})")
                raise typer.Exit(1)

        if write_result.prepared is None:
            print_error("Prepared library returned no output data.")
            raise typer.Exit(1)

        # 7. Show summary table
        table = Table(title="Media to be Added to iPod", border_style="cyan")
        table.add_column("Type", style="magenta")
        table.add_column("Title", style="bold white")
        table.add_column("Artist / Show", style="cyan")
        table.add_column("Album", style="green")
        table.add_column("Duration", justify="right")
        table.add_column("Destination", style="dim")

        for t in new_tracks:
            m_type = "Video" if is_video_track(t) else "Audio"
            table.add_row(
                m_type,
                t.title,
                t.show or t.artist,
                t.album,
                _format_duration(t.length_ms),
                str(t.metadata.location),
            )
        console.print(table)

        if dry_run:
            print_info(f"[yellow]Simulation (--dry-run):[/yellow] {len(new_tracks)} item(s) validated successfully. No disk writes performed.")
            return

        # 8. Transactional commit
        print_info(f"Copying {len(files_to_copy)} file(s) to iPod and updating database...")
        commit_transaction(
            target_mount,
            write_result.prepared.itunes,
            files_to_copy=files_to_copy,
            files_to_delete=[],
            prepared_artwork=write_result.prepared.artwork,
            prepared_artwork_files=write_result.prepared.artwork_files,
        )

        print_success(f"Success! [bold green]{len(new_tracks)}[/bold green] item(s) added to iPod library.")

    finally:
        # Clean up temporary transcoding files
        import shutil
        if temp_dir.exists():
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
