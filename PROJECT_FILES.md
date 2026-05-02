# Project Files - Physical Playlist Builder

## Last Updated

2026-05-02

## Current Stage

B8 - Add Human-Readable Report And Logging.

The project validates neutral playlist input, builds a dry-run plan, creates a safe output folder, writes `export_session.json`, copies tracks planned as `copy`, generates a UTF-8 `playlist.m3u8` from successfully copied files only, writes `export_report.json`, writes human-readable `export_report.txt`, writes `export.log`, and prints a clearer final CLI summary. Conversion, loudness normalization, tag writing, and resume are still not implemented.

## Project File Map

| File path | Responsibility | Important notes |
|---|---|---|
| `README.md` | Project documentation and usage instructions | Must describe only implemented behavior. Updated for B8 reporting and logging behavior. |
| `PROJECT_FILES.md` | File map, current stage changes, and generated/runtime file notes | Updated after each completed stage. |
| `requirements.txt` | Python/test dependencies | Core runtime has no third-party dependencies. |
| `ppb/__init__.py` | Package marker | No runtime logic. |
| `ppb/cli.py` | CLI entry point: args, input read, validation, planning, output-folder creation, copy/M3U8/report/log execution, progress output, final summary | B8 adds real-run `export.log`, `export_report.txt`, richer report metadata, and a final CLI summary without changing dry-run report behavior. |
| `ppb/contract.py` | Neutral input contract dataclasses (`TrackEntry`, `PlaylistJob`, settings) | Independent of MusicLib Web or any database. |
| `ppb/validator.py` | Validation and normalization for `physical_playlist_job.v1` | Blocked tracks remain allowed in non-strict mode and are skipped later. |
| `ppb/input_readers.py` | TXT / CSV / M3U / M3U8 input readers | Converts convenience inputs into the neutral `PlaylistJob` path. |
| `ppb/planner.py` | Dry-run and operation planning | Produces planned `copy`, `convert`, blocked, and error operations. |
| `ppb/filesystem.py` | Safe output folder creation and `export_session.json` writing | Does not copy audio files directly. |
| `ppb/copier.py` | B6 copy-stage logic | Copies only planned `copy` operations, verifies copied size, skips blocked tracks, reports convert as `not_implemented`, and never writes outside final output folder. |
| `ppb/report.py` | JSON and text reports | Writes dry-run reports, export sessions, `export_report.json` with copy/M3U8/report/log metadata, and human-readable `export_report.txt`. |
| `ppb/logging_setup.py` | Logging setup | B8 standard-library logging helper for per-export `export.log` inside the final output folder. |
| `ppb/ffmpeg_tools.py` | ffmpeg conversion / loudness helpers | Planned; not implemented yet. |
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
| `ppb/report.py` | Added B8 `export_report.txt` writer and enriched `export_report.json` with run timing, input/output context, totals, warnings/errors, and generated report/log paths while preserving existing summary/tracks/M3U fields. |
| `ppb/logging_setup.py` | Added B8 standard-library logging setup/cleanup for `export.log` inside the final output folder. |
| `ppb/cli.py` | Hooked B8 logging, text report writing, richer JSON metadata, report/log error handling, and final CLI summary into the existing real copy + M3U8 workflow. |
| `README.md` | Updated user documentation for implemented B8 reporting/logging behavior and generated output files. |
| `PROJECT_FILES.md` | Updated the stage description, file map, current-stage changes, and generated-file notes for B8. |

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
- Existing destination files are not overwritten unless `--overwrite` is active.
- Conversion, loudness normalization, tag writing, and resume are still not implemented in B8.
