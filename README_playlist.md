# Physical Playlist Builder

A standalone Python CLI / desktop utility that creates a **physical playlist folder** on disk from generic playlist input.

> **Stage U2** — JSON input validation and contract normalization.
> No files are copied, converted, or modified yet.

---

## What it does (Stage U2)

- Accepts a `playlist_job.json` (`physical_playlist_job.v1`) as input
- Parses CLI arguments including `--strict`
- Loads JSON and **validates** the job against the schema
- Produces a normalized `PlaylistJob` dataclass (neutral internal representation)
- Reports validation issues: **blockers** (fatal per-track errors) and **warnings** (non-fatal)
- Prints a human-readable job summary with validation results
- In `--strict` mode: exits with code 3 if any tracks are blocked
- In normal mode: exits successfully even if some tracks are blocked (they will be skipped later)
- Exits with code 2 on fatal errors (wrong schema, missing required fields, malformed JSON)

## What it does NOT do yet

- Does **not** check whether source files actually exist on disk (file existence validation is Stage U3)
- Does **not** copy, convert, normalize, or tag any audio files
- Does **not** create the output folder
- Does **not** generate an M3U8 playlist
- Does **not** write any reports or logs
- Does **not** support TXT, CSV, M3U/M3U8, or folder inputs

---

## Safety rules

These rules are enforced throughout all stages:

1. **Source audio files are never modified** — ever.
2. All output (copies, conversions, M3U8, reports, logs) goes **only** inside the selected output folder.
3. Tags are written **only to exported copies**.
4. Loudness processing is applied **only to exported copies**.
5. `ffmpeg` must never overwrite or alter source files.
6. Existing output files are **never silently overwritten** — an explicit `--overwrite` flag is required (planned).
7. `--dry-run` is always available before real execution.

---

## Requirements

- Python 3.10+
- No third-party libraries required for the core tool
- `pytest` for running tests (`pip install pytest`)

---

## Installation

```bash
git clone <repo>
cd physical_playlist_builder
pip install -r requirements.txt
```

---

## Usage

```bash
python -m ppb.cli --input playlist_job.json --out /path/to/output [--dry-run] [--strict]
```

### Arguments

| Argument    | Required | Description |
|-------------|----------|-------------|
| `--input`   | Yes      | Path to `playlist_job.json` or other supported input file |
| `--out`     | Yes      | Output folder where the physical playlist will be created |
| `--dry-run` | No       | Print what would be done without creating or copying any files |
| `--strict`  | No       | Fail (exit 3) if any tracks are blocked; without this flag blocked tracks are reported but skipped |

### Example — valid job

```bash
python -m ppb.cli --input example_playlist_job.json --out ~/Playlists/RoadTrip --dry-run
```

**Expected output:**

```
====================================================
  Physical Playlist Builder — Job Summary
====================================================
  Schema        : physical_playlist_job.v1
  Playlist name : Road Trip Summer 2024
  Track count   : 3
  Blocked       : 0
  Warnings      : 0
  Output folder : /home/user/Playlists/RoadTrip
  Dry-run mode  : YES — no files will be created
  Strict mode   : NO — blocked tracks are skipped
====================================================
  All tracks passed validation.

[dry-run] No files were created or modified.
```

### Example — job with a blocked track + strict mode

```bash
python -m ppb.cli --input broken_job.json --out ~/out --strict
```

```
====================================================
  ...
  Blocked       : 1
  Warnings      : 0
  ...
====================================================

  Validation issues:
  [BLOCKER] track 2 [/music/missing.flac]: "source_path" is required but missing.

  Tracks total   : 2
  Blocked tracks : 1
  Warnings       : 0
  Strict mode    : FAIL — blocked tracks found

[strict] Validation failed: 1 blocked track(s). Use --no-strict to skip blocked tracks.
```

Exit codes:
| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Argument error (argparse) |
| 2 | Fatal validation error (wrong schema, missing required fields, bad JSON) |
| 3 | Strict mode: blocked tracks found |

---

## Input format — `physical_playlist_job.v1`

### Top-level fields

