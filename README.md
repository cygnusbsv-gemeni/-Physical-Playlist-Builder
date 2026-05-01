# Physical Playlist Builder

Physical Playlist Builder is an independent Python CLI utility for answering one question:

```text
How do I physically prepare this playlist on disk?
```

Current stage: input reading, validation, normalization, and dry-run operation planning. The tool reads a neutral playlist input, validates it, computes what would be copied or converted, reports path conflicts and missing sources, and exits. It does not copy, convert, normalize, tag, create M3U8 files, or create output folders yet.

## Supported Input Types

Supported input files:

- JSON `physical_playlist_job.v1`
- TXT path list
- CSV table
- M3U
- M3U8

JSON `physical_playlist_job.v1` is the canonical rich input format. TXT, CSV, M3U, and M3U8 are generic convenience inputs; they are converted internally into the same normalized `PlaylistJob` structure and then validated through the same validation path.

## Canonical Input Contract

The authoritative external input contract is:

```text
DOC/physical_playlist_job_v1_contract.md
```

The canonical JSON format is `physical_playlist_job.v1`. A valid job uses top-level `format`, `playlist`, `settings`, and `tracks`:

```json
{
  "format": "physical_playlist_job.v1",
  "generated_at": "2026-05-01T00:00:00+00:00",
  "playlist": {
    "name": "Road Mix",
    "description": "Optional notes",
    "track_count": 1
  },
  "settings": {
    "output_format": "source",
    "copy_mode": "copy_if_compatible",
    "normalize_loudness": false,
    "target_lufs": -14.0,
    "true_peak_db": -1.0,
    "write_tags": false,
    "generate_m3u8": true,
    "filename_template": "{position:02d} - {artist} - {title}"
  },
  "tracks": [
    {
      "position": 1,
      "source_path": "/music/Artist/Track.flac",
      "title": "Track",
      "artist": "Artist"
    }
  ],
  "summary": {},
  "producer_meta": {}
}
```

Canonical examples are stored at:

```text
DOC/examples/playlist_job.v1.canonical.json
example_playlist_job.json
```

The legacy top-level `schema`, `playlist_name`, `output_format`, `normalize_loudness`, and `write_tags` fields are supported only through a temporary migration normalizer. New jobs should use the canonical shape above.

## Independence

This project consumes only the neutral playlist job contract. It does not import or require any producer application, web framework, database, route, backend module, internal ID, preview model, or running external service.

Any producer may generate the same JSON contract: a catalog, a TXT/CSV/M3U converter, a folder scanner, a desktop app, a manual script, or another cataloging system.

## Usage

```bash
python -m ppb.cli --input playlist_job.json --out /path/to/output --dry-run
```

Windows examples:

```bash
python -m ppb.cli --input playlist_job.json --out D:\PlaylistOut --dry-run
python -m ppb.cli --input tracks.txt --out D:\PlaylistOut --dry-run
python -m ppb.cli --input tracks.csv --out D:\PlaylistOut --dry-run
python -m ppb.cli --input playlist.m3u8 --out D:\PlaylistOut --dry-run
python -m ppb.cli --input playlist_job.json --out D:\PlaylistOut --dry-run --report
```

Arguments:

| Argument | Required | Description |
|---|---:|---|
| `--input` | Yes | Path to JSON, TXT, CSV, M3U, or M3U8 input. |
| `--input-type` | No | `auto`, `json`, `txt`, `csv`, `m3u`, or `m3u8`. Default: `auto`. |
| `--out` | Yes | Output folder planned for future exported playlist files. |
| `--dry-run` | No | Validate and summarize without creating or modifying files. |
| `--strict` | No | Fail with exit code 3 when any tracks are blocked. |
| `--report` | No | With `--dry-run`, write a JSON operation report. Passing no value writes `dry_run_report.json`. |

