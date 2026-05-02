# Project Files - Physical Playlist Builder

## Last Updated

2026-05-02

## Current Stage

B10.1 - Loudness measurement helper groundwork.

The project validates neutral playlist input, builds a dry-run plan, creates a safe output folder, writes `export_session.json`, copies tracks planned as `copy`, converts tracks planned as `convert` through `ppb/ffmpeg_tools.py`, generates a UTF-8 `playlist.m3u8` from successfully copied and converted files, writes `export_report.json`, writes human-readable `export_report.txt`, writes `export.log`, and prints a final CLI summary. B10.1 adds a reusable low-level ffmpeg loudnorm first-pass helper for measuring loudness when called directly from code. The main export workflow does not call loudness measurement or normalization yet. If ffmpeg is missing, copy-only tracks can still complete and convert tracks are reported as `ffmpeg_missing`. Loudness normalization, tag writing, and resume are still not implemented.

## Project File Map

| File path | Responsibility | Important notes |
|---|---|---|
| `README.md` | Project documentation and usage instructions | Must describe only implemented behavior. Updated for B10.1 loudness measurement helper groundwork and the no-op `--skip-loudness` CLI option. |
| `PROJECT_FILES.md` | File map, current stage changes, and generated/runtime file notes | Updated after each completed stage. |
| `requirements.txt` | Python/test dependencies | Core runtime has no third-party dependencies. |
| `ppb/__init__.py` | Package marker | No runtime logic. |
| `ppb/cli.py` | CLI entry point: args, input read, validation, planning, output-folder creation, export/M3U8/report/log execution, progress output, final summary | B10.1 exposes `--skip-loudness` as a reserved no-op option. It does not run loudness measurement or normalization in the export workflow. |
| `ppb/contract.py` | Neutral input contract dataclasses (`TrackEntry`, `PlaylistJob`, settings) | Independent of MusicLib Web or any database. |
| `ppb/validator.py` | Validation and normalization for `physical_playlist_job.v1` | Blocked tracks remain allowed in non-strict mode and are skipped later. |
| `ppb/input_readers.py` | TXT / CSV / M3U / M3U8 input readers | Converts convenience inputs into the neutral `PlaylistJob` path. |
| `ppb/planner.py` | Dry-run and operation planning | Produces planned `copy`, `convert`, blocked, and error operations. |
| `ppb/filesystem.py` | Safe output folder creation and `export_session.json` writing | Does not copy audio files directly. |
| `ppb/copier.py` | Real export-stage logic | Copies planned `copy` operations, converts planned `convert` operations through the ffmpeg helper, verifies copied size, skips blocked tracks, maps conversion failures to explicit statuses, and never writes outside final output folder. |
| `ppb/report.py` | JSON and text reports | Writes dry-run reports, export sessions, `export_report.json` with copy/convert/M3U8/report/log metadata, and human-readable `export_report.txt`. |
| `ppb/logging_setup.py` | Logging setup | B8 standard-library logging helper for per-export `export.log` inside the final output folder. |
| `ppb/ffmpeg_tools.py` | Isolated ffmpeg executable resolution, single-file conversion helper, and low-level loudness measurement helper | Resolves ffmpeg from `PATH` or an explicit path, validates with `ffmpeg -version`, converts one source file into one destination inside an explicit output folder, removes a failed partial destination only when that file did not exist before the failed run, preserves sample rate by default, supports mp3/flac/wav/m4a/aac helper-level targets, and can measure loudness via read-only ffmpeg loudnorm first pass when called directly. |
| `ppb/tags.py` | Tag writing into exported copies | Planned; not implemented yet. |
| `ppb/m3u.py` | M3U8 generation | Writes UTF-8 `playlist.m3u8` from successfully copied and converted files and sanitizes EXTINF text safely. |
| `DOC/physical_playlist_job_v1_contract.md` | External neutral JSON contract documentation | Read-only for B8; contract was not changed. |
| `DOC/examples/playlist_job.v1.canonical.json` | Canonical sample job | Used for smoke validation; not edited in B8. |
| `example_playlist_job.json` | Additional sample job | Not edited in B8. |
| `tests/test_cli_u1.py` | CLI smoke/regression tests from earlier stages | Includes focused CLI copy-stage regression using only temporary files and output folders. Not changed in B9.3. |
| `tests/test_copier.py` | Focused copy-stage tests | Covers copy success, unchanged sources, export report serialization, missing sources, blocked tracks, convert failure/ffmpeg-missing behavior for invalid fixture audio, and destination conflict behavior. |
| `tests/test_ffmpeg_conversion.py` | Focused B9.3 conversion tests | Uses only pytest temporary folders and synthetic WAV files from Python stdlib `wave`; covers successful WAV to MP3 conversion when ffmpeg is available, ffmpeg missing, ffmpeg failure, destination conflicts, overwrite, reports, logs, M3U8 behavior, and source immutability. Not changed in B10.1. |
| `tests/test_validator_u2.py` | Validator tests | Not edited in B6. |
| `tests/test_input_readers_u3.py` | Input reader tests | Not edited in B6. |
| `tests/test_planner_u4.py` | Planner tests | Not edited in B6. |

