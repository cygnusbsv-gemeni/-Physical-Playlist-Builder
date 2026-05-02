# Physical Playlist Builder

Physical Playlist Builder is an independent Python CLI utility for answering one question:

```text
How do I physically prepare this playlist on disk?
```

Current stage: input reading, validation, normalization, dry-run operation planning, safe output-folder creation, copying source-compatible tracks into the export folder, generating `playlist.m3u8` from successfully copied files only, writing user-facing reports/logs, and providing an isolated ffmpeg utility layer for a later conversion stage. The tool reads a neutral playlist input, validates it, computes what would be copied or converted, reports path conflicts and missing sources, creates the physical output folder plus `export_session.json`, copies tracks planned as `copy`, generates a UTF-8 `playlist.m3u8`, writes `export_report.json`, writes human-readable `export_report.txt`, writes `export.log`, and prints a final CLI summary. It does not integrate conversion into the main workflow yet, and it does not normalize or write tags.

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
python -m ppb.cli --input playlist_job.json --out D:\PlaylistOut
python -m ppb.cli --input playlist_job.json --out D:\PlaylistOut --m3u-name road-trip.m3u8
python -m ppb.cli --input DOC\examples\playlist_job.v1.canonical.json --out .\out
python -m ppb.cli --input playlist_job.json --out D:\PlaylistOut --no-create-subfolder
```

Arguments:

| Argument | Required | Description |
|---|---:|---|
| `--input` | Yes | Path to JSON, TXT, CSV, M3U, or M3U8 input. |
| `--input-type` | No | `auto`, `json`, `txt`, `csv`, `m3u`, or `m3u8`. Default: `auto`. |
| `--out` | Yes | Base output folder for future exported playlist files. |
| `--overwrite` | No | Allow writing `export_session.json` into an existing non-empty final output folder. Default: false. |
| `--create-subfolder` / `--no-create-subfolder` | No | Create a timestamped playlist subfolder under `--out`. Default: true. |
| `--dry-run` | No | Validate and summarize without creating or modifying files. |
| `--strict` | No | Fail with exit code 3 when any tracks are blocked. |
| `--report` | No | With `--dry-run`, write a JSON operation report. Passing no value writes `dry_run_report.json`. |
| `--m3u-name` | No | Safe leaf filename for the generated M3U8 file. Default: `playlist.m3u8`. Absolute paths, separators, traversal, and empty names are rejected. |
| `--ffmpeg` | No | Path to an ffmpeg executable for the upcoming conversion stage. Parsed by the CLI, but not used by the main export workflow yet. |
| `--mp3-quality` | No | MP3 VBR quality value from `0` to `9` for the upcoming conversion stage. Default: `2`. Parsed only in this stage. |
| `--audio-bitrate` | No | Audio bitrate such as `192k` for the upcoming conversion stage. Parsed only in this stage. |

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

## Output Folder Creation, Copy, M3U8, Reporting, And Logging Stage

Run without `--dry-run` to create the safe physical output folder and execute the first real copy stage:

```bash
python -m ppb.cli --input playlist_job.json --out ./out
```

By default the final output folder is a timestamped subfolder:

```text
<playlist_name>_<YYYYMMDD_HHMMSS>
```

The playlist name is sanitized for Windows filenames before the folder is created. Pass `--no-create-subfolder` to use `--out` as the exact final output folder.

The output stage writes `export_session.json` into the final folder. This file records the final output path, the validated job, the dry-run plan, and a handoff field named `handoff.final_output_dir`.

After the folder is ready, the copy stage copies only tracks whose planned action is `copy`. Blocked tracks are skipped, missing sources are reported as `source_missing`, and planned conversions are reported as `not_implemented` because conversion is not part of this stage. Existing destination files are not overwritten unless `--overwrite` is passed. Each copied file is size-checked after copy.

Then the tool evaluates `settings.generate_m3u8` from the normalized job. If the setting is `true` or missing, the CLI generates a UTF-8 `playlist.m3u8` (or the filename passed via `--m3u-name`) inside the same final output folder. If the setting is `false`, M3U8 generation is skipped and the skip is recorded in `export_report.json`.

The generated playlist always starts with `#EXTM3U`, uses relative paths from the M3U8 file location to exported files, preserves playlist order, and includes only tracks whose output file was actually created successfully in this run. When metadata is available, the tool writes `#EXTINF:<duration>,<artist> - <title>` lines. If no tracks were copied successfully, the tool still writes a valid empty playlist containing only `#EXTM3U`.

