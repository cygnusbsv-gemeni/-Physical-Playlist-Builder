# Physical Playlist Builder

Physical Playlist Builder is an independent Python CLI utility for answering one question:

```text
How do I physically prepare this playlist on disk?
```

Current stage: B10.5 focused loudness reporting/logging hardening. The tool reads a neutral playlist input, validates it, computes what would be copied or converted, reports path conflicts and missing sources, creates the physical output folder plus `export_session.json`, copies tracks planned as `copy`, converts tracks planned as `convert`, measures loudness for successfully exported output files when `settings.normalize_loudness=true`, applies ffmpeg `loudnorm` second-pass normalization to those exported copies, verifies loudness again after successful normalization, generates a UTF-8 `playlist.m3u8`, writes `export_report.json`, writes human-readable `export_report.txt`, writes `export.log`, and prints a final CLI summary. B10.5 keeps pre-export warnings traceable without presenting resolved loudness warnings as unresolved final warnings, records explicit `tags_status=not_implemented` when `settings.write_tags=true`, clarifies final file sizes, and avoids logging successful ffmpeg stderr/progress as `ERROR`. Loudness processing never touches source audio files and does not write tags; tag writing, resume, and bundled ffmpeg are not implemented.

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
python -m ppb.cli --input playlist_job_mp3.json --out D:\PlaylistOut --ffmpeg C:\Tools\ffmpeg\bin\ffmpeg.exe
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
| `--ffmpeg` | No | Path to an ffmpeg executable for planned `convert` operations. If omitted, `ffmpeg` is resolved from `PATH`. |
| `--mp3-quality` | No | MP3 VBR quality value from `0` to `9` for planned MP3 conversion. Default: `2`. Ignored for non-MP3 formats. |
| `--audio-bitrate` | No | Audio bitrate such as `192k` for planned conversion. Used for MP3/AAC-style encoders when provided. |
| `--skip-loudness` | No | Skip loudness measurement and normalization during real export. |

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

## Output Folder Creation, Copy, Conversion, Loudness Processing, M3U8, Reporting, And Logging Stage

Run without `--dry-run` to create the safe physical output folder and execute the real export stage:

```bash
python -m ppb.cli --input playlist_job.json --out ./out
```

By default the final output folder is a timestamped subfolder:

```text
<playlist_name>_<YYYYMMDD_HHMMSS>
```

The playlist name is sanitized for Windows filenames before the folder is created. Pass `--no-create-subfolder` to use `--out` as the exact final output folder.

The output stage writes `export_session.json` into the final folder. This file records the final output path, the validated job, the dry-run plan, and a handoff field named `handoff.final_output_dir`.

After the folder is ready, the export stage copies tracks whose planned action is `copy` and converts tracks whose planned action is `convert`. Conversion uses `ppb/ffmpeg_tools.py` and writes only destination files inside the final output folder. Blocked tracks are skipped, missing sources are reported as `source_missing`, and existing destination files are reported as `destination_exists` unless `--overwrite` is passed. Each copied file is size-checked after copy. Converted files are verified by the ffmpeg helper by checking that the destination file was created.

When planned convert tracks exist, ffmpeg is resolved from `--ffmpeg` or from `PATH`. If ffmpeg is missing or not runnable, copy-only tracks still proceed, convert tracks are reported as `ffmpeg_missing`, and the error is written to `export_report.json`, `export_report.txt`, and `export.log`.

After copying and conversion, the tool evaluates loudness processing for the final exported audio files only. Loudness runs only in real non-dry-run exports, only for tracks whose export status is `copied` or `converted`, only for destination files inside the final output folder, and only when `settings.normalize_loudness=true` and `--skip-loudness` is not passed. Failed, skipped, missing, conflicted, blocked, and `ffmpeg_missing` tracks are not measured or normalized.

When loudness processing runs, the CLI first calls ffmpeg `loudnorm` in first-pass measurement mode with `target_lufs`, `true_peak_db`, and the default loudness range target. The first pass writes to ffmpeg's `null` muxer. If measurement succeeds, the CLI then runs `loudnorm` second pass on the already-exported copy. The second pass writes a unique temporary audio file inside the same final output folder, then replaces the exported copy only after ffmpeg succeeds. The user-facing filename stays stable. If normalization fails, the existing unnormalized exported file remains intact and only the temporary output from that attempt is removed.

After a successful second-pass normalization, B10.5 runs one more loudness measurement on the normalized exported file. Verification results are reported as `post_loudness_status` with post-normalization loudness fields. Verification failure does not delete, replace, or downgrade the already exported file status; it is reported as a loudness verification problem.

If `settings.normalize_loudness=false` or `--skip-loudness` is passed, loudness measurement, normalization, and post-normalization verification are skipped and the skip is recorded in `export_report.json`, `export_report.txt`, and `export.log`. If ffmpeg is missing during loudness processing, the export does not crash; already copied or converted files remain in place, loudness status is recorded as `ffmpeg_missing` or `skipped`, and `playlist.m3u8` generation still uses the successfully exported files.

