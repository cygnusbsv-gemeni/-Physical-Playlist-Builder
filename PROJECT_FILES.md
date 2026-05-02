# Project Files - Physical Playlist Builder

## Last Updated

2026-05-02

## Current Stage

B9.1 - Add ffmpeg Detection And Isolated Conversion Helper.

The project validates neutral playlist input, builds a dry-run plan, creates a safe output folder, writes `export_session.json`, copies tracks planned as `copy`, generates a UTF-8 `playlist.m3u8` from successfully copied files only, writes `export_report.json`, writes human-readable `export_report.txt`, writes `export.log`, prints a clearer final CLI summary, and now includes an isolated ffmpeg utility layer for a later conversion stage. Planned convert tracks are still reported as `not_implemented` by the main workflow. Loudness normalization, tag writing, resume, and integrated conversion are still not implemented.

## Project File Map

| File path | Responsibility | Important notes |
|---|---|---|
| `README.md` | Project documentation and usage instructions | Must describe only implemented behavior. Updated for B9.1 ffmpeg helper and CLI option status. |
| `PROJECT_FILES.md` | File map, current stage changes, and generated/runtime file notes | Updated after each completed stage. |
| `requirements.txt` | Python/test dependencies | Core runtime has no third-party dependencies. |
| `ppb/__init__.py` | Package marker | No runtime logic. |
| `ppb/cli.py` | CLI entry point: args, input read, validation, planning, output-folder creation, copy/M3U8/report/log execution, progress output, final summary | B9.1 adds `--ffmpeg`, `--mp3-quality`, and `--audio-bitrate` options for the upcoming conversion stage. The options are parsed only and do not change processing behavior yet. |
| `ppb/contract.py` | Neutral input contract dataclasses (`TrackEntry`, `PlaylistJob`, settings) | Independent of MusicLib Web or any database. |
| `ppb/validator.py` | Validation and normalization for `physical_playlist_job.v1` | Blocked tracks remain allowed in non-strict mode and are skipped later. |
| `ppb/input_readers.py` | TXT / CSV / M3U / M3U8 input readers | Converts convenience inputs into the neutral `PlaylistJob` path. |
| `ppb/planner.py` | Dry-run and operation planning | Produces planned `copy`, `convert`, blocked, and error operations. |
| `ppb/filesystem.py` | Safe output folder creation and `export_session.json` writing | Does not copy audio files directly. |
| `ppb/copier.py` | B6 copy-stage logic | Copies only planned `copy` operations, verifies copied size, skips blocked tracks, reports convert as `not_implemented`, and never writes outside final output folder. |
| `ppb/report.py` | JSON and text reports | Writes dry-run reports, export sessions, `export_report.json` with copy/M3U8/report/log metadata, and human-readable `export_report.txt`. |
| `ppb/logging_setup.py` | Logging setup | B8 standard-library logging helper for per-export `export.log` inside the final output folder. |
| `ppb/ffmpeg_tools.py` | Isolated ffmpeg executable resolution and single-file conversion helper | B9.1 helper layer only. Resolves ffmpeg from `PATH` or an explicit path, validates with `ffmpeg -version`, converts one source file into one destination inside an explicit output folder, preserves sample rate by default, supports mp3/flac/wav/m4a/aac helper-level targets, and is not integrated into the main CLI workflow yet. |
| `ppb/tags.py` | Tag writing into exported copies | Planned; not implemented yet. |
| `ppb/m3u.py` | M3U8 generation | Writes UTF-8 `playlist.m3u8` from successfully copied files only and sanitizes EXTINF text safely. |
| `DOC/physical_playlist_job_v1_contract.md` | External neutral JSON contract documentation | Read-only for B8; contract was not changed. |
| `DOC/examples/playlist_job.v1.canonical.json` | Canonical sample job | Used for smoke validation; not edited in B8. |
| `example_playlist_job.json` | Additional sample job | Not edited in B8. |
| `tests/test_cli_u1.py` | CLI smoke/regression tests from earlier stages | B6.1 includes a focused CLI copy-stage regression using only temporary files and output folders. |
| `tests/test_copier.py` | Focused B6 copy-stage tests | Covers copy success, unchanged sources, export report serialization, missing sources, blocked tracks, convert-as-not-implemented, and destination conflict behavior. |
| `tests/test_validator_u2.py` | Validator tests | Not edited in B6. |
| `tests/test_input_readers_u3.py` | Input reader tests | Not edited in B6. |
| `tests/test_planner_u4.py` | Planner tests | Not edited in B6. |

## Current Stage Changes

| File path | Reason for change |
|---|---|
| `ppb/ffmpeg_tools.py` | Added B9.1 structured ffmpeg discovery and isolated single-file conversion helper with output-folder boundary checks. |
| `ppb/cli.py` | Added B9.1 conversion-related CLI options without wiring conversion into processing. |
| `README.md` | Documented that ffmpeg options/helper exist for the upcoming conversion stage while main workflow conversion remains not integrated. |
| `PROJECT_FILES.md` | Updated the stage description, file map, current-stage changes, and safety notes for B9.1. |

## Generated/Runtime Files

These files and folders are generated during local runs and should not be edited manually:

| Path | Notes |
|---|---|
| `out/` | Example output root used by smoke commands. Contains timestamped export folders when commands are run. |
| `<final_output_dir>/export_session.json` | Generated session handoff file written by the output-folder stage. |
| `<final_output_dir>/export_report.json` | Generated B8 JSON report with per-track copy statuses plus run context, totals, warnings/errors, M3U8 metadata, and report/log paths. |
| `<final_output_dir>/export_report.txt` | Generated B8 human-readable report summarizing the completed real run. |
| `<final_output_dir>/export.log` | Generated B8 standard-library log for the completed real run. |
| `<final_output_dir>/playlist.m3u8` | Generated UTF-8 playlist file created after copy when `settings.generate_m3u8` is true or omitted. |
| `dry_run_report.json` | Optional dry-run report when `--report` is used without an explicit path. |
| `test_runtime/` | Test runtime workspace; ignored by git. |
| `.pytest_cache/` | Pytest cache; ignored by git. |
| `__pycache__/` | Python bytecode cache; ignored by git. |

## Safety Notes

- Source audio files are never modified.
- The copy, M3U8, report, and log stages write only inside the selected final output folder.
- The B9.1 ffmpeg helper writes only to a destination that resolves inside an explicitly provided output folder and refuses to overwrite unless `overwrite=True`.
- Existing destination files are not overwritten unless `--overwrite` is active.
- Planned convert tracks remain `not_implemented` in the main workflow until a later stage.
- Loudness normalization, tag writing, and resume are still not implemented in B9.1.
