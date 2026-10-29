"""Command for managing and optimizing artwork on audio and video files."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
from rich.panel import Panel
from rich.table import Table

from ipod_cli.artwork import (
    DEFAULT_ARTWORK_QUALITY,
    DEFAULT_ARTWORK_SIZE,
    embed_artwork,
    extract_embedded_artwork,
    find_adjacent_artwork,
    process_artwork_image,
    resize_existing_artwork,
)
from ipod_cli.ui import console, print_banner, print_error, print_info, print_success, print_warning

app = typer.Typer(
    name="artwork",
    help="Embed, resize (500x500 JPEG 85%), extract, and manage track artwork for iPod.",
    no_args_is_help=True,
)


@app.command(name="set")
def set_artwork_cmd(
    media_file: str = typer.Argument(..., help="Path to audio or video file (MP3, M4A, M4V, FLAC)."),
    image_file: str = typer.Argument(..., help="Path to image file (JPEG, PNG, WebP, etc.)."),
    size: int = typer.Option(DEFAULT_ARTWORK_SIZE, "--size", "-s", help="Max width/height in px (default: 500)."),
    quality: int = typer.Option(DEFAULT_ARTWORK_QUALITY, "--quality", "-q", help="JPEG quality 1-100 (default: 85)."),
) -> None:
    """Resize image to 500x500 JPEG 85% and embed it into the audio/video file."""
    m_path = Path(media_file)
    i_path = Path(image_file)

    if not m_path.is_file():
        print_error(f"Media file '{media_file}' not found.")
        raise typer.Exit(1)
    if not i_path.is_file():
        print_error(f"Image file '{image_file}' not found.")
        raise typer.Exit(1)

    print_banner()
    print_info(f"Optimizing artwork: [bold white]{i_path.name}[/bold white] -> {size}x{size} JPEG (quality {quality})")
    print_info(f"Target media file: [cyan]{m_path.name}[/cyan]")

    try:
        success = embed_artwork(m_path, i_path, target_size=size, quality=quality)
        if success:
            print_success(f"Success! Artwork embedded into [bold green]{m_path.name}[/bold green].")
        else:
            print_warning(f"File format '{m_path.suffix}' does not support embedded artwork tagging.")
    except Exception as exc:
        print_error(f"Failed to embed artwork: {exc}")
        raise typer.Exit(1)


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