## Current Stage Changes

| File path | Reason for change |
|---|---|
| `ppb/ffmpeg_tools.py` | Added `measure_loudness_first_pass()` with structured success/failure data, ffmpeg resolution reuse, read-only `loudnorm` first-pass execution, JSON parsing from stderr/stdout, and clear diagnostics for missing ffmpeg or unparseable loudnorm output. |
| `ppb/cli.py` | Added `--skip-loudness` to `--help` as a reserved no-op option without wiring loudness processing into export. |
| `README.md` | Documented B10.1 as loudness measurement groundwork and clarified that normalization is not integrated. |
| `PROJECT_FILES.md` | Updated the stage description, file map, current-stage changes, generated files, and safety notes for B10.1. |

## Generated/Runtime Files

These files and folders are generated during local runs and should not be edited manually:

| Path | Notes |
|---|---|
| `out/` | Example output root used by smoke commands. Contains timestamped export folders when commands are run. |
| `<final_output_dir>/export_session.json` | Generated session handoff file written by the output-folder stage. |
| `<final_output_dir>/export_report.json` | Generated JSON report with per-track copy/convert statuses plus run context, totals, warnings/errors, M3U8 metadata, report/log paths, and ffmpeg stderr summaries when conversion fails. |
| `<final_output_dir>/export_report.txt` | Generated human-readable report summarizing the completed real run. |
| `<final_output_dir>/export.log` | Generated standard-library log for the completed real run. |
| `<final_output_dir>/playlist.m3u8` | Generated UTF-8 playlist file created after export when `settings.generate_m3u8` is true or omitted. |
| `dry_run_report.json` | Optional dry-run report when `--report` is used without an explicit path. |
| Loudness measurement files | None. The B10.1 helper writes ffmpeg output to the `null` muxer and does not create audio or report files. |
| `test_runtime/` | Test runtime workspace; ignored by git. |
| `C:\Temp\project_pytest\` | Recommended external pytest temp root for Windows/YandexDisk runs when available. |
| `.pytest_cache/` | Pytest cache; ignored by git. |
| `__pycache__/` | Python bytecode cache; ignored by git. |

## Safety Notes

- Source audio files are never modified.
- The copy, conversion, M3U8, report, and log stages write only inside the selected final output folder.
- The ffmpeg helper writes only to a destination that resolves inside an explicitly provided output folder and refuses to overwrite unless `overwrite=True`.
- Failed ffmpeg partial output is removed only when the destination did not exist before the failed conversion attempt.
- Source files are passed to ffmpeg only as inputs; conversion never runs in-place on source audio.
- The B10.1 loudness helper passes the measured audio path to ffmpeg only as an input and writes output to the `null` muxer.
- The main CLI export workflow does not run loudness measurement or normalization yet.
- Existing destination files are not overwritten unless `--overwrite` is active.
- Planned convert tracks are executed only for supported helper formats (`mp3`, `flac`, `wav`, `m4a`, `aac`).
- Loudness normalization, tag writing, and resume are still not implemented in B10.1.
