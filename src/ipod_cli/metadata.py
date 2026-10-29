"""Extração e normalização de metadados de áudio com Mutagen."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import mutagen
from mutagen.mp4 import MP4, MP4Cover

from iPodDB.library.media import AudioEncoding


@dataclass(frozen=True, slots=True)
class AudioMetadata:
    """Metadados extraídos de um arquivo de áudio."""

    title: str
    artist: str
    album: str
    album_artist: str = ""
    genre: str = ""
    composer: str = ""
    year: int = 0
    track_number: int = 0
    total_tracks: int = 0
    disc_number: int = 0
    total_discs: int = 0
    length_ms: int = 0
    size_bytes: int = 0
    bitrate_kbps: int = 0
    sample_rate_hz: int = 44100
    file_format: str = "M4A"
    encoding: AudioEncoding = AudioEncoding.ALAC
    sha256: str = ""
    artwork_data: Optional[bytes] = None


def _parse_track_disc(value: str) -> tuple[int, int]:
    """Interpreta números como '3' ou '3/12'."""
    if not value:
        return 0, 0
    match = re.match(r"(\d+)(?:/(\d+))?", str(value).strip())
    if match:
        num = int(match.group(1))
        total = int(match.group(2)) if match.group(2) else 0
        return num, total
    return 0, 0


def _parse_year(value: str) -> int:
    """Extrai o ano de 4 dígitos (ex: '2024-05-10' -> 2024)."""
    if not value:
        return 0
    match = re.search(r"\b(\d{4})\b", str(value))
    return int(match.group(1)) if match else 0


def _compute_sha256(path: Path) -> str:
    """Calcula hash SHA-256 do arquivo."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(131072):
            hasher.update(chunk)
    return hasher.hexdigest()