The stage writes `export_report.json` into the final folder with one result per track. Result statuses include:

- `copied`
- `skipped`
- `failed`
- `source_missing`
- `destination_exists`
- `not_implemented`

The same `export_report.json` also records:

- `started_at`
- `finished_at`
- `input_path`
- `final_output_dir`
- `playlist_name`
- `totals`
- `warnings`
- `errors`
- `m3u_path`
- `m3u_track_count`
- `m3u_status`
- `m3u_warnings` or `m3u_errors` when applicable
- `report_txt_path`
- `log_path`

The stage also writes `export_report.txt`, a human-readable report for the completed run. It summarizes the playlist name, input path, final output folder, copied/skipped/failed/source-missing/destination-conflict/not-implemented totals, M3U8 status and path, failed or missing tracks, destination conflicts, not-yet-implemented convert tracks, and generated files.

The stage writes `export.log` using only Python standard library logging. The log records the main real-run milestones: validation completed, output folder created, copy stage completed, M3U8 generated or skipped, reports written, plus warnings and errors when available.

At the end of a real run, the CLI prints a final summary containing the final output folder, copied/skipped/failed/missing/conflict/not-implemented counts, M3U8 status/path, `export_report.json`, `export_report.txt`, and `export.log`.

Dry-run mode does not create `export_report.txt` or `export.log`. `--report` remains a dry-run JSON report feature.

## FFmpeg Utility Layer

B9.1 adds `ppb/ffmpeg_tools.py`, a reusable low-level helper module for a later conversion stage. It can:

- resolve ffmpeg from `PATH` or from an explicit executable path;
- validate the executable by running `ffmpeg -version`;
- convert one source file into one destination file inside an explicitly provided output folder;
- avoid overwriting destinations unless `overwrite=True`;
- create destination parent folders only inside the output folder boundary;
- capture ffmpeg return code, stdout, stderr, and a stderr summary.

Supported helper-level target formats are `mp3`, `flac`, `wav`, `m4a`, and `aac`. MP3 uses `libmp3lame` with default VBR quality `2`; `m4a` and `aac` use ffmpeg's native `aac` encoder when available in the local ffmpeg build. Sample rate is preserved by default because the helper does not pass `-ar`.

This layer is not integrated into the main CLI processing flow yet. Planned `convert` tracks are still reported as `not_implemented`, and generated `playlist.m3u8` files still include only files that were actually created successfully by the current copy stage.

Example generated files after a real run:

```text
<final_output_dir>/export_session.json
<final_output_dir>/export_report.json
<final_output_dir>/export_report.txt
<final_output_dir>/export.log
<final_output_dir>/playlist.m3u8
```

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
- The copy/M3U8/report/log stage reads source files and writes only destination copies plus generated report/log/playlist files in the selected output folder.
- Tags, if implemented later, are written only to exported copies.
- Loudness processing, if implemented later, applies only to exported copies.
- All outputs must stay inside the selected output folder.
- Existing files must not be silently overwritten.
- Existing non-empty output folders are rejected unless `--overwrite` is passed.
- Duplicate planned output filenames are reported as conflicts.
- Output filenames must be safe leaf filenames, not absolute paths or paths with `..`.
- `--m3u-name` must be a safe leaf filename, not an absolute path, nested path, or traversal path.
- The output folder must not be the same as a source track directory.
- The output folder must not be inside a source track directory.
- The output path must not be empty or a filesystem root.
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

Focused B9.1 checks:

```bash
python -m ppb.cli --help
python -m py_compile ppb\cli.py ppb\ffmpeg_tools.py
```

## Current Limitations

- TXT, CSV, M3U, and M3U8 inputs carry less metadata than canonical JSON.
- Only planned `copy` operations are executed.
- Conversion is available only as an isolated helper in `ppb/ffmpeg_tools.py`; it is not integrated into the main CLI workflow yet.
- Loudness normalization and tag writing are not implemented yet.
- `playlist.m3u8` includes only successfully copied files from the current run; planned conversions remain excluded as `not_implemented`.
- `export.log` is created only for real runs after the final output folder is ready.

## Next Stage

Next stage is not implemented yet. A logical next step after B9.1 is B9.2 conversion/export handling for planned `convert` operations while keeping M3U8 generation limited to actually created output files.
