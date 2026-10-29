"""Commands for converting and transferring videos to the iPod (Classic / Video 5.5)."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import List, Optional

import typer
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.table import Table

from iPodDB.library.media import prepared_video
from iPodDB.library.models import MediaType, Track, TrackMetadata
from iPodDB.library.video import is_video_track
from iPodDB.library.writing import FileDependency, PreparedMedia, WriteResources
from ipod_cli.allocator import MusicPathAllocator, count_music_directories
from ipod_cli.commands.tracks import _format_duration, _load_library
from ipod_cli.device import discover_ipod_mounts, inspect_ipod, is_ipod_mount
from ipod_cli.metadata import AudioMetadata, extract_metadata
from ipod_cli.storage_ops import commit_transaction, load_ipod_library
from ipod_cli.transcode import (
    SUPPORTED_VIDEO_EXTS,
    check_ffmpeg,
    is_ipod_compatible_video,
    is_video_file,
    transcode_video,
)
from ipod_cli.ui import console, print_banner, print_error, print_info, print_success, print_warning

app = typer.Typer(
    name="video",
    help="Convert videos to iPod 5.5 H.264 Baseline specs and manage iPod videos.",
    no_args_is_help=True,
)


def _collect_video_files(paths: list[str]) -> list[Path]:
    """Collect all valid video files from arguments and directories."""
    results: list[Path] = []
    for p_str in paths:
        p = Path(p_str)
        if p.is_file() and is_video_file(p):
            results.append(p.resolve())
        elif p.is_dir():
            for item in p.rglob("*"):
                if item.is_file() and is_video_file(item):
                    results.append(item.resolve())
    return sorted(list(set(results)))


@app.command(name="convert")
def convert_video_cmd(
    source: str = typer.Argument(..., help="Path to the input video file (MKV, MP4, AVI, MOV, WebM, etc.)."),
    output: Optional[str] = typer.Argument(None, help="Optional output path (.m4v). Defaults to '<name>_ipod.m4v'."),
    resolution: str = typer.Option("320x240", "--res", "-r", help="Target resolution: '320x240' (iPod screen native) or '640x480'."),
    bitrate: int = typer.Option(768, "--bitrate", "-b", help="Target video bitrate in kbps (max 1500)."),
    audio_bitrate: int = typer.Option(160, "--audio-bitrate", "-ab", help="Target AAC audio bitrate in kbps (max 160)."),
    fps: int = typer.Option(30, "--fps", help="Framerate in frames per second (max 30)."),
    fit: str = typer.Option("pad", "--fit", help="Aspect ratio mode: 'pad' (black bars), 'stretch', or 'crop'."),
) -> None:
    """Convert any video file to an iPod 5.5 compatible H.264 Baseline M4V file."""
    src_path = Path(source)
    if not src_path.is_file():
        print_error(f"Input file '{source}' not found.")
        raise typer.Exit(1)

    if not check_ffmpeg():
        print_error("FFmpeg was not found in PATH. Install FFmpeg to enable video conversion.")
        raise typer.Exit(1)

    out_path = Path(output) if output else src_path.with_name(f"{src_path.stem}_ipod.m4v")

    print_banner()
    print_info(f"Converting video for iPod 5.5: [bold white]{src_path.name}[/bold white]")
    print_info(f"Specs: H.264 Baseline Profile | {resolution} | {bitrate} kbps | {fps} fps | AAC {audio_bitrate} kbps")

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task(f"Encoding {src_path.name} -> {out_path.name}...", total=None)
            transcode_video(
                source_path=src_path,
                output_path=out_path,
                resolution=resolution,
                video_bitrate_kbps=bitrate,
                audio_bitrate_kbps=audio_bitrate,
                fps=fps,
                fit=fit,
            )

        out_size_mb = out_path.stat().st_size / (1024 * 1024)
        print_success(f"Conversion complete! Output saved to: [bold green]{out_path}[/bold green] ({out_size_mb:.2f} MB)")
    except Exception as exc:
        print_error(f"Video conversion failed: {exc}")
        raise typer.Exit(1)


@app.command(name="add")
def add_video_cmd(
    paths: List[str] = typer.Argument(..., help="Video file(s) or folder(s) to convert and transfer to the iPod."),
    mount_point: Optional[str] = typer.Option(None, "--mount", "-m", help="iPod mount point or drive letter."),
    kind: str = typer.Option("movie", "--kind", "-k", help="Video classification: 'movie', 'music-video', or 'tv-show'."),
    title: Optional[str] = typer.Option(None, "--title", help="Override title for single video."),
    artist: Optional[str] = typer.Option(None, "--artist", help="Override artist / director."),
    album: Optional[str] = typer.Option(None, "--album", help="Override album / collection."),
    show: Optional[str] = typer.Option(None, "--show", help="TV Show title (if adding TV show)."),
    season: Optional[int] = typer.Option(None, "--season", help="Season number (if adding TV show)."),
    episode: Optional[int] = typer.Option(None, "--episode", help="Episode number (if adding TV show)."),
    resolution: str = typer.Option("320x240", "--res", "-r", help="Target resolution: '320x240' or '640x480'."),
    bitrate: int = typer.Option(768, "--bitrate", "-b", help="Target video bitrate in kbps."),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Simulate video addition without modifying iPod."),
) -> None:
    """Convert and transfer videos to the iPod under Movies, Music Videos, or TV Shows."""
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

    # 2. Collect input video files
    video_files = _collect_video_files(paths)
    if not video_files:
        print_warning("No supported video files found in the provided paths.")
        raise typer.Exit(0)

    print_banner()
    print_info(f"Found [bold]{len(video_files)}[/bold] video file(s) to process.")

    # 3. Load iPod library
    try:
        lib = load_ipod_library(target_mount)
    except Exception as e:
        print_error(f"Failed to load iPod library: {e}")
        raise typer.Exit(1)

    # 4. Set up music path allocator
    existing_locations = [t.metadata.location for t in lib.snapshot.tracks if t.metadata.location]
    dir_count = count_music_directories(target_mount)
    allocator = MusicPathAllocator(directory_count=dir_count, occupied_paths=existing_locations)

    # Resolve target media type
    kind_lower = kind.lower().strip()
    if kind_lower in ("music-video", "music_video", "mv"):
        target_media_type = MediaType.MUSIC_VIDEO
        category_name = "Music Video"
    elif kind_lower in ("tv-show", "tv_show", "tv"):
        target_media_type = MediaType.TV_SHOW
        category_name = "TV Show"
    else:
        target_media_type = MediaType.VIDEO
        category_name = "Movie"

    # 5. Process and transcode videos
    temp_dir = Path(tempfile.mkdtemp(prefix="ipod_video_"))
    files_to_copy: list[tuple[Path, Path]] = []
    prepared_medias: list[PreparedMedia] = []
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
            task = progress.add_task("Processing videos...", total=len(video_files))

            for idx, src_file in enumerate(video_files, start=1):
                progress.update(task, description=f"[cyan]Encoding video ({idx}/{len(video_files)}):[/cyan] {src_file.name}")

                is_compliant = is_ipod_compatible_video(src_file)
                process_file = src_file

                if not is_compliant:
                    if not check_ffmpeg():
                        print_error("FFmpeg not found in PATH. Required for video transcoding.")
                        raise typer.Exit(1)
                    m4v_temp = temp_dir / f"vid_{idx}.m4v"
                    transcode_video(
                        source_path=src_file,
                        output_path=m4v_temp,
                        resolution=resolution,
                        video_bitrate_kbps=bitrate,
                    )
                    process_file = m4v_temp

                meta = extract_metadata(process_file)
                ipod_rel_path = allocator.allocate("m4v")
                ipod_abs_dst = target_mount / ipod_rel_path.replace("/", os.sep)

                temp_track_id = -idx
                file_dep = FileDependency(ipod_rel_path, meta.size_bytes, meta.sha256)
                media = prepared_video(temp_track_id, file_dep, has_audio=True)
                prepared_medias.append(media)

                # Apply command line overrides if provided
                track_title = title if (title and len(video_files) == 1) else (meta.title or src_file.stem)
                track_artist = artist or meta.artist
                track_album = album or meta.album
                tv_show = show or meta.show
                season_num = season if season is not None else meta.season_number
                episode_num = episode if episode is not None else meta.episode_number

                track_meta = TrackMetadata(
                    location=ipod_rel_path,
                    sample_rate_hz=48000,
                    file_format="M4V",
                )

                new_track = Track(
                    track_id=temp_track_id,
                    title=track_title,
                    artist=track_artist,
                    album=track_album,
                    length_ms=meta.length_ms,
                    size_bytes=meta.size_bytes,
                    bitrate_kbps=bitrate or meta.bitrate_kbps,
                    year=meta.year,
                    media_types=(target_media_type,),
                    show=tv_show,
                    season_number=season_num,
                    episode_number=episode_num,
                    metadata=track_meta,
                )
                new_tracks.append(new_track)
                files_to_copy.append((process_file, ipod_abs_dst))

                progress.advance(task)

        # 6. Database draft and preparation
        desired = replace(lib.snapshot, tracks=(*lib.snapshot.tracks, *new_tracks))
        draft = lib.begin_draft(desired)
        plan = lib.analyze(draft)

        if plan.blocked:
            print_error(f"Cannot add videos: database analysis reported {len(plan.issues)} blocker(s):")
            for issue in plan.issues:
                console.print(f"  [red]• {issue.code}[/red]: {issue.message} ({issue.detail or ''})")
            raise typer.Exit(1)

        write_resources = WriteResources(
            media=tuple(prepared_medias),
            pending_playback_sidecars=False,
        )
        write_result = lib.prepare(plan, write_resources)

        if write_result.issues:
            print_error(f"Video preparation validation failed with {len(write_result.issues)} issue(s):")
            for issue in write_result.issues:
                console.print(f"  [red]• {issue.code}[/red]: {issue.message} ({issue.detail or ''})")
            raise typer.Exit(1)

        if write_result.prepared is None:
            print_error("Prepared library returned no output data.")
            raise typer.Exit(1)

        # 7. Summary Table
        table = Table(title="Videos to be Added to iPod", border_style="cyan")
        table.add_column("Title", style="bold white")
        table.add_column("Category", style="magenta")
        table.add_column("Duration", justify="right")
        table.add_column("Destination", style="dim")

        for t, (_, dst) in zip(new_tracks, files_to_copy):
            table.add_row(
                t.title,
                category_name,
                _format_duration(t.length_ms),
                str(t.metadata.location),
            )
        console.print(table)

        if dry_run:
            print_info(f"[yellow]Simulation (--dry-run):[/yellow] {len(new_tracks)} video(s) validated successfully. No disk writes performed.")
            return

        # 8. Transactional commit
        print_info(f"Transferring {len(files_to_copy)} video file(s) and updating iPod database...")
        commit_transaction(
            target_mount,
            write_result.prepared.itunes,
            files_to_copy=files_to_copy,
            files_to_delete=[],
        )

        print_success(
            f"Success! [bold green]{len(new_tracks)}[/bold green] video(s) added to iPod [bold cyan]({category_name})[/bold cyan]."
        )

    finally:
        # Clean up temporary transcoding directory
        import shutil
        if temp_dir.exists():
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass


@app.command(name="list")
def list_videos_cmd(
    mount_point: Optional[str] = typer.Option(None, "--mount", "-m", help="iPod mount point or drive letter."),
    as_json: bool = typer.Option(False, "--json", help="Output videos as structured JSON."),
) -> None:
    """List all videos (Movies, Music Videos, TV Shows) present on the iPod."""
    mount_path, library = _load_library(mount_point)
    all_tracks = library.snapshot.tracks
    video_tracks = [t for t in all_tracks if is_video_track(t)]

    if as_json:
        data = [
            {
                "id": t.track_id,
                "title": t.title,
                "artist": t.artist,
                "album": t.album,
                "kind": t.media_kind.value,
                "duration_ms": t.length_ms,
                "bitrate_kbps": t.bitrate_kbps,
                "size_bytes": t.size_bytes,
                "location": t.metadata.location,
                "show": t.show,
                "season": t.season_number,
                "episode": t.episode_number,
            }
            for t in video_tracks
        ]
        console.print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    print_banner()
    clean_mount = str(mount_path).rstrip("/\\")
    print_info(f"Videos found on [bold]{clean_mount}[/bold] ({len(video_tracks)} total):")

    if not video_tracks:
        print_warning("No videos found on the iPod.")
        return

    table = Table(title="iPod Videos", border_style="cyan")
    table.add_column("ID", justify="right", style="dim")
    table.add_column("Title", style="bold white")
    table.add_column("Category", style="cyan")
    table.add_column("Show / Series", style="green")
    table.add_column("Duration", justify="right", style="magenta")
    table.add_column("Bitrate", justify="right")

    for t in video_tracks:
        category = t.media_kind.value.replace("_", " ").capitalize()
        table.add_row(
            str(t.track_id),
            t.title or "[Untitled Video]",
            category,
            t.show or "-",
            _format_duration(t.length_ms),
            f"{t.bitrate_kbps} kbps" if t.bitrate_kbps else "-",
        )

    console.print(table)