Input type detection defaults to file extension. `.json` is treated as canonical `physical_playlist_job.v1` JSON, `.txt` as a plain path list, `.csv` as tabular input, and `.m3u` / `.m3u8` as playlist files.

TXT input uses one source path per line. Empty lines and lines starting with `#` are ignored. Relative paths are resolved against the TXT file folder.

CSV input requires a `source_path` column and supports comma or semicolon delimiters. Optional metadata columns include `position`, `output_filename`, `filename_hint`, `title`, `artist`, `album`, `albumartist`, `tracknumber`, `date`, `year`, `genre`, `duration_sec`, `codec`, `bitrate_kbps`, `sample_rate_hz`, `channels`, `bit_depth`, `tag_format`, `warnings`, and `blockers`. Relative `source_path` values are resolved against the CSV file folder.

M3U and M3U8 input support `#EXTM3U` and `#EXTINF:<duration>,<artist> - <title>` metadata. Empty lines and unsupported comments are ignored. Relative paths are resolved against the playlist file folder.

## Dry-Run Planning Workflow

Use dry-run first:

```bash
python -m ppb.cli --input DOC/examples/playlist_job.v1.canonical.json --out ./out --dry-run
```

The CLI prints the validation summary first:

```text
Input path: ...
Detected input type: ...
Format: physical_playlist_job.v1
Playlist name: ...
Track count: ...
Blocked track count: ...
Warning count: ...
Output folder: ...
Dry-run mode: ...
Strict mode: ...
```

Then dry-run prints an operation plan with:

- output directory validity;
- whether the output directory already exists;
- whether the output directory matches a source track directory;
- planned copy and conversion counts;
- blocked tracks;
- missing source files;
- duplicate output filenames;
- operations that are safe for the next output-folder stage.

When `--report` is passed, the same plan is written as JSON. No music files or output folders are created.

## Strict vs Non-Strict

Blocked tracks are allowed in input and are reported.

In default non-strict mode, validation succeeds even when blocked tracks exist. The summary explains that blocked tracks will be skipped later.

In `--strict` mode, validation fails with exit code 3 if any blocked tracks exist.

Fatal job-level errors always fail with exit code 2. Examples include malformed JSON, missing `format`, unsupported `format`, missing `playlist.name`, and missing or non-list `tracks`.

## Validation Rules

- `format` must be exactly `physical_playlist_job.v1`.
- Top-level `schema` is not required for canonical input.
- Playlist metadata is read from `playlist.name`, `playlist.description`, and `playlist.track_count`.
- Processing settings are read from `settings`.
- Tracks are read from `tracks[]` in array order.
- `source_path` is required for every runnable track.
- Missing `position` uses implicit array order and adds a warning.
- Input `warnings` are preserved and reported.
- Input `blockers` make the track blocked and are reported.
- `summary` is optional.
- `producer_meta` is optional and ignored safely.
- Unknown optional fields do not fail validation.
- TXT, CSV, M3U, and M3U8 inputs are normalized into `PlaylistJob` before validation.

## Safety Rules

- Source audio files are never modified.
- Dry-run checks whether source files exist before any real output stage.
- Tags, if implemented later, are written only to exported copies.
- Loudness processing, if implemented later, applies only to exported copies.
- All outputs must stay inside the selected output folder.
- Existing files must not be silently overwritten.
- Duplicate planned output filenames are reported as conflicts.
- Output filenames must be safe leaf filenames, not absolute paths or paths with `..`.
- The output folder must not be the same as a source track directory.
- Dry-run must be available before real execution.

## Requirements

- Python 3.10+
- No third-party runtime dependencies for the core validator
- `pytest` for tests

Install test dependencies:

```bash
pip install -r requirements.txt
```

## Running Tests

```bash
pytest tests/
```

## Current Limitations

- TXT, CSV, M3U, and M3U8 inputs carry less metadata than canonical JSON.
- Output folders are not created.
- Files are not copied, converted, normalized, tagged, or overwritten.
- M3U8 generation is not implemented yet.
