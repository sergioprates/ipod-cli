"""Audio and video metadata extraction and tag normalization with Mutagen and FFprobe."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import mutagen
from mutagen.mp4 import MP4

from iPodDB.library.media import AudioEncoding
from ipod_cli.artwork import find_adjacent_artwork, process_artwork_image
from ipod_cli.transcode import is_video_file, probe_media_file


@dataclass(frozen=True, slots=True)
class AudioMetadata:
    """Consolidated media file metadata for audio and video tracks."""

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
    is_video: bool = False
    show: str = ""
    episode: str = ""
    season_number: int = 0
    episode_number: int = 0


def _parse_track_disc(value: str) -> tuple[int, int]:
    """Parse track or disc representations such as '3' or '3/12'."""
    if not value:
        return 0, 0
    match = re.match(r"(\d+)(?:/(\d+))?", str(value).strip())
    if match:
        num = int(match.group(1))
        total = int(match.group(2)) if match.group(2) else 0
        return num, total
    return 0, 0


def _parse_year(value: str) -> int:
    """Extract a 4-digit release year (e.g. '2024-05-10' -> 2024)."""
    if not value:
        return 0
    match = re.search(r"\b(\d{4})\b", str(value))
    return int(match.group(1)) if match else 0


def _compute_sha256(path: Path) -> str:
    """Calculate the SHA-256 hash of a file."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(131072):
            hasher.update(chunk)
    return hasher.hexdigest()


def extract_metadata(
    file_path: Path,
    artwork_override: Optional[Path | bytes] = None,
    auto_find_artwork: bool = True,
) -> AudioMetadata:
    """Extract metadata from audio or video files (MP3, M4A, M4V, FLAC, MKV, MP4, etc.)."""
    path = Path(file_path)
    size_bytes = path.stat().st_size
    sha256 = _compute_sha256(path)

    title = path.stem
    artist = "Unknown Artist"
    album = "Unknown Album"
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
    is_video = is_video_file(path)
    show = ""
    episode = ""
    season_number = 0
    episode_number = 0

    ext = path.suffix.lower()
    file_format = "M4A"
    encoding = AudioEncoding.ALAC

    if is_video:
        file_format = "M4V"
        encoding = AudioEncoding.AAC
        sample_rate_hz = 48000
    elif ext == ".mp3":
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
        try:
            mp4 = MP4(path)
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
            # MP4 / M4A / M4V format
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
                # Video tags in MP4
                if "tvsh" in tags and tags["tvsh"]:
                    show = str(tags["tvsh"][0])
                if "tven" in tags and tags["tven"]:
                    episode = str(tags["tven"][0])
                if "tvsn" in tags and tags["tvsn"]:
                    season_number = int(tags["tvsn"][0])
                if "tves" in tags and tags["tves"]:
                    episode_number = int(tags["tves"][0])

            # ID3 / MP3 format
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

            # Vorbis / FLAC / Matroska format
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

    # If mutagen could not read length (e.g. video files like MKV/AVI/WebM)
    if length_ms == 0:
        probe_info = probe_media_file(path)
        if probe_info:
            fmt = probe_info.get("format", {})
            try:
                if "duration" in fmt:
                    length_ms = int(float(fmt["duration"]) * 1000)
                if "bit_rate" in fmt:
                    bitrate_kbps = int(int(fmt["bit_rate"]) / 1000)
            except Exception:
                pass

            tags = fmt.get("tags", {})
            if tags:
                title = tags.get("title", title)
                artist = tags.get("artist", artist)
                album = tags.get("album", album)
                if "date" in tags:
                    year = _parse_year(tags["date"])

    # Artwork resolution and optimization (target 500x500 JPEG 85%)
    if artwork_override:
        try:
            artwork_data = process_artwork_image(artwork_override)
        except Exception:
            pass
    elif artwork_data:
        try:
            artwork_data = process_artwork_image(artwork_data)
        except Exception:
            pass
    elif auto_find_artwork:
        adjacent = find_adjacent_artwork(path)
        if adjacent:
            try:
                artwork_data = process_artwork_image(adjacent)
            except Exception:
                pass

    return AudioMetadata(
        title=title.strip() or path.stem,
        artist=artist.strip() or ("Unknown Artist" if not is_video else "Unknown"),
        album=album.strip() or ("Unknown Album" if not is_video else "Videos"),
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
        sample_rate_hz=sample_rate_hz or 48000 if is_video else 44100,
        file_format=file_format,
        encoding=encoding,
        sha256=sha256,
        artwork_data=artwork_data,
        is_video=is_video,
        show=show,
        episode=episode,
        season_number=season_number,
        episode_number=episode_number,
    )
