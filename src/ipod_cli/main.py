"""Main entry point for the ipod-sync CLI."""

import logging
from pathlib import Path
from typing import Optional

import typer
from rich.panel import Panel
from rich.table import Table

from ipod_cli import __version__
from ipod_cli.commands import add, backup, export, playlists, remove, sync, tracks
from ipod_cli.device import discover_ipod_mounts, inspect_ipod, is_ipod_mount
from ipod_cli.ui import console, error_console, print_banner, print_error, print_info, print_success, print_warning

# Suppress internal diagnostic logs in standard terminal output
logging.basicConfig(level=logging.ERROR, format="%(message)s")

app = typer.Typer(
    name="ipod",
    help="iPod Sync CLI: Manage and synchronize your iPod (Classic, Video, Mini, Nano) without iTunes.",
    add_completion=False,
    no_args_is_help=True,
)

# Register command modules
app.command(name="tracks")(tracks.list_tracks)
app.add_typer(playlists.app, name="playlists")
app.command(name="export")(export.export_tracks)
app.command(name="add")(add.add_tracks)
app.command(name="remove")(remove.remove_tracks)
app.command(name="sync")(sync.sync_library)
app.add_typer(backup.app, name="backup")


def version_callback(value: bool) -> None:
    if value:
        console.print(f"[bold cyan]ipod-sync[/bold cyan] version [bold green]{__version__}[/bold green]")
        raise typer.Exit()


def _format_bytes(bytes_count: int) -> str:
    """Format byte counts into human-readable strings (KB, MB, GB, etc.)."""
    if bytes_count <= 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if bytes_count < 1024:
            return f"{bytes_count:.2f} {unit}"
        bytes_count /= 1024
    return f"{bytes_count:.2f} PB"


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        help="Show ipod-sync version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Enable detailed debug logs.",
    ),
) -> None:
    """Modern headless CLI tool for iPod management."""
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)


@app.command(name="detect")
def detect_cmd() -> None:
    """Scan and detect connected iPod devices automatically."""
    print_banner()
    print_info("Scanning for connected iPod volumes and drives...")

    mounts = discover_ipod_mounts()
    if not mounts:
        print_warning("No iPod device detected automatically.")
        console.print(
            "\n[dim]Tips:\n"
            "1. Make sure your iPod is mounted in Disk Mode or accessible as a drive.\n"
            "2. If you know the drive letter or path, specify it directly: [bold]ipod info --mount <path>[/bold] (e.g. [bold]ipod info -m E:\\\\[/bold])[/dim]\n"
        )
        return

    print_success(f"{len(mounts)} iPod device(s) found!")

    table = Table(title="Connected iPods", border_style="cyan")
    table.add_column("Mount Point", style="bold green")
    table.add_column("Identified Model", style="bold white")
    table.add_column("Device Name", style="cyan")
    table.add_column("Tracks", justify="right", style="magenta")
    table.add_column("Free / Total Space", justify="right")

    for mount in mounts:
        try:
            device = inspect_ipod(mount)
            free_str = _format_bytes(device.free_bytes)
            total_str = _format_bytes(device.total_bytes)
            table.add_row(
                str(device.mount_point),
                device.model_name,
                device.device_name,
                str(device.track_count),
                f"{free_str} / {total_str}",
            )
        except Exception as e:
            table.add_row(str(mount), "[red]Error inspecting[/red]", "-", "-", str(e))

    console.print(table)


