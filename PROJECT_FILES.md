# Project Files - Physical Playlist Builder

## Last Updated

2026-05-03

## Current Stage

B12.3 - Actual resume reuse for safe candidates.

The project keeps the B11.3 runtime export behavior: it validates neutral playlist input, builds a dry-run plan, creates or prepares a safe output folder, writes `export_session.json`, copies tracks planned as `copy`, converts tracks planned as `convert` through `ppb/ffmpeg_tools.py`, measures loudness for newly exported final output files when `settings.normalize_loudness=true`, normalizes those exported copies with ffmpeg `loudnorm` second pass, verifies loudness again after successful normalization, writes normalized tags to newly exported final copies when `settings.write_tags=true` and `--skip-tags` is not passed, generates a UTF-8 `playlist.m3u8` from final copied/converted/resumed files, writes `export_report.json`, writes human-readable `export_report.txt`, writes `export.log`, and prints a final CLI summary. B12.3 extends `--resume` from comparison planning into actual conservative reuse: only `safe_to_reuse_candidate=true` tracks that still pass execution-time path, existence, and copy-size checks are reported as `resumed` and skipped by copy/conversion/loudness/tag writing. Unsafe or invalidated candidates continue through normal behavior. A local B12.3 hardening patch makes copied exported files writable after copy and adds small permission-only retries before loudness replacement and tag writing for Windows/cloud-sync `PermissionError` / `WinError 5` cases. General retry and fail-fast remain not implemented.

## Project File Map

