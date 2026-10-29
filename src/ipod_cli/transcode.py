"""Módulo de conversão e transcodificação de áudio otimizado para iPod 5.5 (Video)."""

from __future__ import annotations

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
    """Verifica se o FFmpeg está instalado e disponível no PATH."""
    return shutil.which("ffmpeg") is not None


is_ffmpeg_available = check_ffmpeg


def needs_transcoding(file_path: Path) -> bool:
    """Verifica se o arquivo de áudio necessita de conversão para o iPod 5.5."""
    ext = file_path.suffix.lower()
    if ext in _CONVERTIBLE_EXTENSIONS:
        return True

    # Para .m4a e .mp3, verifica se a taxa de amostragem ultrapassa 44.1kHz ou 48kHz
    if ext in (".m4a", ".alac", ".wav"):
        try:
            import mutagen
            audio = mutagen.File(file_path)
            if audio is not None and hasattr(audio.info, "sample_rate"):
                if audio.info.sample_rate > 44100:
                    return True
                # Arquivos de 24-bit ou superior causam engasgo no iPod 5.5
                if getattr(audio.info, "bits_per_sample", 16) > 16:
                    return True
        except Exception:
            pass

    return False


import functools


@functools.lru_cache(maxsize=1)
def check_soxr_support() -> bool:
    """Verifica se o FFmpeg instalado possui suporte ao resampler libsoxr."""
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
    """Converte um arquivo de áudio para ALAC 44.1kHz 16-bit com resampler SoX."""
    if not check_ffmpeg():
        raise RuntimeError("FFmpeg não foi encontrado no PATH. Instale o FFmpeg para habilitar a conversão.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    has_soxr = check_soxr_support()
    resample_filter = "aresample=resampler=soxr" if has_soxr else f"aresample=osr={sample_rate}"

    # Pipeline de alta fidelidade para iPod 5.5 (DAC Wolfson WM8758)
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
        # Fallback se a capa do vídeo original não puder ser copiada diretamente
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
