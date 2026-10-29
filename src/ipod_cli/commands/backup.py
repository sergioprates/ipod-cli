"""Command for fast backup and restoration of iPod database and system folders."""

from __future__ import annotations

import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from ipod_cli.device import discover_ipod_mounts, inspect_ipod, is_ipod_mount
from ipod_cli.ui import console, print_banner, print_error, print_info, print_success, print_warning

app = typer.Typer(name="backup", help="Create and restore backup copies of the iPod database.")


@app.callback(invoke_without_command=True)
def create_backup(
    ctx: typer.Context,
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Target file or directory to save the backup (.zip).",
    ),
    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="iPod mount point or drive letter (e.g. 'D:\\').",
    ),
) -> None:
    """Create a compressed zip backup of the iPod's iTunes and Device control folders."""
    if ctx.invoked_subcommand is not None:
        return

    print_banner()

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
            raise typer.Exit(1)
        target_mount = mounts[0]

    dev = inspect_ipod(target_mount)
    clean_mount = str(target_mount).rstrip("/\\")
    print_info(f"Starting backup of [bold]{dev.device_name}[/bold] ({clean_mount})...")

    # Generate backup filename
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_name = "".join(c for c in dev.device_name if c.isalnum() or c in (" ", "_", "-")).strip() or "ipod"
    backup_filename = f"{clean_name}_backup_{now_str}.zip"

    if output is None:
        backup_dir = Path("ipod_backups")
        backup_dir.mkdir(parents=True, exist_ok=True)
        zip_path = backup_dir / backup_filename
    elif output.is_dir():
        output.mkdir(parents=True, exist_ok=True)
        zip_path = output / backup_filename
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        zip_path = output

    control_dir = target_mount / "iPod_Control"
    items_to_backup = ["iTunes", "Device"]

    file_count = 0
    total_bytes = 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for folder_name in items_to_backup:
            folder_path = control_dir / folder_name
            if not folder_path.exists():
                folder_path = control_dir / folder_name.lower()
            if not folder_path.exists():
                continue

            for root, _, files in os.walk(folder_path):
                for f in files:
                    file_p = Path(root) / f
                    arcname = f"iPod_Control/{Path(root).relative_to(control_dir)}/{f}".replace("\\", "/")
                    zipf.write(file_p, arcname)
                    file_count += 1
                    total_bytes += file_p.stat().st_size

    mb_size = total_bytes / (1024 * 1024)
    print_success(
        f"Backup successfully created at: [bold cyan]{zip_path}[/bold cyan] "
        f"({file_count} files, {mb_size:.2f} MB)"
    )


@app.command(name="list")
def list_backups(
    backup_dir: Path = typer.Option(
        Path("ipod_backups"),
        "--dir",
        "-d",
        help="Directory where backups are stored.",
    ),
) -> None:
    """List available local iPod backup files."""
    if not backup_dir.exists():
        print_info("No backup directory found.")
        return

    backups = sorted(list(backup_dir.glob("*.zip")), key=lambda p: p.stat().st_mtime, reverse=True)
    if not backups:
        print_info(f"No zip backup files found in '{backup_dir}'.")
        return

    table = Table(title=f"iPod Backups in {backup_dir}", border_style="cyan")
    table.add_column("Filename", style="bold white")
    table.add_column("Size", justify="right", style="green")
    table.add_column("Date Created", style="dim")

    for b in backups:
        st = b.stat()
        mb = st.st_size / (1024 * 1024)
        mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        table.add_row(b.name, f"{mb:.2f} MB", mtime)

    console.print(table)


@app.command(name="restore")
def restore_backup(
    backup_file: Path = typer.Argument(
        ...,
        help="Path to the .zip backup file to restore.",
    ),
    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="iPod mount point or drive letter (e.g. 'D:\\').",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Confirm restore non-interactively.",
    ),
) -> None:
    """Restore iTunes and Device folders from a .zip backup archive."""
    print_banner()

    if not backup_file.exists():
        print_error(f"Backup file not found: '{backup_file}'")
        raise typer.Exit(1)

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
            raise typer.Exit(1)
        target_mount = mounts[0]

    dev = inspect_ipod(target_mount)
    clean_mount = str(target_mount).rstrip("/\\")
    print_warning(
        f"Warning: Restoration will overwrite current database on [bold]{dev.device_name}[/bold] ({clean_mount})!"
    )
    if not yes:
        if not typer.confirm("Are you sure you want to restore this backup?"):
            print_info("Operation cancelled by user.")
            return

    control_dir = target_mount / "iPod_Control"
    print_info(f"Extracting files into [bold]{control_dir}[/bold]...")

    with zipfile.ZipFile(backup_file, "r") as zipf:
        for member in zipf.infolist():
            if member.filename.startswith("iPod_Control/"):
                rel_path = member.filename[len("iPod_Control/"):]
                if not rel_path:
                    continue
                dest = control_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zipf.open(member) as src_f, open(dest, "wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f)

    print_success("Restore completed successfully! The iPod database has been restored.")
