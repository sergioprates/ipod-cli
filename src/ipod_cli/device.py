"""iPod hardware detection and device inspection module."""

from __future__ import annotations

import os
import shutil
import string
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from device_registry import DEFAULT_DEVICE_REGISTRY, parse_sysinfo, parse_sysinfo_extended
from device_registry.models import DeviceEvidence, IdentificationStatus
from iPodDB.library import IPodLibrary
from storage import Storage
from storage.models import ScsiVpdPagePlan

_IPOD_VPD_PLAN = ScsiVpdPagePlan(
    index_page=0xC0,
    first_data_page=0xC2,
    last_data_page=0xFF,
    scan_range_when_index_empty=True,
)


@dataclass
class IPodDevice:
    """Consolidated hardware and library metadata for a detected iPod."""

    mount_point: Path
    volume_label: str
    filesystem: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    model_name: str
    family: str
    generation: str
    model_number: str
    serial_number: str
    firmware_version: str
    finish: str
    device_name: str
    track_count: int
    playlist_count: int
    supports_alac: bool
    supports_artwork: bool
    supports_video: bool
    database_found: bool


def _find_case_insensitive_path(base: Path, *segments: str) -> Optional[Path]:
    """Resolve directory segments case-insensitively across platforms."""
    current = base
    for seg in segments:
        if not current.exists() or not current.is_dir():
            return None
        match = None
        seg_lower = seg.lower()
        try:
            for child in current.iterdir():
                if child.name.lower() == seg_lower:
                    match = child
                    break
        except (PermissionError, OSError):
            return None
        if match is None:
            return None
        current = match
    return current


def is_ipod_mount(path: Path) -> bool:
    """Check if the given directory contains an iPod_Control directory."""
    if not path.exists() or not path.is_dir():
        return False
    control_dir = _find_case_insensitive_path(path, "iPod_Control")
    return control_dir is not None and control_dir.is_dir()


def discover_ipod_mounts() -> list[Path]:
    """Discover all connected mounts that correspond to an iPod device."""
    mounts: list[Path] = []
    seen_paths: set[str] = set()

    # 1. Use the cross-platform Storage layer
    try:
        storage = Storage()
        result = storage.discover()
        for volume in result.volumes:
            path = volume.mount_point.path
            canon = str(path.resolve()).lower()
            if canon not in seen_paths and is_ipod_mount(path):
                seen_paths.add(canon)
                mounts.append(path)
    except Exception:
        pass

    # 2. On Windows, scan drive letters directly in case removable flag is unset
    if sys.platform == "win32":
        for letter in string.ascii_uppercase:
            drive_path = Path(f"{letter}:\\")
            canon = str(drive_path).lower()
            if canon in seen_paths:
                continue
            try:
                if drive_path.exists() and is_ipod_mount(drive_path):
                    seen_paths.add(canon)
                    mounts.append(drive_path)
            except (PermissionError, OSError):
                continue

    # 3. On macOS, scan /Volumes
    if sys.platform == "darwin":
        volumes_dir = Path("/Volumes")
        if volumes_dir.exists():
            for child in volumes_dir.iterdir():
                canon = str(child.resolve()).lower()
                if canon not in seen_paths and is_ipod_mount(child):
                    seen_paths.add(canon)
                    mounts.append(child)
    # 4. On Linux, scan /media and /run/media
    elif sys.platform.startswith("linux"):
        for base in (Path("/media"), Path("/run/media")):
            if base.exists():
                for root, _, _ in os.walk(base):
                    p = Path(root)
                    canon = str(p.resolve()).lower()
                    if canon not in seen_paths and is_ipod_mount(p):
                        seen_paths.add(canon)
                        mounts.append(p)

    return mounts


