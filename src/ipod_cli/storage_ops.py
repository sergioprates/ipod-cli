"""Safe transactional read and write operations for iPod storage."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.progress import Progress, SpinnerColumn, TextColumn

from iPodDB.library import IPodLibrary
from iPodDB.library.models import LibrarySnapshot, Track
from iPodDB.library.writing import PreparedLibrary
from ipod_cli.ui import console, print_error, print_info, print_success, print_warning

logger = logging.getLogger(__name__)


def get_ipod_paths(mount_point: Path) -> dict[str, Path]:
    """Return core structural paths for an iPod mount."""
    control = mount_point / "iPod_Control"
    return {
        "control": control,
        "itunes": control / "iTunes",
        "itunesdb": control / "iTunes" / "iTunesDB",
        "music": control / "Music",
        "artwork": control / "Artwork",
        "artworkdb": control / "Artwork" / "ArtworkDB",
    }


def load_ipod_library(mount_point: Path) -> IPodLibrary:
    """Load the IPodLibrary from an iPod mount point."""
    paths = get_ipod_paths(mount_point)
    itunes_file = paths["itunesdb"]

    if not itunes_file.exists():
        raise FileNotFoundError(
            f"iTunesDB database file not found at '{itunes_file}'."
        )

    itunes_data = itunes_file.read_bytes()
    lib = IPodLibrary(itunes_data)

    artwork_file = paths["artworkdb"]
    if artwork_file.exists():
        try:
            lib = lib.with_artwork(artwork_file.read_bytes())
        except Exception as e:
            logger.warning("Could not load ArtworkDB: %s", e)

    return lib


def save_pre_write_backup(mount_point: Path) -> Path:
    """Create a fast automated backup of iTunesDB prior to any write."""
    paths = get_ipod_paths(mount_point)
    itunes_file = paths["itunesdb"]

    backup_dir = Path("ipod_backups") / "pre_sync"
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_dest = backup_dir / f"iTunesDB_auto_{timestamp}.bak"
    if itunes_file.exists():
        shutil.copy2(itunes_file, bak_dest)

    # Also maintain a .bak directly on the iPod filesystem
    ipod_bak = itunes_file.with_name("iTunesDB.bak")
    try:
        shutil.copy2(itunes_file, ipod_bak)
    except Exception:
        pass

    return bak_dest


def commit_transaction(
    mount_point: Path,
    prepared_itunes: bytes,
    files_to_copy: list[tuple[Path, Path]],  # (src_local, dst_ipod)
    files_to_delete: list[Path] | None = None,
) -> None:
    """Commit changes to the iPod using transactional atomic file operations."""
    paths = get_ipod_paths(mount_point)
    itunes_file = paths["itunesdb"]
    itunes_tmp = itunes_file.with_name("iTunesDB.tmp")

    # 1. Automatic pre-write backup
    save_pre_write_backup(mount_point)

    # 2. Copy new audio files into destination Fxx directories
    copied_successfully: list[Path] = []
    try:
        for src, dst in files_to_copy:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied_successfully.append(dst)

        # 3. Write new iTunesDB into temporary file
        itunes_tmp.write_bytes(prepared_itunes)

        # 4. Atomically replace the existing iTunesDB
        if itunes_tmp.exists():
            itunes_tmp.replace(itunes_file)

        # 5. Physically delete removed tracks (if requested)
        if files_to_delete:
            for f in files_to_delete:
                if f.exists() and f.is_file():
                    try:
                        f.unlink()
                    except Exception as e:
                        logger.warning("Failed to delete file %s: %s", f, e)

    except Exception as exc:
        print_error(f"Error while committing changes to iPod: {exc}")
        # Rollback: remove temporary file if present
        if itunes_tmp.exists():
            try:
                itunes_tmp.unlink()
            except Exception:
                pass
        # Remove newly copied files from the failed transaction
        for copied in copied_successfully:
            if copied.exists():
                try:
                    copied.unlink()
                except Exception:
                    pass
        raise