| Field               | Type             | Required | Description |
|---------------------|------------------|----------|-------------|
| `schema`            | string           | Yes      | Must be `"physical_playlist_job.v1"` |
| `playlist_name`     | string           | Yes      | Human-readable playlist name (non-empty) |
| `tracks`            | array of objects | Yes      | Ordered list of track entries (may be empty) |
| `output_format`     | string or null   | No       | Target audio format (`"mp3"`, `"flac"`, etc.) or `null` for copy-as-is |
| `normalize_loudness`| boolean          | No       | Apply EBU R128 loudness normalization to exported copies (default: false) |
| `write_tags`        | boolean          | No       | Write metadata tags to exported copies (default: false) |

### Track entry fields

| Field           | Type    | Required | Description |
|-----------------|---------|----------|-------------|
| `source_path`   | string  | Yes      | Absolute path to the source audio file |
| `position`      | int     | Recommended | 1-based position; if omitted, implicit order is used (warning issued) |
| `output_filename` | string | No      | Explicit output filename with extension |
| `filename_hint` | string  | No       | Hint for constructing the output filename |
| `display_title` | string  | No       | Title tag override |
| `artist`        | string  | No       | Artist tag override |
| `album`         | string  | No       | Album tag override |
| `albumartist`   | string  | No       | Album artist tag override |
| `tracknumber`   | string  | No       | Track number tag override |
| `date`          | string  | No       | Date/year tag override |
| `genre`         | string  | No       | Genre tag override |
| `tag_format`    | string  | No       | Tag format hint, e.g. `"ID3v2.4"` |
| `duration_sec`  | float   | No       | Duration in seconds (informational) |
| `codec`         | string  | No       | Codec name, e.g. `"flac"`, `"mp3"` |
| `bitrate_kbps`  | int     | No       | Bitrate in kbps (informational) |
| `sample_rate_hz`| int     | No       | Sample rate in Hz (informational) |
| `channels`      | int     | No       | Number of channels (informational) |
| `bit_depth`     | int     | No       | Bit depth (informational) |
| `warnings`      | array   | No       | Pre-existing warnings from input source (surfaced in report) |
| `blockers`      | array   | No       | Pre-existing blockers from input source (track is treated as blocked) |

See `example_playlist_job.json` for a complete example.

---

## Running tests

```bash
pytest tests/
```

---

## Project structure

```
physical_playlist_builder/
├── README.md
├── requirements.txt
├── example_playlist_job.json
├── ppb/
│   ├── __init__.py
│   ├── cli.py              ← CLI entry point (Stage U2 — updated)
│   ├── contract.py         ← Neutral input contract dataclasses (Stage U2 — extended)
│   ├── validator.py        ← Input validation (Stage U2 — implemented)
│   ├── input_readers.py    ← TXT / CSV / M3U / M3U8 readers (Planned)
│   ├── planner.py          ← Dry-run / operation planning (Planned)
│   ├── filesystem.py       ← Safe output folder handling (Planned)
│   ├── copier.py           ← File copy logic (Planned)
│   ├── m3u.py              ← M3U8 generation (Planned)
│   ├── report.py           ← JSON/TXT reports (Planned)
│   ├── logging_setup.py    ← Logging configuration (Planned)
│   ├── ffmpeg_tools.py     ← ffmpeg conversion/loudness (Planned)
│   └── tags.py             ← Tag writing to exported copies (Planned)
└── tests/
    ├── __init__.py
    ├── test_cli_u1.py      ← Stage U1 smoke tests
    └── test_validator_u2.py ← Stage U2 validator tests
```

---

## Current limitations (Stage U2)

- Source file existence is **not validated** — a `source_path` that doesn't exist on disk will not be caught until Stage U3.
- Only JSON input is supported. TXT, CSV, M3U/M3U8, and folder inputs are not yet implemented.
- No output folder is created.
- No files are copied, converted, or tagged.
- No M3U8 playlist is generated.
- No reports or logs are written.

---

## Next stage (U3)

**Stage U3 — Source file existence validation.**

The next stage will:
1. Accept the normalized `PlaylistJob` from Stage U2.
2. Check that each `source_path` actually exists and is a readable file.
3. Record tracks where the source file is missing as blocked (with a clear message).
4. Add `--skip-missing` flag (or rely on the existing `--strict` distinction).
5. Update the validation summary with file existence results.
6. Still not copy or modify any files.
