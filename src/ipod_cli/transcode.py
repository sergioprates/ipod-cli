"""Audio and video conversion and transcoding pipeline optimized for iPod 5.5 (Video)."""

from __future__ import annotations

import functools
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

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

_NATIVE_AUDIO_EXTENSIONS = frozenset({
    ".mp3",
    ".m4a",
    ".aac",
    ".m4b",
    ".alac",
})

SUPPORTED_VIDEO_EXTS = frozenset({
    ".mp4",
    ".m4v",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".flv",
    ".wmv",
    ".ts",
    ".mpg",
    ".mpeg",
    ".3gp",
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


def check_ffprobe() -> bool:
    """Check if FFprobe is installed and available in system PATH."""
    return shutil.which("ffprobe") is not None


def is_video_file(file_path: Path) -> bool:
    """Determine whether a file is a recognized video container."""
    return file_path.suffix.lower() in SUPPORTED_VIDEO_EXTS


def probe_media_file(file_path: Path) -> Dict[str, Any]:
    """Inspect media file format and streams using ffprobe."""
    if not check_ffprobe():
        return {}

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(file_path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode == 0 and res.stdout.strip():
            return json.loads(res.stdout)
    except Exception:
        pass
    return {}


def is_ipod_compatible_video(file_path: Path) -> bool:
    """Check if a video file is already 100% compliant with iPod 5.5 hardware."""
    ext = file_path.suffix.lower()
    if ext not in (".mp4", ".m4v", ".mov"):
        return False

    info = probe_media_file(file_path)
    if not info:
        return False

    streams = info.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if not video_stream:
        return False

    v_codec = video_stream.get("codec_name", "").lower()
    profile = video_stream.get("profile", "").lower()
    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))

    # Must be H.264 baseline or MPEG-4 Simple Profile
    is_h264_baseline = v_codec == "h264" and ("baseline" in profile or "constrained baseline" in profile)
    is_mpeg4_sp = v_codec == "mpeg4"

    if not (is_h264_baseline or is_mpeg4_sp):
        return False

    # iPod 5.5 supports up to 640x480 (or 320x240)
    if width > 640 or height > 480:
        return False

    # Check audio stream if present
    if audio_stream:
        a_codec = audio_stream.get("codec_name", "").lower()
        if a_codec != "aac":
            return False
        sample_rate = int(audio_stream.get("sample_rate", 0))
        if sample_rate > 48000:
            return False

    return True


def needs_transcoding(file_path: Path) -> bool:
    """Determine whether an audio or video file requires transcoding for iPod 5.5."""
    ext = file_path.suffix.lower()

    if is_video_file(file_path):
        return not is_ipod_compatible_video(file_path)

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
        subprocess.run(cmd, capture_output=True, text=True, check=True)
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


def transcode_video(
    source_path: Path,
    output_path: Path,
    *,
    resolution: str = "320x240",
    video_bitrate_kbps: int = 768,
    audio_bitrate_kbps: int = 160,
    fps: int = 30,
    sample_rate: int = 48000,
    fit: str = "pad",
) -> TranscodeResult:
    """Convert a video file to an iPod 5.5 compatible H.264 Baseline M4V.

    Hardware specs for iPod 5.5th Gen:
    - H.264 Baseline Profile, level 1.3 (up to 320x240) or level 3.0 (up to 640x480)
    - Pixel format: yuv420p (required for iPod hardware decoder)
    - Video bitrate: default 768 kbps, max 1500 kbps
    - Framerate: 30 fps
    - Audio: AAC-LC stereo up to 160 kbps, 48 kHz
    - Container: MP4/M4V with +faststart
    """
    if not check_ffmpeg():
        raise RuntimeError("FFmpeg was not found in PATH. Install FFmpeg to enable video conversion.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Parse resolution (e.g. "320x240" or "640x480")
    if "x" in resolution.lower():
        parts = resolution.lower().split("x", 1)
        w, h = int(parts[0]), int(parts[1])
    else:
        w, h = 320, 240

    level = "1.3" if (w <= 320 and h <= 240) else "3.0"

    # Aspect ratio preserving scaling filter
    if fit == "stretch":
        vf = f"scale={w}:{h},setsar=1"
    elif fit == "crop":
        vf = f"scale=w={w}:h={h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
    else:  # default "pad": preserves original aspect ratio with clean black bars
        vf = f"scale=w={w}:h={h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(source_path),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-b:v", f"{video_bitrate_kbps}k",
        "-maxrate", "1500k",
        "-bufsize", "2000k",
        "-r", str(fps),
        "-profile:v", "baseline",
        "-level", level,
        "-vf", vf,
        "-c:a", "aac",
        "-ar", str(sample_rate),
        "-rematrix_maxval", "1.0",
        "-ac", "2",
        "-b:a", f"{audio_bitrate_kbps}k",
        "-movflags", "+faststart",
        "-f", "mp4",
        "-map_metadata", "0",
        str(output_path),
    ]

    subprocess.run(cmd, capture_output=True, text=True, check=True)

    return TranscodeResult(
        source_path=source_path,
        output_path=output_path,
        converted=True,
        codec="h264_baseline",
        sample_rate=sample_rate,
    )
