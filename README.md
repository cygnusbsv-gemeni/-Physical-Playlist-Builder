# Physical Playlist Builder

Physical Playlist Builder is an independent Python CLI utility for answering one question:

```text
How do I physically prepare this playlist on disk?
```

Current stage: validation and normalization only. The tool reads a neutral JSON job, validates it, prints a summary, and exits. It does not copy, convert, normalize, tag, create M3U8 files, or create output folders yet.

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

Arguments:

| Argument | Required | Description |
|---|---:|---|
| `--input` | Yes | Path to a JSON job using `physical_playlist_job.v1`. |
| `--out` | Yes | Output folder planned for future exported playlist files. |
| `--dry-run` | No | Validate and summarize without creating or modifying files. |
| `--strict` | No | Fail with exit code 3 when any tracks are blocked. |

## Dry-Run Validation Workflow

Use dry-run first:

```bash
python -m ppb.cli --input DOC/examples/playlist_job.v1.canonical.json --out ./out --dry-run
```

The CLI prints:

```text
Format: physical_playlist_job.v1
Playlist name: ...
Track count: ...
Blocked track count: ...
Warning count: ...
Output folder: ...
Dry-run mode: ...
Strict mode: ...
```

At this stage, dry-run and non-dry-run both perform validation only. No files or folders are created.

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

## Safety Rules

- Source audio files are never modified.
- Tags, if implemented later, are written only to exported copies.
- Loudness processing, if implemented later, applies only to exported copies.
- All outputs must stay inside the selected output folder.
- Existing files must not be silently overwritten.
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

- Source file existence is not checked yet.
- Only JSON input is supported.
- Output folders are not created.
- Files are not copied, converted, normalized, tagged, or overwritten.
- M3U8 generation is not implemented yet.