| File path | Responsibility | Important notes |
|---|---|---|
| `README.md` | Project documentation and usage instructions | Must describe only implemented behavior. Updated for B12.3 actual safe resume reuse behavior, validation commands, safety rules, and current limitations. |
| `PROJECT_FILES.md` | File map, current stage changes, and generated/runtime file notes | Updated after each completed stage. |
| `requirements.txt` | Python/test dependencies | Includes `pytest` for tests and `mutagen` for tag writing. |
| `ppb/__init__.py` | Package marker | No runtime logic. |
| `ppb/cli.py` | CLI entry point: args, input read, validation, planning, resume preflight/comparison/execution logging, output-folder creation or resume-folder preparation, export/loudness/tag/M3U8/report/log execution, progress output, final summary | `--resume` requires `--no-create-subfolder` and an existing final output folder. It prints/logs/reports resume metadata, passes safe candidates into the copy stage for actual reuse, does not enable overwrite automatically, and keeps retry/fail-fast unimplemented. It never measures, normalizes, or tags source files directly. |
| `ppb/contract.py` | Neutral input contract dataclasses (`TrackEntry`, `PlaylistJob`, settings) | Independent of MusicLib Web or any database. |
| `ppb/validator.py` | Validation and normalization for `physical_playlist_job.v1` | Blocked tracks remain allowed in non-strict mode and are skipped later. |
| `ppb/input_readers.py` | TXT / CSV / M3U / M3U8 input readers | Converts convenience inputs into the neutral `PlaylistJob` path. |
| `ppb/planner.py` | Dry-run and operation planning | Produces planned `copy`, `convert`, blocked, and error operations. |
| `ppb/filesystem.py` | Safe output folder creation and `export_session.json` writing | Does not copy audio files directly. |
| `ppb/copier.py` | Real export-stage logic | Copies planned `copy` operations, makes copied exported files writable after copy, converts planned `convert` operations through the ffmpeg helper, verifies copied size, skips blocked tracks, maps conversion failures to explicit statuses, reuses execution-validated safe resume candidates as `resumed`, records per-track resume fields, and never writes outside final output folder. |
| `ppb/report.py` | JSON and text reports | Writes dry-run reports, export sessions, `export_report.json` with copy/convert/resumed/loudness measurement/loudness normalization/post-normalization verification/tag/M3U8/report/log metadata, tag totals, per-track `tag_status` fields, per-track resume fields, B12.3 resume comparison data, and resume execution totals when requested; writes human-readable `export_report.txt` with loudness summary, verification summary, tag-writing summary, resume preflight/comparison/execution sections, and per-track loudness/tag issues. |
| `ppb/resume.py` | Resume preflight state discovery, conservative comparison planning, and candidate indexing helper | Loads only `export_session.json` and `export_report.json` from the selected final output folder, handles missing/malformed/unexpected JSON safely, validates known prior final-output paths, rejects trusted generated paths outside the selected final output folder, compares current planned operations with prior track results and existing output files, returns conservative comparison candidates/totals, and exposes candidates by operation index for execution. It does not implement retry or fail-fast behavior. |
| `ppb/logging_setup.py` | Logging setup | B8 standard-library logging helper for per-export `export.log` inside the final output folder. |
| `ppb/ffmpeg_tools.py` | Isolated ffmpeg executable resolution, single-file conversion helper, low-level loudness measurement helper, and low-level loudness normalization helper | Resolves ffmpeg from `PATH` or an explicit path, validates with `ffmpeg -version`, converts one source file into one destination inside an explicit output folder, removes a failed partial destination only when that file did not exist before the failed run, preserves sample rate by default, supports mp3/flac/wav/m4a/aac helper-level targets, measures loudness via read-only ffmpeg loudnorm first pass for exported copies, and normalizes exported copies via loudnorm second pass using safe temporary files inside the final output folder. Before replacing the exported copy after successful loudnorm output, it makes the destination writable and retries permission-only replacement failures a few times. The CLI also reuses the measurement helper for post-normalization verification. |
| `ppb/tags.py` | Isolated tag-writing helper for exported copies | Provides `TagWriteResult` and `write_tags_to_exported_file()` for one already-exported file inside `final_output_dir`; makes the exported copy writable before writing and retries permission-only failures a few times; supports MP3 ID3v2.3/ID3v2.4, FLAC VorbisComment, and M4A/MP4 metadata atoms through mutagen; ignores `source_path` metadata and is called by the CLI only for final exported copies. |
| `ppb/m3u.py` | M3U8 generation | Writes UTF-8 `playlist.m3u8` from successfully copied, converted, and safely resumed files while preserving playlist order, and sanitizes EXTINF text safely. |
| `DOC/physical_playlist_job_v1_contract.md` | External neutral JSON contract documentation | Contract was not changed in B11.3. |
| `DOC/examples/playlist_job.v1.canonical.json` | Canonical sample job | Not edited in B11.3. |
| `example_playlist_job.json` | Additional sample job | Not edited in B11.3. |
| `tests/test_cli_u1.py` | CLI smoke/regression tests from earlier stages | Includes focused CLI copy-stage regression using only temporary files and output folders. Not changed in B11.3. |
| `tests/test_copier.py` | Focused copy-stage tests | Covers copy success, unchanged sources, export report serialization, missing sources, blocked tracks, convert failure/ffmpeg-missing behavior for invalid fixture audio, and destination conflict behavior. |
| `tests/test_ffmpeg_conversion.py` | Focused B9.3 conversion tests | Uses only pytest temporary folders and synthetic WAV files from Python stdlib `wave`; covers successful WAV to MP3 conversion when ffmpeg is available, ffmpeg missing, ffmpeg failure, destination conflicts, overwrite, reports, logs, M3U8 behavior, and source immutability. Not changed in B11.3. |
| `tests/test_loudness_processing.py` | Focused loudness tests | Uses only pytest temporary folders and synthetic audio. In B11.3 one stale tag expectation was updated from `not_implemented` to the current `unsupported_format` behavior for WAV outputs. |
| `tests/test_tag_writing.py` | Focused B11.3 tag-writing tests | Uses only synthetic audio and temporary files; covers MP3 ID3v2.4/v2.3, FLAC VorbisComment, M4A/MP4, `write_tags=false`, `--skip-tags`, unsupported WAV, path safety, skipped non-exported tracks, preserved playlist paths, and per-track tag failures. |
| `tests/test_validator_u2.py` | Validator tests | Not edited in B11.3. |
| `tests/test_input_readers_u3.py` | Input reader tests | Not edited in B11.3. |
| `tests/test_planner_u4.py` | Planner tests | Not edited in B11.3. |