Input warnings are still preserved in `input_warnings` / `pre_export_warnings`, but the resolved pre-export warning `No integrated loudness value is available yet.` is not repeated as an unresolved final warning after loudness has been measured successfully.

If `settings.write_tags=true`, B10.5 records `tags_status=not_implemented` and `tags_reason="Tag writing is not implemented yet."` in the report. It still does not write tags.

Then the tool evaluates `settings.generate_m3u8` from the normalized job. If the setting is `true` or missing, the CLI generates a UTF-8 `playlist.m3u8` (or the filename passed via `--m3u-name`) inside the same final output folder. If the setting is `false`, M3U8 generation is skipped and the skip is recorded in `export_report.json`.

The generated playlist always starts with `#EXTM3U`, uses relative paths from the M3U8 file location to exported files, preserves playlist order, and includes only tracks whose output file was actually created successfully in this run. Successfully copied and successfully converted tracks are included. Failed, skipped, missing, conflicted, and `ffmpeg_missing` tracks are excluded. When metadata is available, the tool writes `#EXTINF:<duration>,<artist> - <title>` lines. If no tracks were exported successfully, the tool still writes a valid empty playlist containing only `#EXTM3U`.

The stage writes `export_report.json` into the final folder with one result per track. Result statuses include:

- `copied`
- `converted`
- `skipped`
- `failed`
- `source_missing`
- `destination_exists`
- `ffmpeg_missing`

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
- `loudness`
- `loudness_totals`
- `loudness_verification_totals`
- `tags_status`
- `tags_reason`
- `input_warnings`
- `pre_export_warnings`
- `report_txt_path`
- `log_path`

Each track record in `export_report.json` includes loudness measurement fields:

- `loudness_status`: `measured`, `skipped`, `failed`, or `ffmpeg_missing`
- `input_i`
- `input_tp`
- `input_lra`
- `input_thresh`
- `target_offset`
- `loudness_error`
- `loudness_stderr_summary`

Each track record also includes loudness normalization fields:

- `loudness_normalization_status`: `normalized`, `skipped`, `failed`, or `ffmpeg_missing`
- `normalized_output_path`
- `loudness_normalization_error`
- `loudness_normalization_stderr_summary`

Each track record also includes post-normalization loudness verification fields:

- `post_loudness_status`: `measured`, `skipped`, `failed`, or `ffmpeg_missing`
- `post_input_i`
- `post_input_tp`
- `post_input_lra`
- `post_input_thresh`
- `post_target_offset`
- `post_loudness_error`
- `post_loudness_stderr_summary`

B10.5 also adds clearer size fields while keeping existing `source_size`, `destination_size`, and `bytes_copied` fields:

- `size_after_export_before_loudness`
- `size_after_loudness`
- `final_size`

The stage also writes `export_report.txt`, a human-readable report for the completed run. It summarizes the playlist name, input path, final output folder, copied/converted/skipped/failed/source-missing/destination-conflict/ffmpeg-missing totals, loudness measured/normalized/skipped/failed/ffmpeg-missing totals, post-normalization verification totals, tag-writing status, per-track loudness and verification failures, M3U8 status and path, failed or missing tracks, destination conflicts, and generated files. Resolved integrated-loudness pre-export warnings are kept in the input warning trace but are not shown under final unresolved `Warnings`.

The stage writes `export.log` using only Python standard library logging. The log records the main real-run milestones: validation completed, output folder created, export stage completed, conversions or copy operations, loudness measurement started, loudness normalization started, per-track loudness measured/normalized/verified/skipped/failed entries, loudness measurement completed, loudness normalization completed, loudness verification completed, M3U8 generated or skipped, reports written, plus warnings and errors when available. Successful ffmpeg stderr/progress summaries are logged as non-error information; `ERROR` is reserved for failed ffmpeg calls and failed export/loudness operations.

At the end of a real run, the CLI prints a final summary containing the final output folder, copied/converted/skipped/failed/missing/conflict/ffmpeg-missing counts, M3U8 status/path, `export_report.json`, `export_report.txt`, and `export.log`.

Dry-run mode does not create `export_report.txt` or `export.log`. `--report` remains a dry-run JSON report feature.

## FFmpeg Conversion And Loudness Processing

`ppb/ffmpeg_tools.py` is the reusable low-level helper used by the main export workflow for planned `convert` tracks, loudness measurement, and loudness normalization. It can:

- resolve ffmpeg from `PATH` or from an explicit executable path;
- validate the executable by running `ffmpeg -version`;
- convert one source file into one destination file inside an explicitly provided output folder;
- avoid overwriting destinations unless `overwrite=True`;
- create destination parent folders only inside the output folder boundary;
- capture ffmpeg return code, stdout, stderr, and a stderr summary;
- measure loudness through a read-only ffmpeg loudnorm first pass for exported output files;
- normalize loudness through ffmpeg loudnorm second pass for exported output files only, using a temporary output inside the final output folder;
- verify loudness after successful normalization by measuring the normalized exported output file.

