"""Artwork processing, resizing, and embedding pipeline optimized for iPod 5.5.

Why 500x500 JPEG at 85% Quality is the Ideal Format for iPod:
- Native Screen & UI: The iPod Video (5.5th Gen) features a 320x240 display. The "Now Playing"
  album art box is 100x100 px (or up to 200x200 px in full-screen/dock/iTunes views).
- Memory / SDRAM Limits: iPod 5.5 has 32MB (30GB model) or 64MB (80GB model) of SDRAM.
  Storing uncompressed or giant cover art (e.g. 2000x2000 4MB PNGs) consumes RAM, slows down
  track browsing, increases battery drain, and can crash the firmware memory allocator.
- Optimal File Size: A 500x500 JPEG at 85% quality takes only 40 KB to 80 KB, delivering
  pin-sharp visual rendering with virtually zero storage overhead.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

from PIL import Image

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover

DEFAULT_ARTWORK_SIZE = 500
DEFAULT_ARTWORK_QUALITY = 85

ADJACENT_COVER_NAMES = (
    "cover.jpg", "cover.jpeg", "cover.png", "cover.webp",
    "folder.jpg", "folder.jpeg", "folder.png",
    "front.jpg", "front.jpeg", "front.png",
    "album.jpg", "album.jpeg", "album.png",
    "art.jpg", "art.png",
)


def process_artwork_image(
    image_input: bytes | str | Path,
    target_size: int = DEFAULT_ARTWORK_SIZE,
    quality: int = DEFAULT_ARTWORK_QUALITY,
) -> bytes:
    """Resize and optimize an image to iPod ideal format: JPEG 500x500 max, quality 85.

    - Handles transparent PNGs/WebP by applying a clean white background.
    - Preserves aspect ratio, scaling the largest dimension down to target_size.
    - Encodes to baseline progressive JPEG with quality=85.
    """
    if isinstance(image_input, (str, Path)):
        img = Image.open(image_input)
    else:
        img = Image.open(io.BytesIO(image_input))

    # Convert to RGB (flatten transparency if RGBA / LA / P)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[3])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Resize preserving aspect ratio (Mode.Max)
    img.thumbnail((target_size, target_size), Image.Resampling.LANCZOS)

    output = io.BytesIO()
    img.save(output, format="JPEG", quality=quality, optimize=True)
    return output.getvalue()


def find_adjacent_artwork(media_path: Path) -> Optional[Path]:
    """Find cover art images in the same folder as the media file."""
    parent = media_path.parent
    if not parent.is_dir():
        return None

    # Check common album art filenames
    for name in ADJACENT_COVER_NAMES:
        candidate = parent / name
        if candidate.is_file():
            return candidate

    # Look for any image file if there is an unambiguous candidate
    images = [
        f for f in parent.iterdir()
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
    ]
    if len(images) == 1:
        return images[0]

    return None


def extract_embedded_artwork(media_path: Path) -> Optional[bytes]:
    """Extract embedded artwork from an audio or video file."""
    path = Path(media_path)
    try:
        audio = mutagen.File(path)
    except Exception:
        return None

    if audio is None:
        return None

    # MP4 / M4A / M4V format
    if isinstance(audio, MP4) or "covr" in getattr(audio, "tags", {}):
        covers = audio.tags.get("covr")
        if covers and len(covers) > 0:
            return bytes(covers[0])

    # ID3 / MP3 format
    if hasattr(audio, "tags") and hasattr(audio.tags, "getall"):
        pics = audio.tags.getall("APIC")
        if pics:
            return bytes(pics[0].data)

    # FLAC format
    if hasattr(audio, "pictures") and audio.pictures:
        return bytes(audio.pictures[0].data)

    return None


def embed_artwork(
    media_file: Path,
    image_source: bytes | str | Path,
    target_size: int = DEFAULT_ARTWORK_SIZE,
    quality: int = DEFAULT_ARTWORK_QUALITY,
) -> bool:
    """Resize image to 500x500 JPEG 85% and embed it into the media file's tags."""
    path = Path(media_file)
    if not path.is_file():
        raise FileNotFoundError(f"Media file '{media_file}' not found.")

    jpeg_bytes = process_artwork_image(image_source, target_size=target_size, quality=quality)
    ext = path.suffix.lower()

    if ext in (".m4a", ".mp4", ".m4v", ".alac", ".m4b"):
        mp4 = MP4(path)
        mp4["covr"] = [MP4Cover(jpeg_bytes, imageformat=MP4Cover.FORMAT_JPEG)]
        mp4.save()
        return True

    elif ext == ".mp3":
        audio = MP3(path)
        if audio.tags is None:
            audio.add_tags()
        audio.tags.delall("APIC")
        audio.tags.add(
            APIC(
                encoding=3,  # UTF-8
                mime="image/jpeg",
                type=3,  # Front cover
                desc="Cover",
                data=jpeg_bytes,
            )
        )
        audio.save()
        return True

    elif ext == ".flac":
        audio = FLAC(path)
        pic = Picture()
        pic.type = 3  # Front cover
        pic.mime = "image/jpeg"
        pic.desc = "Cover"
        pic.data = jpeg_bytes
        audio.clear_pictures()
        audio.add_picture(pic)
        audio.save()
        return True

    return False


def resize_existing_artwork(
    media_file: Path,
    target_size: int = DEFAULT_ARTWORK_SIZE,
    quality: int = DEFAULT_ARTWORK_QUALITY,
) -> bool:
    """Extract existing artwork from media file, resize to 500x500 JPEG 85%, and save back.

    Direct drop-in equivalent for TagLib ResizeArtWork routine.
    """
    path = Path(media_file)
    existing_data = extract_embedded_artwork(path)
    if not existing_data:
        return False

    return embed_artwork(path, existing_data, target_size=target_size, quality=quality)


def ensure_track_artwork(
    media_file: Path,
    fallback_image: Optional[Path] = None,
    target_size: int = DEFAULT_ARTWORK_SIZE,
    quality: int = DEFAULT_ARTWORK_QUALITY,
) -> bool:
    """Ensure media file has artwork. If missing, embeds fallback or folder cover."""
    path = Path(media_file)
    if extract_embedded_artwork(path) is not None:
        return False  # Already has artwork

    image_to_use = fallback_image or find_adjacent_artwork(path)
    if image_to_use and Path(image_to_use).is_file():
        return embed_artwork(path, image_to_use, target_size=target_size, quality=quality)

    return False
