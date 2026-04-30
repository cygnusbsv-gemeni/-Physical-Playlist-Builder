# Physical Playlist Builder

A standalone Python CLI / desktop utility that creates a **physical playlist folder** on disk from generic playlist input.

> **Stage U1** — Project skeleton and CLI summary.
> No files are copied, converted, or modified yet.

---

## What it does (Stage U1)

- Accepts a `playlist_job.json` (`physical_playlist_job.v1`) as input
- Parses CLI arguments
- Loads and prints a job summary: schema, playlist name, track count, output folder, dry-run mode
- Exits with a clear error if the input file is missing or malformed

## What it does NOT do yet

- Does **not** copy, convert, normalize, or tag any audio files
- Does **not** create the output folder
- Does **not** generate an M3U8 playlist
- Does **not** write any reports or logs
- Does **not** validate source file paths

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
- No third-party libraries required at Stage U1
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
python -m ppb.cli --input playlist_job.json --out /path/to/output [--dry-run]
```

### Arguments

| Argument    | Required | Description |
|-------------|----------|-------------|
| `--input`   | Yes      | Path to `playlist_job.json` or other supported input file |
| `--out`     | Yes      | Output folder where the physical playlist will be created |
| `--dry-run` | No       | Print what would be done without creating or copying any files |

### Example

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
  Output folder : /home/user/Playlists/RoadTrip
  Dry-run mode  : YES — no files will be created
====================================================
  First track   : {'source_path': '/music/library/...', 'position': 1, ...}

[dry-run] No files were created or modified.
```

---

## Input format — `physical_playlist_job.v1`

The primary input format is a JSON file. All fields:

| Field               | Type            | Required | Description |
|---------------------|-----------------|----------|-------------|
| `schema`            | string          | Yes      | Must be `"physical_playlist_job.v1"` |
| `playlist_name`     | string          | Yes      | Human-readable playlist name |
| `tracks`            | array of objects | Yes     | Ordered list of track entries (may be empty) |
| `output_format`     | string or null  | No       | Target audio format (`"mp3"`, `"flac"`, etc.) or `null` for copy-as-is |
| `normalize_loudness`| boolean         | No       | Apply EBU R128 loudness normalization to exported copies |
| `write_tags`        | boolean         | No       | Write metadata tags to exported copies |

### Track entry fields

| Field           | Type   | Required | Description |
|-----------------|--------|----------|-------------|
| `source_path`   | string | Yes      | Absolute path to the source audio file |
| `position`      | int    | Yes      | 1-based position in the playlist |
| `display_title` | string | No       | Title override for the exported copy |
| `artist`        | string | No       | Artist override for the exported copy |
| `album`         | string | No       | Album override for the exported copy |

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
│   ├── cli.py              ← CLI entry point (Stage U1 — implemented)
│   ├── contract.py         ← Neutral input contract dataclasses (Stage U1 — stub)
│   ├── input_readers.py    ← TXT / CSV / M3U / M3U8 readers (Planned)
│   ├── validator.py        ← Input validation (Planned)
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
    └── test_cli_u1.py      ← Stage U1 smoke tests
```

---

## Current limitations (Stage U1)

- Only JSON input is supported. TXT, CSV, M3U/M3U8, and folder inputs are not yet implemented.
- No source file validation is performed (missing files are not detected).
- No output folder is created.
- No files are copied, converted, or tagged.
- No M3U8 playlist is generated.
- No reports or logs are written.

---

## Next stage (U2)

**Stage U2 — JSON input reader and contract normalization.**

The next stage will:
1. Read `playlist_job.json` and convert it into a `PlaylistJob` dataclass (defined in `contract.py`).
2. Validate that required top-level fields are present (`schema`, `playlist_name`, `tracks`).
3. Validate that each track has `source_path` and `position`.
4. Reject unknown schema versions with a clear error.
5. Pass the `PlaylistJob` object to the rest of the CLI pipeline instead of the raw `dict`.

Fields the next stage must validate in `physical_playlist_job.v1`:
- `schema` — must equal `"physical_playlist_job.v1"` (string, required)
- `playlist_name` — non-empty string (required)
- `tracks` — list (required, may be empty)
- each track: `source_path` — non-empty string (required)
- each track: `position` — positive integer (required)
- each track: `display_title`, `artist`, `album` — optional strings
- `output_format` — string or null (optional)
- `normalize_loudness` — boolean (optional, default false)
- `write_tags` — boolean (optional, default false)