@app.command(name="info")
def info_cmd(
    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="iPod mount point or drive letter (e.g. 'D:\\' or '/media/ipod').",
    ),
) -> None:
    """Display comprehensive hardware, firmware, storage, and library details."""
    print_banner()

    target_mount: Optional[Path] = None

    if mount_point:
        p = Path(mount_point)
        if not p.exists():
            print_error(f"The specified path does not exist: '{mount_point}'")
            raise typer.Exit(1)
        if not is_ipod_mount(p):
            print_warning(f"The directory '{mount_point}' does not appear to be an iPod (missing 'iPod_Control').")
            if not typer.confirm("Do you want to inspect anyway?"):
                raise typer.Exit(1)
        target_mount = p
    else:
        mounts = discover_ipod_mounts()
        if not mounts:
            print_error("No iPod detected automatically.")
            console.print("[dim]Use [bold]--mount <path>[/bold] to specify your iPod drive.[/dim]")
            raise typer.Exit(1)
        target_mount = mounts[0]

    clean_mount = str(target_mount).rstrip("/\\")
    print_info(f"Inspecting iPod at [bold]{clean_mount}[/bold]...")

    try:
        dev = inspect_ipod(target_mount)
    except Exception as e:
        print_error(f"Failed to inspect iPod: {e}")
        raise typer.Exit(1)

    # Hardware & Identity Table
    t_hw = Table(show_header=False, box=None, padding=(0, 2))
    t_hw.add_row("[bold cyan]Model:[/bold cyan]", f"[bold white]{dev.model_name}[/bold white]")
    t_hw.add_row("[bold cyan]Family / Generation:[/bold cyan]", f"{dev.family} {dev.generation}".strip() or "N/A")
    if dev.finish:
        t_hw.add_row("[bold cyan]Finish / Color:[/bold cyan]", dev.finish)
    t_hw.add_row("[bold cyan]Model Number:[/bold cyan]", dev.model_number or "N/A")
    t_hw.add_row("[bold cyan]Serial Number:[/bold cyan]", dev.serial_number or "N/A")
    t_hw.add_row("[bold cyan]Firmware Version:[/bold cyan]", dev.firmware_version or "N/A")

    # Storage Table
    t_storage = Table(show_header=False, box=None, padding=(0, 2))
    used_pct = (dev.used_bytes / dev.total_bytes * 100) if dev.total_bytes > 0 else 0
    t_storage.add_row("[bold cyan]Total Capacity:[/bold cyan]", _format_bytes(dev.total_bytes))
    t_storage.add_row("[bold cyan]Used Space:[/bold cyan]", f"{_format_bytes(dev.used_bytes)} ({used_pct:.1f}%)")
    t_storage.add_row("[bold cyan]Free Space:[/bold cyan]", _format_bytes(dev.free_bytes))

    # Capabilities Table
    t_caps = Table(show_header=False, box=None, padding=(0, 2))
    t_caps.add_row("[bold cyan]ALAC (Lossless) Support:[/bold cyan]", "[green]Yes[/green]" if dev.supports_alac else "[yellow]No[/yellow]")
    t_caps.add_row("[bold cyan]Album Artwork Support:[/bold cyan]", "[green]Yes[/green]" if dev.supports_artwork else "[yellow]No[/yellow]")
    t_caps.add_row("[bold cyan]Video Playback:[/bold cyan]", "[green]Yes[/green]" if dev.supports_video else "[dim]No[/dim]")

    # Library Statistics
    t_lib = Table(show_header=False, box=None, padding=(0, 2))
    t_lib.add_row("[bold cyan]Device Name:[/bold cyan]", dev.device_name)
    t_lib.add_row("[bold cyan]Database Status:[/bold cyan]", "[green]iTunesDB Present[/green]" if dev.database_found else "[yellow]Not Found[/yellow]")
    t_lib.add_row("[bold cyan]Total Tracks:[/bold cyan]", str(dev.track_count))
    t_lib.add_row("[bold cyan]Total Playlists:[/bold cyan]", str(dev.playlist_count))

    console.print(Panel(t_hw, title="[bold]Device Hardware Identification[/bold]", border_style="cyan"))
    console.print(Panel(t_storage, title="[bold]Storage & Partition[/bold]", border_style="green"))
    console.print(Panel(t_caps, title="[bold]Supported Capabilities[/bold]", border_style="blue"))
    console.print(Panel(t_lib, title="[bold]Library Statistics[/bold]", border_style="magenta"))


if __name__ == "__main__":
    app()
