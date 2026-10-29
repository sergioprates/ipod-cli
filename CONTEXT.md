# CONTEXT.md — Technical Architecture & Domain Context

This document provides deep technical background on iPod firmware, the binary database specifications, hardware characteristics, and the design decisions behind `ipod-sync`.

---

## 1. Domain Background: The iPod Ecosystem

Standard Apple iPods (Generations 1 through 7, Mini, and Nano 1st through 5th Gen) do not read music files simply by scanning the filesystem. Instead, the firmware reads a proprietary binary database file stored in `iPod_Control/iTunes/`.

### Key Filesystem Structures:

- **`iPod_Control/iTunes/iTunesDB`**:
  - The primary binary database containing all track metadata, playback counters, playlists, and file locations.
  - Formatted in little-endian binary chunks, identified by 4-byte magic headers:
    - `mhbd` (*Database Header*): Root container for the entire database.
    - `mhsd` (*Dataset Header*): Groups records into datasets (Dataset 1: Tracks, Dataset 2: Playlists, Dataset 3: Podcasts, Dataset 4: Album/Artist browse indexes).
    - `mhit` (*Track Item*): 300+ byte record describing an individual track (ID, bitrate, sample rate, length, play count, rating).
    - `mhod` (*Object Descriptor*): Variable-length strings attached to `mhit` or `mhyp` (title, artist, album, genre, file location).
    - `mhlp` / `mhyp` (*Playlist Headers*): Playlist containers and track references.
- **`iPod_Control/Music/F00` to `Fxx`**:
  - Media files are distributed across numbered directories (`F00` through `F49` on 5.5th Gen).
  - Filenames are randomized 4-letter uppercase stems (e.g., `ABCD.m4a`, `BJFS.mp3`). This prevents FAT32 directory table bottlenecks and ensures compatibility with firmware limits.
- **`iPod_Control/Artwork/ArtworkDB` & `*.ithmb`**:
  - Cover art is not read from embedded ID3/MP4 tags during playback.
  - The iPod converts artwork into uncompressed raw pixel buffers stored in monolithic `.ithmb` files (e.g. RGB565 / 16-bit color format).
  - The `ArtworkDB` indexes which offset in which `.ithmb` file corresponds to each track's `artwork_id`.
- **`iPod_Control/Device/SysInfo`**:
  - Key-value text or binary metadata containing the iPod's board revision, model number, and firmware build.

---

## 2. Hardware Profile: iPod 5.5th Gen (Video) & Wolfson DAC

### The Wolfson WM8758 DAC
The 5.5th Generation iPod (Late 2006, Model MA446 / MA450) is widely regarded by audiophiles as the pinnacle of portable iPod audio quality due to its **Wolfson Microelectronics WM8758** DAC chip. Unlike later Cirrus Logic chips used in the 6th/7th Gen Classic, the Wolfson DAC produces a warmer, highly musical, and detailed analog sound signature.

### Optimal Audio Delivery Constraints
To achieve bit-perfect playback without exhausting the iPod's 32MB/64MB SDRAM buffer or overheating its PortalPlayer processor:

1. **ALAC (Apple Lossless Audio Codec)**:
   - Preserves 100% of the original audio quality from FLAC, WAV, or AIFF masters.
   - Decoded natively in hardware by the iPod with minimal CPU utilization.
2. **44,100 Hz (44.1 kHz) Sample Rate**:
   - The native audio clock of the iPod DAC runs at 44.1 kHz. Playing 48 kHz, 96 kHz, or 192 kHz forces the firmware to perform poor real-time software resampling, draining the battery and causing stutter.
   - We pre-resample using **SoX** (`aresample=resampler=soxr`), the industry standard for clean phase response and zero aliasing.
3. **16-bit PCM (`-sample_fmt s16p`)**:
   - 24-bit audio files cannot be decoded by standard iPod firmware and will fail to play.
4. **Faststart (`-movflags +faststart`)**:
   - Standard MP4 muxers place the `moov` atom (the index header) at the end of the file.
   - On an iPod hard drive, this forces the drive head to seek to the end of the file before playback can start.
   - With `+faststart`, the `moov` atom is positioned at byte 0, enabling instantaneous playback.

---

## 3. Subsystem Breakdown

### `src/iPodDB`
Extracted from `iOpenPod`. Provides:
- `IPodLibrary`: The main entry point. Loads raw `iTunesDB` bytes, provides read-only `LibrarySnapshot`, creates `LibraryDraft`, runs static `analyze()`, and prepares atomic serialization with `prepare()`.
- `_write_preparation.py`: Ensures strict round-trip validation. If any field in the generated binary does not match the desired model, a `WriteIssue` is raised rather than producing a corrupt file.

### `src/device_registry`
- Contains authoritative specifications for all 100+ iPod variants released between 2001 and 2019.
- Maps serial numbers, model numbers, and SCSI inquiry strings to exact generation, color finish, drive capacity, and audio/video playback capabilities.

### `src/storage`
- Interfaces with Win32 `IOCTL_STORAGE_QUERY_PROPERTY` and Linux/macOS block devices.
- Uses SCSI VPD page queries (`0xC0`, `0xC2`) to probe the device controller even when files on disk are missing or corrupted.

### `src/ipod_cli`
- **`allocator.py`**: Manages the `MusicPathAllocator`, ensuring uniform distribution of tracks across `F00-F49` without filename collisions.
- **`metadata.py`**: Uses `mutagen` to extract complete tags (title, artist, album, track number, year, cover art, duration, bitrate, sample rate).
- **`transcode.py`**: Invokes FFmpeg with dynamic SoX/SWR resampler detection.
- **`storage_ops.py`**: Manages transactional disk safety:
  1. Auto-generates pre-write `.bak` files.
  2. Copies media payload.
  3. Writes database to temporary file.
  4. Atomically replaces target database.

---

## 4. Extensibility Guide for Agents

### Adding a New CLI Command
1. Create a new module under `src/ipod_cli/commands/<command_name>.py`.
2. Define the command function using Typer decorators and typed parameters.
3. Use `ui.console`, `print_info`, `print_success`, `print_error` for standard output.
4. Import and register the command in `src/ipod_cli/main.py`:
   ```python
   from ipod_cli.commands import my_command
   app.command(name="my-command")(my_command.run)
   ```
5. Test using `uv run ipod my-command --help`.
