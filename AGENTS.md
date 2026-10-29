# AGENTS.md — Agent Operation & Development Guide

Welcome, AI Agent (Hermes, Claude, Antigravity, etc.)! This document provides complete context, operational standards, machine-readable interfaces, and architectural safety rules for interacting with and developing `ipod-sync`.

---

## 1. Project Mission & Identity

- **Name**: `ipod-sync` (CLI binary: `ipod` / `ipod-sync`)
- **Repository**: [sergioprates/ipod-cli](https://github.com/sergioprates/ipod-cli)
- **Purpose**: A 100% headless, high-fidelity CLI to inspect, manage, convert, and synchronize music on iPod hardware (Classic, Video 5th/5.5th Gen, Mini, Nano) directly from the terminal without iTunes.
- **Lineage**: Extracted and adapted from [TheRealSavi/iOpenPod](https://github.com/TheRealSavi/iOpenPod). All GUI/desktop dependencies (PySide6, Qt, GUI event loops) have been completely removed.

---

## 2. Environment & Execution Contract

Agents should invoke all operations using [`uv`](https://docs.astral.sh/uv/) for instant, reproducible execution without manual virtual environment management:

```bash
# Verify installation & command list
uv run ipod --help

# Detect attached iPod hardware
uv run ipod detect

# Inspect detailed hardware & database metadata
uv run ipod info
```

For programmatic consumption by agents, use `--json` or non-interactive flags where applicable.

---

## 3. Machine-Readable Interfaces (For Agent Automation)

When querying the iPod state to make autonomous decisions, agents should request structured JSON output:

### List tracks as JSON:
```bash
uv run ipod tracks --json
uv run ipod tracks --artist "Daft Punk" --json
uv run ipod tracks --limit 100 --json
```

**JSON Output Format**:
```json
[
  {
    "id": 3848,
    "title": "7 rings",
    "artist": "Ariana Grande",
    "album": "thank u, next",
    "genre": "Pop",
    "year": 2019,
    "track_number": 7,
    "duration_ms": 178000,
    "bitrate_kbps": 256,
    "location": "iPod_Control/Music/F12/ABCD.m4a"
  }
]
```

### List playlists as JSON:
```bash
uv run ipod playlists --json
```

### Inspect specific playlist and tracks as JSON:
```bash
uv run ipod playlists show "Rock Hits" --json
uv run ipod playlists show 12815454400400274375 --json
```

### List videos as JSON:
```bash
uv run ipod video list --json
```

### Safe Pre-execution Simulations:
Always run with `--dry-run` before modifying disk state on physical hardware:
```bash
# Preview what would be added and transcoded
uv run ipod add ./new_music --dry-run
uv run ipod video add ./movies/clip.mkv --dry-run

# Preview sync diff (additions, deletions, in-sync counts)
uv run ipod sync ./local_library --dry-run

# Preview track removal
uv run ipod remove --search "Obsolete" --dry-run

# Preview playlist operations
uv run ipod playlists create "New Mix" --search "Daft Punk" --dry-run
uv run ipod playlists add-tracks "New Mix" 3084 --dry-run
uv run ipod playlists remove "Obsolete Playlist" --dry-run
```

### Non-Interactive Automation:
In headless agent environments, pass `--yes` / `-y` to skip terminal confirmation prompts:
```bash
uv run ipod remove 780 781 --yes
uv run ipod playlists remove "Obsolete Playlist" --yes
uv run ipod sync ./local_library --yes
uv run ipod backup restore "ipod_backups/backup.zip" --yes
```

---

## 4. Architecture & Source Code Layout

```
ipod-sync/
├── AGENTS.md                  # Instructions and context for AI agents
├── README.md                  # Public documentation & user quickstart
├── pyproject.toml             # Hatchling build definition & dependencies
├── src/
│   ├── ipod_cli/              # CLI interface and user-facing workflows
│   │   ├── main.py            # Typer app entry point & detect/info commands
│   │   ├── ui.py              # Rich console output, banners, color tokens
│   │   ├── device.py          # Device discovery, SCSI VPD hardware probing
│   │   ├── allocator.py       # MusicPathAllocator (F00-F49 FAT32 path generator)
│   │   ├── metadata.py        # Tag extraction via Mutagen (MP3, MP4, FLAC, WAV)
│   │   ├── storage_ops.py     # Safe atomic transactions, stage & pre-write backups
│   │   ├── transcode.py       # FFmpeg audio pipeline (ALAC 44.1kHz SoX / faststart)
│   │   └── commands/
│   │       ├── add.py         # 'ipod add' command
│   │       ├── remove.py      # 'ipod remove' command
│   │       ├── sync.py        # 'ipod sync' command
│   │       ├── tracks.py      # 'ipod tracks' command
│   │       ├── playlists.py   # 'ipod playlists' command
│   │       ├── export.py      # 'ipod export' command
│   │       └── backup.py      # 'ipod backup' command (create, list, restore)
│   ├── iPodDB/                # Low-level binary database engine
│   │   ├── library/           # IPodLibrary, snapshot models, draft/plan/prepare
│   │   ├── iTunesDB/          # Binary MHBD/MHSD/MHIT/MHOD parser & serializer
│   │   └── ArtworkDB/         # Binary MHFD/MHII/MHIF parser & Ithmb decoders
│   ├── device_registry/       # Official Apple iPod hardware database & catalog
│   └── storage/               # Cross-platform raw block I/O & SCSI VPD inquiry
```

---

## 5. Critical Hardware & Safety Rules for Agents

When generating code, running commands, or modifying files:

1. **Never write directly to `iTunesDB`**:
   - Always use `storage_ops.commit_transaction()`.
   - Never overwrite `iPod_Control/iTunes/iTunesDB` without writing to `iTunesDB.tmp` first, followed by an atomic rename/replace.
   - Always verify that a pre-write backup exists in `ipod_backups/pre_sync/` and `iTunesDB.bak`.

2. **Respect Physical Hardware Constraints (iPod Video 5.5th Gen)**:
   - **Audio Sample rate**: 44,100 Hz (44.1kHz) or 48,000 Hz. Higher sample rates cause audio stuttering.
   - **Audio Bit depth**: 16-bit PCM (`-sample_fmt s16p`). 24-bit audio is not supported by standard iPod firmware.
   - **Faststart**: Always pass `-movflags +faststart` for M4A/ALAC/M4V so that the `moov` atom is placed at byte 0.
   - **Video Codec**: Strictly H.264 Baseline Profile with `-profile:v baseline -level 1.3` (320x240) or `-level 3.0` (640x480).
   - **Video Pixel Format**: Strictly 8-bit YUV 4:2:0 (`-pix_fmt yuv420p`). Other pixel formats (such as 4:4:4 or RGB) will fail on the iPod decoder.
   - **Video Framerate & Bitrate**: Max 30 fps; video bitrate default 768 kbps (max 1500 kbps); AAC-LC audio up to 160 kbps 48 kHz stereo.

3. **Database Preparation Protocol (`iPodDB.library`)**:
   - To add an audio track:
     1. Build `TrackMetadata` with `location="iPod_Control/Music/Fxx/XXXX.ext"`, `sample_rate_hz=44100`, and `file_format="M4A"` (or `"MP3"`).
     2. Build `Track(track_id=-idx, ...)` with negative ID.
     3. Build `PreparedMedia(track_id, FileDependency(...), ...)` using `prepared_audio()`.
     4. Call `desired = replace(lib.snapshot, tracks=(*lib.snapshot.tracks, *new_tracks))`.
     5. Call `draft = lib.begin_draft(desired)`.
     6. Call `plan = lib.analyze(draft)`.
     7. Call `res = lib.prepare(plan, WriteResources(media=..., pending_playback_sidecars=False))`.
     8. Verify `res.issues` is empty before committing.
   - To add a video track:
     1. Build `TrackMetadata` with `location="iPod_Control/Music/Fxx/XXXX.m4v"`, `sample_rate_hz=48000`, and `file_format="M4V"`.
     2. Build `Track(track_id=-idx, media_types=(MediaType.VIDEO,), ...)` (or `MediaType.MUSIC_VIDEO` / `MediaType.TV_SHOW`).
     3. Build `PreparedMedia` using `prepared_video(track_id, FileDependency(...), has_audio=True)`.
     4. Commit via `lib.prepare()` and `commit_transaction()`.
   - To remove a track:
     - Always pass `lib.begin_draft(desired, delete_omissions=True)`. Without `delete_omissions=True`, the database validation will correctly flag a `draft.deletion_not_enabled` error.

4. **Preserve Headless Independence**:
   - Under no circumstances import `PySide6`, `PyQt`, `tkinter`, or GUI modules.
   - The CLI must run on headless CI servers, SSH remote sessions, and autonomous agent sandboxes.

5. **Windows FAT32 Path Quirks**:
   - `iPod_Control` has hidden and system attributes on Windows. When checking directories, use case-insensitive resolution and do not rely on standard non-hidden globbing.
   - Strip trailing backslashes before formatting strings inside Rich markup tags (e.g. `[bold]{clean_mount}[/bold]`) to prevent Windows `\` from escaping Rich closing brackets.