## Current Stage Changes

| File path | Reason for change |
|---|---|
| `ppb/resume.py` | Kept conservative comparison and added candidate indexing for B12.3 execution reuse. Comparison now marks whether candidates apply to execution. |
| `ppb/cli.py` | Feeds resume comparison candidates into the copy stage, prepares explicit resume folders without enabling audio overwrite, logs resume execution start/completion, and skips loudness/tag processing for `resumed` tracks. |
| `ppb/copier.py` | Added `resumed` status, execution-time reuse validation, per-track resume fields, and resume totals while preserving normal copy/convert behavior for unsafe or invalidated candidates. |
| `ppb/report.py` | Added resume execution totals, per-track resume fields in JSON via copy results, `resumed` totals, and a concise `Resume Execution` section in `export_report.txt`. |
| `ppb/m3u.py` | Includes `resumed` output files in `playlist.m3u8` together with copied/converted files while preserving playlist order. |
| `README.md` | Updated the current stage, usage, safety rules, validation commands, current limitations, and next-stage note for B12.3 actual safe reuse. |
| `PROJECT_FILES.md` | Updated the stage description, file map, current-stage changes, generated file notes, and safety notes for B12.3. |
| `ppb/copier.py` | Local B12.3 hardening: copied exported files are made writable after `shutil.copy2()` so read-only source attributes do not block later loudness/tag writing. |
| `ppb/ffmpeg_tools.py` | Local B12.3 hardening: loudness replacement makes the exported destination writable and retries permission-only replace failures before reporting failure while keeping the original exported file intact. |
| `ppb/tags.py` | Local B12.3 hardening: tag writing makes the exported destination writable and retries permission-only mutagen save failures. |
| `tests/test_copier.py` | Added focused read-only copied-file coverage. |
| `tests/test_loudness_processing.py` | Added focused read-only replacement and final permission-failure cleanup coverage. |
| `tests/test_tag_writing.py` | Added focused read-only exported MP3 tag-writing coverage. |

## Generated/Runtime Files

These files and folders are generated during local runs and should not be edited manually:

| Path | Notes |
|---|---|
| `out/` | Example output root used by smoke commands. Contains timestamped export folders when commands are run. |
| `<final_output_dir>/export_session.json` | Generated session handoff file written by the output-folder stage. |
| `<final_output_dir>/export_report.json` | Generated JSON report with per-track copy/convert/resumed statuses plus loudness measurement, normalization, post-normalization verification, tag-writing fields, resume fields, size fields, run context, totals, resume execution totals, loudness totals, tag totals, warning traces, final warnings/errors, M3U8 metadata, B12.3 resume metadata/comparison data when requested, report/log paths, and stderr summaries. |
| `<final_output_dir>/export_report.txt` | Generated human-readable report summarizing the completed real run, including copied/converted/resumed totals, loudness measured/normalized/skipped/failed/ffmpeg-missing totals, post-normalization verification totals, tag-writing status/totals, resume preflight/comparison/execution sections when requested, and per-track loudness/tag issues. |
| `<final_output_dir>/export.log` | Generated standard-library log for the completed real run, including resume preflight/comparison/execution when requested, per-track reused/not-reused reasons, loudness measurement, normalization, verification, tag-writing, M3U8, and report milestones plus concise per-track outcomes. |
| `<final_output_dir>/playlist.m3u8` | Generated UTF-8 playlist file created after export when `settings.generate_m3u8` is true or omitted; includes copied, converted, and safely resumed tracks. |
| `dry_run_report.json` | Optional dry-run report when `--report` is used without an explicit path. |
| `.<track>.ppb-loudnorm-*.tmp<ext>` inside `<final_output_dir>/` | Temporary loudness normalization output created only during an active normalization attempt. It is replaced into the exported copy on success or removed on failure; it should not be edited manually. |
| `test_runtime/` | Test runtime workspace; ignored by git. |
| `C:\Temp\project_pytest\` | Recommended external pytest temp root for Windows/YandexDisk runs when available. |
| `C:\Temp\project_pytest\b12_3_copier\` | Recommended external temp folder for focused copy-stage regression tests after B12.3. |
| `C:\Temp\project_pytest\b12_3_regression\` | Recommended external temp folder for the focused B12.3 regression subset. |
| `C:\Temp\project_pytest\b12_3_resume_*` | Temporary folders used for local B12.3 resume smoke checks. |
| `.pytest_cache/` | Pytest cache; ignored by git. |
| `__pycache__/` | Python bytecode cache; ignored by git. |

## Safety Notes

- Source audio files are never modified.
- The copy, conversion, tag, M3U8, report, and log stages write only inside the selected final output folder.
- Reused resume audio files are not copied, converted, normalized, tagged, renamed, deleted, or overwritten.
- The ffmpeg helper writes only to a destination that resolves inside an explicitly provided output folder and refuses to overwrite unless `overwrite=True`.
- Failed ffmpeg partial output is removed only when the destination did not exist before the failed conversion attempt.
- Source files are passed to ffmpeg only as inputs; conversion never runs in-place on source audio.
- Loudness measurement passes only successfully exported destination files inside the final output folder to ffmpeg and writes output to the `null` muxer.
- Loudness normalization passes only successfully measured exported destination files inside the final output folder to ffmpeg.
- Loudness normalization writes temporary files only inside the final output folder and replaces the exported copy only after ffmpeg succeeds. Before replacement it makes the exported destination writable and retries permission-only Windows/cloud-sync failures a few times.
- Post-normalization verification measures only the final normalized exported file inside the final output folder and does not modify or delete it.
- Post-normalization verification failures are reported as loudness verification problems and do not downgrade the copied/converted/normalized export status.
- The main CLI workflow never measures or normalizes source audio files directly.
- Loudness processing is skipped when `settings.normalize_loudness=false` or `--skip-loudness` is passed.
- Loudness measurement or normalization failures keep copied/converted outputs intact and remove only temporary normalization output created by the failed attempt.
- Existing destination files are not overwritten unless `--overwrite` is active.
- Planned convert tracks are executed only for supported helper formats (`mp3`, `flac`, `wav`, `m4a`, `aac`).
- Loudness normalization helper-level outputs are currently supported for `mp3`, `flac`, `wav`, `m4a`, and `aac`; unsupported final extensions are reported as failed normalization attempts.
- `ppb/tags.py` refuses to write tags outside the final output folder and refuses missing exported files.
- `ppb/tags.py` uses only the provided normalized metadata dict, ignores `source_path` metadata, and never reads or modifies source audio files.
- The main CLI workflow calls `ppb/tags.py` only after copy/conversion/loudness, only for `copied` or `converted` track results with destination files inside the final output folder, and only when `settings.write_tags=true` and `--skip-tags` is not passed. `resumed` tracks are deliberately skipped. Tag writing makes the exported destination writable and retries permission-only Windows/cloud-sync failures a few times.
- Per-track tag failures keep the exported audio file and are recorded in `export_report.json`, `export_report.txt`, and `export.log`.
- Resume preflight reads only `export_session.json` and `export_report.json` inside the selected final output folder.
- Resume preflight validates known prior final-output paths before reporting the state and refuses to trust generated prior paths outside the selected final output folder.
- Resume comparison checks current operations against prior report track results and existing output files only inside the selected final output folder.
- Resume execution consumes only `safe_to_reuse_candidate=true` candidates and revalidates current destination path, prior output path, file existence, and copy size before returning `resumed`.
- Unsafe resume candidates and safe candidates invalidated at execution time continue through normal copy/convert behavior and may report `destination_exists` without overwriting.
- `--resume` does not automatically enable `--overwrite`.
- General retry is not implemented, and fail-fast is not implemented. A small permission-only retry exists only for exported-copy loudness replacement and tag writing inside the final output folder.
