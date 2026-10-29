"""Safe transactional read and write operations for iPod storage."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.progress import Progress, SpinnerColumn, TextColumn

import os
from iPodDB.library import IPodLibrary
from iPodDB.library.file_content import content_chunks
from iPodDB.library.models import LibrarySnapshot, Track
from iPodDB.library.writing import PreparedFile, PreparedLibrary
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
    """Create a fast automated backup of iTunesDB and ArtworkDB prior to any write."""
    paths = get_ipod_paths(mount_point)
    itunes_file = paths["itunesdb"]
    artwork_file = paths["artworkdb"]

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

    if artwork_file.exists():
        try:
            art_bak = backup_dir / f"ArtworkDB_auto_{timestamp}.bak"
            shutil.copy2(artwork_file, art_bak)
            shutil.copy2(artwork_file, artwork_file.with_name("ArtworkDB.bak"))
        except Exception:
            pass

    return bak_dest


def commit_transaction(
    mount_point: Path,
    prepared_itunes: bytes,
    files_to_copy: list[tuple[Path, Path]],  # (src_local, dst_ipod)
    files_to_delete: list[Path] | None = None,
    prepared_artwork: bytes | None = None,
    prepared_artwork_files: tuple[PreparedFile, ...] | None = None,
) -> None:
    """Commit changes to the iPod using transactional atomic file operations."""
    paths = get_ipod_paths(mount_point)
    itunes_file = paths["itunesdb"]
    itunes_tmp = itunes_file.with_name("iTunesDB.tmp")
    artwork_file = paths["artworkdb"]
    artwork_tmp = artwork_file.with_name("ArtworkDB.tmp")

    # 1. Automatic pre-write backup
    save_pre_write_backup(mount_point)

    # 2. Copy new audio/video files into destination Fxx directories
    copied_successfully: list[Path] = []
    artwork_files_written: list[Path] = []
    try:
        for src, dst in files_to_copy:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied_successfully.append(dst)

        # 3. Write and replace artwork files (.ithmb) if present
        if prepared_artwork_files:
            for pf in prepared_artwork_files:
                target_dest = mount_point / pf.relative_path.replace("/", os.sep)
                target_dest.parent.mkdir(parents=True, exist_ok=True)
                tmp_dest = target_dest.with_name(f"{target_dest.name}.tmp")
                with open(tmp_dest, "wb") as out_fp:
                    for chunk in content_chunks(pf.data):
                        out_fp.write(chunk)
                tmp_dest.replace(target_dest)
                artwork_files_written.append(target_dest)

        # 4. Write new ArtworkDB if present
        if prepared_artwork is not None:
            artwork_file.parent.mkdir(parents=True, exist_ok=True)
            artwork_tmp.write_bytes(prepared_artwork)
            if artwork_tmp.exists():
                artwork_tmp.replace(artwork_file)

        # 5. Write new iTunesDB into temporary file and atomically replace
        itunes_tmp.write_bytes(prepared_itunes)
        if itunes_tmp.exists():
            itunes_tmp.replace(itunes_file)

        # 6. Physically delete removed tracks (if requested)
        if files_to_delete:
            for f in files_to_delete:
                if f.exists() and f.is_file():
                    try:
                        f.unlink()
                    except Exception as e:
                        logger.warning("Failed to delete file %s: %s", f, e)

    except Exception as exc:
        print_error(f"Error while committing changes to iPod: {exc}")
        # Rollback: remove temporary files if present
        for tmp_f in (itunes_tmp, artwork_tmp):
            if tmp_f.exists():
                try:
                    tmp_f.unlink()
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