def inspect_ipod(mount_path: Path) -> IPodDevice:
    """Inspect an iPod mount point and extract hardware, firmware, and library details."""
    control_dir = _find_case_insensitive_path(mount_path, "iPod_Control")
    if control_dir is None:
        raise ValueError(f"Directory 'iPod_Control' was not found on '{mount_path}'.")

    # Disk usage
    try:
        usage = shutil.disk_usage(mount_path)
        total_b, free_b = usage.total, usage.free
        used_b = usage.used
    except Exception:
        total_b, free_b, used_b = 0, 0, 0

    evidence = DeviceEvidence()

    # 1. Read SysInfo on disk
    sysinfo_path = _find_case_insensitive_path(control_dir, "Device", "SysInfo")
    if sysinfo_path and sysinfo_path.exists():
        try:
            content = sysinfo_path.read_bytes()
            evidence = evidence.merged_with(parse_sysinfo(content))
        except Exception:
            pass

    # 2. Hardware SCSI VPD probe directly on storage volume
    try:
        storage = Storage()
        discovered = storage.discover()
        mount_resolved = mount_path.resolve()
        matching_volume = next(
            (v for v in discovered.volumes if v.mount_point.path.resolve() == mount_resolved),
            None,
        )
        if matching_volume is not None:
            probe = storage.probe_hardware(matching_volume, page_plan=_IPOD_VPD_PLAN)
            for observation in probe.observations:
                if observation.vendor_payload:
                    evidence = evidence.merged_with(parse_sysinfo_extended(observation.vendor_payload))
                elif observation.unit_serial:
                    from device_registry.identifiers import normalize_serial
                    from device_registry.models import DeviceIdentifier, EvidenceAuthority
                    norm = normalize_serial(observation.unit_serial)
                    if norm:
                        evidence = evidence.merged_with(
                            DeviceEvidence(
                                product_serials=(
                                    DeviceIdentifier(norm, "scsi_inquiry", EvidenceAuthority.DEVICE_METADATA),
                                )
                            )
                        )
    except Exception:
        pass

    # 3. Match against the official catalog
    model_number = ""
    serial_number = ""
    firmware_version = ""
    model_name = "iPod (Unknown)"
    family = "iPod"
    generation = ""
    finish = ""
    supports_alac = True
    supports_artwork = True
    supports_video = False

    ident = DEFAULT_DEVICE_REGISTRY.identify(evidence)
    if ident.status == IdentificationStatus.EXACT and ident.profile:
        prof = ident.profile
        model_name = f"{prof.family} {prof.generation} ({prof.advertised_capacity})"
        family = prof.family
        generation = prof.generation
        model_number = prof.model_number
        finish = prof.finish
        supports_alac = prof.capabilities.audio.supports_alac
        supports_artwork = prof.capabilities.artwork.supports_cover_art
        supports_video = prof.capabilities.video.supported

    if evidence.product_serials:
        serial_number = evidence.product_serials[0].value
    if evidence.model_numbers and not model_number:
        model_number = evidence.model_numbers[0].value
    if evidence.firmware_versions:
        firmware_version = evidence.firmware_versions[0].value

    # 4. Read iTunesDB database
    itunes_dir = _find_case_insensitive_path(control_dir, "iTunes")
    itunesdb_path = None
    if itunes_dir:
        itunesdb_path = _find_case_insensitive_path(itunes_dir, "iTunesDB")

    track_count = 0
    playlist_count = 0
    device_name = "iPod"
    database_found = False

    if itunesdb_path and itunesdb_path.exists():
        try:
            db_bytes = itunesdb_path.read_bytes()
            library = IPodLibrary(db_bytes)
            snapshot = library.snapshot
            track_count = len(snapshot.tracks)
            playlist_count = len(snapshot.playlists)
            device_name = snapshot.device_name or "iPod"
            database_found = True
        except Exception:
            pass

    return IPodDevice(
        mount_point=mount_path,
        volume_label=mount_path.name or str(mount_path),
        filesystem="FAT32",
        total_bytes=total_b,
        used_bytes=used_b,
        free_bytes=free_b,
        model_name=model_name,
        family=family,
        generation=generation,
        model_number=model_number,
        serial_number=serial_number,
        firmware_version=firmware_version,
        finish=finish,
        device_name=device_name,
        track_count=track_count,
        playlist_count=playlist_count,
        supports_alac=supports_alac,
        supports_artwork=supports_artwork,
        supports_video=supports_video,
        database_found=database_found,
    )