def extract_metadata(file_path: Path) -> AudioMetadata:
    """Extrai metadados completos de um arquivo MP3, M4A, ALAC, WAV, etc."""
    path = Path(file_path)
    size_bytes = path.stat().st_size
    sha256 = _compute_sha256(path)

    title = path.stem
    artist = "Desconhecido"
    album = "Álbum Desconhecido"
    album_artist = ""
    genre = ""
    composer = ""
    year = 0
    track_number = 0
    total_tracks = 0
    disc_number = 0
    total_discs = 0
    length_ms = 0
    bitrate_kbps = 256
    sample_rate_hz = 44100
    artwork_data: Optional[bytes] = None

    ext = path.suffix.lower()
    file_format = "M4A"
    encoding = AudioEncoding.ALAC

    if ext == ".mp3":
        file_format = "MP3"
        encoding = AudioEncoding.MP3
    elif ext in (".wav", ".wave"):
        file_format = "WAV"
        encoding = AudioEncoding.WAV
    elif ext in (".aif", ".aiff"):
        file_format = "AIFF"
        encoding = AudioEncoding.AIFF
    elif ext in (".m4a", ".mp4", ".m4b", ".alac"):
        file_format = "M4A"
        # Mutagen MP4 identifica se é ALAC ou AAC
        try:
            mp4 = MP4(path)
            # Se o codec for alac nos átomos
            atoms = mp4.pprint()
            if "alac" in atoms.lower():
                encoding = AudioEncoding.ALAC
            else:
                encoding = AudioEncoding.AAC
        except Exception:
            encoding = AudioEncoding.ALAC

    try:
        audio = mutagen.File(path)
    except Exception:
        audio = None

    if audio is not None:
        if hasattr(audio, "info") and audio.info:
            if hasattr(audio.info, "length"):
                length_ms = int(audio.info.length * 1000)
            if hasattr(audio.info, "bitrate") and audio.info.bitrate:
                bitrate_kbps = int(audio.info.bitrate / 1000)
            if hasattr(audio.info, "sample_rate") and audio.info.sample_rate:
                sample_rate_hz = int(audio.info.sample_rate)

        tags = audio.tags
        if tags:
            # Formato MP4 / M4A / ALAC
            if isinstance(audio, MP4) or "©nam" in tags:
                if "©nam" in tags and tags["©nam"]:
                    title = str(tags["©nam"][0])
                if "©ART" in tags and tags["©ART"]:
                    artist = str(tags["©ART"][0])
                if "©alb" in tags and tags["©alb"]:
                    album = str(tags["©alb"][0])
                if "aART" in tags and tags["aART"]:
                    album_artist = str(tags["aART"][0])
                if "©gen" in tags and tags["©gen"]:
                    genre = str(tags["©gen"][0])
                if "©wrt" in tags and tags["©wrt"]:
                    composer = str(tags["©wrt"][0])
                if "©day" in tags and tags["©day"]:
                    year = _parse_year(str(tags["©day"][0]))
                if "trkn" in tags and tags["trkn"]:
                    t = tags["trkn"][0]
                    if isinstance(t, tuple) and len(t) >= 2:
                        track_number, total_tracks = t[0], t[1]
                if "disk" in tags and tags["disk"]:
                    d = tags["disk"][0]
                    if isinstance(d, tuple) and len(d) >= 2:
                        disc_number, total_discs = d[0], d[1]
                if "covr" in tags and tags["covr"]:
                    cov = tags["covr"][0]
                    artwork_data = bytes(cov)

            # Formato ID3 / MP3
            elif hasattr(tags, "getall"):
                if "TIT2" in tags:
                    title = str(tags["TIT2"].text[0])
                if "TPE1" in tags:
                    artist = str(tags["TPE1"].text[0])
                if "TALB" in tags:
                    album = str(tags["TALB"].text[0])
                if "TPE2" in tags:
                    album_artist = str(tags["TPE2"].text[0])
                if "TCON" in tags:
                    genre = str(tags["TCON"].text[0])
                if "TCOM" in tags:
                    composer = str(tags["TCOM"].text[0])
                if "TDRC" in tags:
                    year = _parse_year(str(tags["TDRC"].text[0]))
                elif "TYER" in tags:
                    year = _parse_year(str(tags["TYER"].text[0]))
                if "TRCK" in tags:
                    track_number, total_tracks = _parse_track_disc(str(tags["TRCK"].text[0]))
                if "TPOS" in tags:
                    disc_number, total_discs = _parse_track_disc(str(tags["TPOS"].text[0]))
                pics = tags.getall("APIC")
                if pics:
                    artwork_data = bytes(pics[0].data)

            # Formato Vorbis / FLAC (caso tenha sido transcrito mantendo tags)
            else:
                title = str(tags.get("title", [title])[0])
                artist = str(tags.get("artist", [artist])[0])
                album = str(tags.get("album", [album])[0])
                album_artist = str(tags.get("albumartist", [tags.get("album_artist", [""])])[0])
                genre = str(tags.get("genre", [""])[0])
                composer = str(tags.get("composer", [""])[0])
                year = _parse_year(str(tags.get("date", ["0"])[0]))
                track_number, total_tracks = _parse_track_disc(str(tags.get("tracknumber", ["0"])[0]))
                disc_number, total_discs = _parse_track_disc(str(tags.get("discnumber", ["0"])[0]))
                if hasattr(audio, "pictures") and audio.pictures:
                    artwork_data = bytes(audio.pictures[0].data)

    return AudioMetadata(
        title=title.strip() or path.stem,
        artist=artist.strip() or "Desconhecido",
        album=album.strip() or "Álbum Desconhecido",
        album_artist=album_artist.strip(),
        genre=genre.strip(),
        composer=composer.strip(),
        year=year,
        track_number=track_number,
        total_tracks=total_tracks,
        disc_number=disc_number,
        total_discs=total_discs,
        length_ms=length_ms,
        size_bytes=size_bytes,
        bitrate_kbps=bitrate_kbps,
        sample_rate_hz=sample_rate_hz or 44100,
        file_format=file_format,
        encoding=encoding,
        sha256=sha256,
        artwork_data=artwork_data,
    )
