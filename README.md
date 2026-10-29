# ipod-sync

A modern, high-fidelity, and 100% headless command-line interface for managing and synchronizing music on iPod devices (Classic, Video 5th/5.5th Gen, Mini, Nano) directly from your terminal—without iTunes.

Built on the low-level database engine of [TheRealSavi/iOpenPod](https://github.com/TheRealSavi/iOpenPod) — featuring native binary parsing and serialization for `iTunesDB`, `ArtworkDB`, and transactional disk integrity, but completely free of GUI and desktop dependencies (PySide6 / Qt removed).

---

## ⚡ Quickstart (with `uv`)

The project is structured to run immediately via [`uv`](https://docs.astral.sh/uv/) without manual virtualenv creation or dependency management:

```bash
# 1. Clone the repository
git clone https://github.com/sergioprates/ipod-cli.git
cd ipod-cli

# 2. Run CLI commands directly with uv
uv run ipod detect
uv run ipod info
uv run ipod tracks
```

*(Alternatively, install in editable mode with traditional pip: `pip install -e .`)*

---

## 🎧 Audiophile Profile (iPod 5.5th Gen / Wolfson WM8758 DAC)

For the iPod Video 5.5th Gen (celebrated for its Wolfson WM8758 digital-to-analog converter), `ipod-sync` includes a high-fidelity transcoding pipeline powered by FFmpeg:

* **Audio Codec**: Apple Lossless (`-c:a alac`) in 16-bit PCM (`-sample_fmt s16p`).
* **Sampling Rate**: 44,100 Hz with high-precision **SoX** resampling (`aresample=resampler=soxr`) and automatic fallback to high-quality SWR.
* **Metadata & Cover Art**: Full preservation of ID3/MP4 tags and embedded album art within the M4A container (`-c:v copy -disposition:v attached_pic`).
* **Spin-up Optimization**: `-movflags +faststart` moves the `moov` header atom to the beginning of the file, preventing hard drive spin-up lag during playback.

Formats such as **FLAC**, **OGG**, **OPUS**, **WMA**, **APE**, or files exceeding 44.1kHz/16-bit are automatically detected and converted before transfer.

---

## 🛠️ CLI Command Reference

### 1. `ipod detect`
Scans USB buses and system mount points, identifying connected iPods with 100% precision using SCSI VPD hardware probing.
```bash
uv run ipod detect
```

### 2. `ipod info`
Displays hardware specifications, exact catalog model (e.g. iPod 5.5th Gen 30GB Black MA446), serial number, firmware version, partition storage, and library statistics.
```bash
uv run ipod info
uv run ipod info -m D:\
```

### 3. `ipod tracks`
Lists tracks stored in the iPod database with filtering options and structured JSON export.
```bash
# List first 20 tracks
uv run ipod tracks --limit 20

# Filter by artist or album
uv run ipod tracks --artist "Daft Punk"
uv run ipod tracks --album "Discovery"

# Full-text search
uv run ipod tracks --search "love"

# Export library to JSON
uv run ipod tracks --json > tracks.json
```

### 4. `ipod playlists`
Manage iPod playlists: query, inspect, create, modify, rename, and delete.
```bash
# List all playlists
uv run ipod playlists
uv run ipod playlists --json

# Show details and track listing of a playlist
uv run ipod playlists show "Rock Hits"
uv run ipod playlists show "Rock Hits" --json

# Create a new playlist (optionally filtering tracks to add immediately)
uv run ipod playlists create "Rock Hits" 3079 3082
uv run ipod playlists create "Ariana Mix" --search "Ariana"
uv run ipod playlists create "Synthwave" --artist "Kavinsky" --dry-run

# Append tracks to an existing playlist
uv run ipod playlists add-tracks "Rock Hits" 3084 3086
uv run ipod playlists add-tracks "Rock Hits" --search "Daft Punk"

# Remove tracks from a playlist (does NOT delete audio files from disk)
uv run ipod playlists remove-tracks "Rock Hits" 3079
uv run ipod playlists remove-tracks "Rock Hits" --all

# Rename a playlist
uv run ipod playlists rename "Rock Hits" "80s Rock"

# Delete a playlist (does NOT delete audio files from disk)
uv run ipod playlists remove "80s Rock" --yes
```

### 5. `ipod export`
Extracts audio tracks from the iPod, reconstructing original filenames and folder structures: `Artist/Album/01 - Title.ext`.
```bash
# Dry run simulation
uv run ipod export ./recovered_music --artist "Ariana Grande" --dry-run

# Perform extraction
uv run ipod export ./recovered_music --artist "Ariana Grande"
```

### 6. `ipod add`
Adds individual tracks or entire folders to the iPod, transcoding non-native formats automatically.
```bash
# Add a single audio file (with preview)
uv run ipod add ./album/track.flac --dry-run

# Add an entire folder, converting all tracks to ALAC 44.1kHz
uv run ipod add ./music_collection --force-alac

# Add native files (MP3, M4A, etc.) directly
uv run ipod add ./new_tracks/
```

### 7. `ipod remove`
Removes tracks from the iPod library and deletes their corresponding physical files inside `iPod_Control/Music/Fxx/`.
```bash
# Remove by track ID
uv run ipod remove 780 781

# Remove by search query (prompts for confirmation)
uv run ipod remove --search "Old Album"

# Non-interactive removal
uv run ipod remove --search "Old Album" --yes

# Dry run preview
uv run ipod remove --search "Old Album" --dry-run
```

### 8. `ipod sync`
Synchronizes a local music directory with the iPod, adding new tracks and optionally removing omitted ones.
```bash
# Dry run simulation of sync plan
uv run ipod sync "C:\Users\User\Music" --dry-run

# Synchronize local tracks to iPod
uv run ipod sync "C:\Users\User\Music"

# Mirror synchronization (deletes tracks on iPod that were removed locally)
uv run ipod sync "C:\Users\User\Music" --delete
```

### 9. `ipod backup`
Manages backups of the iPod's `iTunesDB` and device control folders.
```bash
# Create a full backup archive
uv run ipod backup

# List available local backups
uv run ipod backup list

# Restore a backup
uv run ipod backup restore "ipod_backups/iPod_backup_20261029.zip"
```

---

## 🔒 Data Integrity & Atomic Transactions

* **Pre-write Safety Backups**: Every write operation (`add`, `remove`, `sync`) automatically creates a timestamped backup (`iTunesDB.bak` on the iPod and a copy in `ipod_backups/pre_sync/`).
* **Atomic Replacement**: Database updates are written to `iTunesDB.tmp` and atomically replaced, ensuring unexpected disconnections never corrupt the iPod partition.
* **Path Allocation**: The `MusicPathAllocator` guarantees collision-free filenames conforming to the `F00-F49` 4-character uppercase FAT32 structure.

---

## 📜 License

GPL-3.0-or-later — see [LICENSE](LICENSE) for details.