Supported helper-level target formats are `mp3`, `flac`, `wav`, `m4a`, and `aac`. MP3 uses `libmp3lame` with default VBR quality `2`; `m4a` and `aac` use ffmpeg's native `aac` encoder when available in the local ffmpeg build. Sample rate is preserved by default because the helper does not pass `-ar`.

In the main CLI workflow, planned `convert` tracks use the normalized job target format, normally `settings.output_format`, and the planned output filename from the dry-run plan. Successful conversions are reported as `converted` and are included in `playlist.m3u8`. Failed conversions are reported as `failed`, `source_missing`, `destination_exists`, or `ffmpeg_missing` and are excluded from `playlist.m3u8`. If ffmpeg creates a partial destination file for a failed conversion and that destination did not exist before the run, the helper removes that partial file inside the output folder. The per-track JSON result records the target format, ffmpeg return code when available, and an ffmpeg stderr summary when conversion fails.

The CLI calls `measure_loudness_first_pass()` after successful copy/conversion. It runs ffmpeg with the `loudnorm` filter in first-pass mode and writes output to ffmpeg's `null` muxer, so it does not create, modify, replace, delete, rename, normalize, or tag audio files. It returns structured fields including `success`, `return_code`, `input_i`, `input_tp`, `input_lra`, `input_thresh`, `target_offset`, raw loudnorm JSON payload, and stderr diagnostics. The CLI stores these values in reports and logs.

When first-pass measurement succeeds, the CLI calls `normalize_loudness_second_pass()` for the same exported file. The helper refuses files outside the final output folder, creates a unique temporary destination inside that folder, uses the measured `loudnorm` values for the second pass, writes no metadata tags (`-map_metadata -1`), and replaces the exported copy only after ffmpeg succeeds. If ffmpeg fails or the temporary output is incomplete, the original exported copy remains in place and the attempt is reported as `failed` or `ffmpeg_missing`. B10.5 then verifies successful normalization by measuring the final exported file again.

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
- The copy/conversion/M3U8/report/log stage reads source files and writes only exported audio files plus generated report/log/playlist files in the selected output folder.
- ffmpeg conversion receives source files only as inputs and writes destination files only inside the selected final output folder.
- Loudness measurement receives only successfully exported destination files inside the final output folder as ffmpeg inputs and writes output to the `null` muxer.
- Loudness normalization receives only successfully measured exported destination files inside the final output folder as ffmpeg inputs.
- Loudness normalization writes a temporary output only inside the final output folder, then replaces the exported copy only after ffmpeg succeeds.
- Loudness measurement and normalization never read source audio files directly in the CLI workflow.
- Loudness normalization failures keep the existing unnormalized exported copy intact and remove only the temporary output created by that attempt.
- Tags, if implemented later, are written only to exported copies.
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

Focused B10.5 checks:

```bash
python -m ppb.cli --help
python -m py_compile ppb\cli.py ppb\report.py ppb\ffmpeg_tools.py ppb\logging_setup.py
python -m pytest tests\test_loudness_processing.py -vv --tb=short --basetemp C:\Temp\project_pytest\b10_5 -p no:cacheprovider
python -m pytest tests\test_ffmpeg_conversion.py tests\test_copier.py tests\test_cli_u1.py -q --basetemp C:\Temp\project_pytest\b10_5_regression -p no:cacheprovider
```

If `C:\Temp` is not writable in the local environment, use another explicit pytest temp directory outside the repository when possible. The focused conversion and loudness tests generate synthetic WAV fixtures with Python standard library `wave`; they do not use real user music files. Tests that require real ffmpeg conversion or loudness normalization skip cleanly when ffmpeg is not available, while ffmpeg-missing coverage still runs with an invalid explicit `--ffmpeg` path.

## Current Limitations

- TXT, CSV, M3U, and M3U8 inputs carry less metadata than canonical JSON.
- ffmpeg must be installed on `PATH` or passed with `--ffmpeg` for planned `convert` tracks to succeed.
- If ffmpeg is missing for conversion, planned `convert` tracks are marked `ffmpeg_missing`; copy-only tracks can still complete.
- If ffmpeg is missing for loudness processing, successfully copied or converted files remain intact and per-track loudness measurement/normalization/verification status is recorded as `ffmpeg_missing` or `skipped`.
- Loudness normalization currently supports exported files whose final extension maps to the helper-level formats `mp3`, `flac`, `wav`, `m4a`, or `aac`. Other exported formats remain unnormalized and are reported as failed normalization attempts.
- Tag writing is not implemented yet.
- Resume of interrupted exports is not implemented yet.
- `playlist.m3u8` includes only successfully copied or converted files from the current run.
- Failed conversion partial files are removed only when the destination did not exist before the failed ffmpeg run.
- `export.log` is created only for real runs after the final output folder is ready.

## Next Stage

Next stage is not implemented yet. A logical next step after B10.5 is a focused tag-writing stage that writes tags only to exported copies after copy/conversion/loudness processing is complete. Tag writing and resume remain not implemented.
