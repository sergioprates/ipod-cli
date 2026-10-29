"""Audio conversion and transcoding pipeline optimized for iPod 5.5 (Video)."""

from __future__ import annotations

import functools
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_CONVERTIBLE_EXTENSIONS = frozenset({
    ".flac",
    ".ogg",
    ".opus",
    ".wav",
    ".aif",
    ".aiff",
    ".wma",
    ".ape",
    ".wv",
})

_NATIVE_EXTENSIONS = frozenset({
    ".mp3",
    ".m4a",
    ".aac",
    ".m4b",
    ".alac",
})


@dataclass(frozen=True)
class TranscodeResult:
    source_path: Path
    output_path: Path
    converted: bool
    codec: str
    sample_rate: int = 44100


def check_ffmpeg() -> bool:
    """Check if FFmpeg is installed and available in system PATH."""
    return shutil.which("ffmpeg") is not None


is_ffmpeg_available = check_ffmpeg


def needs_transcoding(file_path: Path) -> bool:
    """Determine whether an audio file requires transcoding for iPod 5.5."""
    ext = file_path.suffix.lower()
    if ext in _CONVERTIBLE_EXTENSIONS:
        return True

    # For .m4a and .mp3, verify sample rate does not exceed 44.1kHz or 16-bit
    if ext in (".m4a", ".alac", ".wav"):
        try:
            import mutagen
            audio = mutagen.File(file_path)
            if audio is not None and hasattr(audio.info, "sample_rate"):
                if audio.info.sample_rate > 44100:
                    return True
                if getattr(audio.info, "bits_per_sample", 16) > 16:
                    return True
        except Exception:
            pass

    return False


@functools.lru_cache(maxsize=1)
def check_soxr_support() -> bool:
    """Check if the installed FFmpeg build supports the libsoxr resampler."""
    try:
        res = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=0.01", "-af", "aresample=resampler=soxr", "-f", "null", "-"],
            capture_output=True,
            timeout=5,
        )
        return res.returncode == 0
    except Exception:
        return False


def transcode_to_alac(
    source_path: Path,
    output_path: Path,
    *,
    sample_rate: int = 44100,
) -> TranscodeResult:
    """Convert an audio file to ALAC 44.1kHz 16-bit with high-quality resampling."""
    if not check_ffmpeg():
        raise RuntimeError("FFmpeg was not found in PATH. Install FFmpeg to enable audio conversion.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    has_soxr = check_soxr_support()
    resample_filter = "aresample=resampler=soxr" if has_soxr else f"aresample=osr={sample_rate}"

    # Audiophile pipeline for iPod 5.5 (Wolfson WM8758 DAC)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(source_path),
        "-c:a", "alac",
        "-ar", str(sample_rate),
        "-af", resample_filter,
        "-sample_fmt", "s16p",
        "-map", "0:a:0",
        "-map", "0:v?",
        "-c:v", "copy",
        "-disposition:v", "attached_pic",
        "-map_metadata", "0",
        "-movflags", "+faststart",
        str(output_path),
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError:
        # Fallback if original embedded video/cover stream cannot be copied directly
        fallback_cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(source_path),
            "-c:a", "alac",
            "-ar", str(sample_rate),
            "-af", resample_filter,
            "-sample_fmt", "s16p",
            "-map", "0:a:0",
            "-vn",
            "-map_metadata", "0",
            "-movflags", "+faststart",
            str(output_path),
        ]
        subprocess.run(fallback_cmd, capture_output=True, text=True, check=True)

    return TranscodeResult(
        source_path=source_path,
        output_path=output_path,
        converted=True,
        codec="alac",
        sample_rate=sample_rate,
    )
