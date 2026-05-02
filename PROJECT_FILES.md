# Project Files - Physical Playlist Builder

## Last Updated

2026-05-02

## Current Stage

B7 - Generate M3U8 Playlist.

The project validates neutral playlist input, builds a dry-run plan, creates a safe output folder, writes `export_session.json`, copies tracks planned as `copy`, generates a UTF-8 `playlist.m3u8` from successfully copied files only, and writes `export_report.json` with both per-track copy results and M3U8 metadata. Conversion, loudness normalization, tag writing, and resume are still not implemented.

## Project File Map

| File path | Responsibility | Important notes |
|---|---|---|
| `README.md` | Project documentation and usage instructions | Must describe only implemented behavior. Updated for B7 M3U8 generation behavior. |
| `PROJECT_FILES.md` | File map, current stage changes, and generated/runtime file notes | Updated after each completed stage. |
| `requirements.txt` | Python/test dependencies | Core runtime has no third-party dependencies. |
| `ppb/__init__.py` | Package marker | No runtime logic. |
| `ppb/cli.py` | CLI entry point: args, input read, validation, planning, output-folder creation, copy/M3U8 execution, progress output | B7 adds `--m3u-name`, validates it as a safe leaf filename, generates M3U8 after copy, and writes M3U metadata into `export_report.json`. |
| `ppb/contract.py` | Neutral input contract dataclasses (`TrackEntry`, `PlaylistJob`, settings) | Independent of MusicLib Web or any database. |
| `ppb/validator.py` | Validation and normalization for `physical_playlist_job.v1` | Blocked tracks remain allowed in non-strict mode and are skipped later. |
| `ppb/input_readers.py` | TXT / CSV / M3U / M3U8 input readers | Converts convenience inputs into the neutral `PlaylistJob` path. |
| `ppb/planner.py` | Dry-run and operation planning | Produces planned `copy`, `convert`, blocked, and error operations. |
| `ppb/filesystem.py` | Safe output folder creation and `export_session.json` writing | Does not copy audio files directly. |
| `ppb/copier.py` | B6 copy-stage logic | Copies only planned `copy` operations, verifies copied size, skips blocked tracks, reports convert as `not_implemented`, and never writes outside final output folder. |
| `ppb/report.py` | JSON reports | Writes dry-run reports, export sessions, and `export_report.json` with copy and M3U8 metadata. |
| `ppb/logging_setup.py` | Logging setup | Planned; not implemented yet. |
| `ppb/ffmpeg_tools.py` | ffmpeg conversion / loudness helpers | Planned; not implemented yet. |
| `ppb/tags.py` | Tag writing into exported copies | Planned; not implemented yet. |
| `ppb/m3u.py` | M3U8 generation | B7 writes UTF-8 `playlist.m3u8` from successfully copied files only and sanitizes EXTINF text safely. |
| `DOC/physical_playlist_job_v1_contract.md` | External neutral JSON contract documentation | Read-only for B7; contract was not changed. |
| `DOC/examples/playlist_job.v1.canonical.json` | Canonical sample job | Used for smoke validation; not edited in B7. |
| `example_playlist_job.json` | Additional sample job | Not edited in B7. |
| `tests/test_cli_u1.py` | CLI smoke/regression tests from earlier stages | B6.1 includes a focused CLI copy-stage regression using only temporary files and output folders. |
| `tests/test_copier.py` | Focused B6 copy-stage tests | Covers copy success, unchanged sources, export report serialization, missing sources, blocked tracks, convert-as-not-implemented, and destination conflict behavior. |
| `tests/test_validator_u2.py` | Validator tests | Not edited in B6. |
| `tests/test_input_readers_u3.py` | Input reader tests | Not edited in B6. |
| `tests/test_planner_u4.py` | Planner tests | Not edited in B6. |

## Current Stage Changes

| File path | Reason for change |
|---|---|
| `ppb/m3u.py` | Added the B7 M3U8 generator, safe `--m3u-name` validation, EXTINF formatting, Unicode-safe text sanitization, and empty-playlist handling. |
| `ppb/cli.py` | Hooked M3U8 generation into the post-copy stage and exposed the new `--m3u-name` CLI option. |
| `ppb/report.py` | Extended `export_report.json` to include `m3u_path`, `m3u_track_count`, `m3u_status`, and optional warnings/errors while preserving track copy results. |
| `README.md` | Updated user documentation for B7 behavior, `generate_m3u8`, and the new example command. |
| `PROJECT_FILES.md` | Updated the stage description, file map, and generated-file notes for B7. |

## Generated/Runtime Files

These files and folders are generated during local runs and should not be edited manually:

| Path | Notes |
|---|---|
| `out/` | Example output root used by smoke commands. Contains timestamped export folders when commands are run. |
| `<final_output_dir>/export_session.json` | Generated session handoff file written by the output-folder stage. |
| `<final_output_dir>/export_report.json` | Generated B7 report with per-track copy statuses plus M3U8 metadata. |
| `<final_output_dir>/playlist.m3u8` | Generated UTF-8 playlist file created after copy when `settings.generate_m3u8` is true or omitted. |
| `dry_run_report.json` | Optional dry-run report when `--report` is used without an explicit path. |
| `test_runtime/` | Test runtime workspace; ignored by git. |
| `.pytest_cache/` | Pytest cache; ignored by git. |
| `__pycache__/` | Python bytecode cache; ignored by git. |

## Safety Notes

- Source audio files are never modified.
- The copy and M3U8 stages write only inside the selected final output folder.
- Existing destination files are not overwritten unless `--overwrite` is active.
- Conversion, loudness normalization, tag writing, and resume are still not implemented in B7.
