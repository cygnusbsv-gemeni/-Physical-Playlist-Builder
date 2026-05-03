# Project Files - Physical Playlist Builder

## Last Updated

2026-05-03

## Current Stage

B12.2 - File-level resume comparison plan.

The project keeps the B11.3 runtime export behavior: it validates neutral playlist input, builds a dry-run plan, creates a safe output folder, writes `export_session.json`, copies tracks planned as `copy`, converts tracks planned as `convert` through `ppb/ffmpeg_tools.py`, measures loudness for successfully exported final output files when `settings.normalize_loudness=true`, normalizes those exported copies with ffmpeg `loudnorm` second pass, verifies loudness again after successful normalization, writes normalized tags to final exported copies when `settings.write_tags=true` and `--skip-tags` is not passed, generates a UTF-8 `playlist.m3u8` from final copied/converted files, writes `export_report.json`, writes human-readable `export_report.txt`, writes `export.log`, and prints a final CLI summary. B12.2 extends `--resume` with file-level comparison planning for prior `export_session.json`, prior `export_report.json`, current planned operations, and existing output files inside an explicit existing final output folder. It reports conservative safe/unsafe candidates for a future resume stage, but does not skip, retry, or reuse prior per-track results.

## Project File Map

| File path | Responsibility | Important notes |
|---|---|---|
| `README.md` | Project documentation and usage instructions | Must describe only implemented behavior. Updated for B12.2 resume comparison behavior, validation commands, safety rules, and current limitations. |
| `PROJECT_FILES.md` | File map, current stage changes, and generated/runtime file notes | Updated after each completed stage. |
| `requirements.txt` | Python/test dependencies | Includes `pytest` for tests and `mutagen` for tag writing. |
| `ppb/__init__.py` | Package marker | No runtime logic. |
| `ppb/cli.py` | CLI entry point: args, input read, validation, planning, resume preflight/comparison, output-folder creation, export/loudness/tag/M3U8/report/log execution, progress output, final summary | `--resume` requires `--no-create-subfolder` and an existing final output folder. It prints/logs/reports resume metadata and comparison totals, but still runs copy/conversion/loudness/tag/M3U8 behavior through the existing pipeline without skip/retry logic. It never measures, normalizes, or tags source files directly. |
| `ppb/contract.py` | Neutral input contract dataclasses (`TrackEntry`, `PlaylistJob`, settings) | Independent of MusicLib Web or any database. |
| `ppb/validator.py` | Validation and normalization for `physical_playlist_job.v1` | Blocked tracks remain allowed in non-strict mode and are skipped later. |
| `ppb/input_readers.py` | TXT / CSV / M3U / M3U8 input readers | Converts convenience inputs into the neutral `PlaylistJob` path. |
| `ppb/planner.py` | Dry-run and operation planning | Produces planned `copy`, `convert`, blocked, and error operations. |
| `ppb/filesystem.py` | Safe output folder creation and `export_session.json` writing | Does not copy audio files directly. |
| `ppb/copier.py` | Real export-stage logic | Copies planned `copy` operations, converts planned `convert` operations through the ffmpeg helper, verifies copied size, skips blocked tracks, maps conversion failures to explicit statuses, and never writes outside final output folder. |
| `ppb/report.py` | JSON and text reports | Writes dry-run reports, export sessions, `export_report.json` with copy/convert/loudness measurement/loudness normalization/post-normalization verification/tag/M3U8/report/log metadata, tag totals, per-track `tag_status` fields, and B12.2 resume comparison data when requested; writes human-readable `export_report.txt` with loudness summary, verification summary, tag-writing summary, resume preflight/comparison sections, and per-track loudness/tag issues. |
| `ppb/resume.py` | Resume preflight state discovery and B12.2 comparison planning | Loads only `export_session.json` and `export_report.json` from the selected final output folder, handles missing/malformed/unexpected JSON safely, validates known prior final-output paths, rejects trusted generated paths outside the selected final output folder, compares current planned operations with prior track results and existing output files, and returns conservative comparison candidates/totals. It does not implement actual skip, reuse, retry, or fail-fast behavior. |
| `ppb/logging_setup.py` | Logging setup | B8 standard-library logging helper for per-export `export.log` inside the final output folder. |
| `ppb/ffmpeg_tools.py` | Isolated ffmpeg executable resolution, single-file conversion helper, low-level loudness measurement helper, and low-level loudness normalization helper | Resolves ffmpeg from `PATH` or an explicit path, validates with `ffmpeg -version`, converts one source file into one destination inside an explicit output folder, removes a failed partial destination only when that file did not exist before the failed run, preserves sample rate by default, supports mp3/flac/wav/m4a/aac helper-level targets, measures loudness via read-only ffmpeg loudnorm first pass for exported copies, and normalizes exported copies via loudnorm second pass using safe temporary files inside the final output folder. The CLI also reuses the measurement helper for post-normalization verification. |
| `ppb/tags.py` | Isolated tag-writing helper for exported copies | Provides `TagWriteResult` and `write_tags_to_exported_file()` for one already-exported file inside `final_output_dir`; supports MP3 ID3v2.3/ID3v2.4, FLAC VorbisComment, and M4A/MP4 metadata atoms through mutagen; ignores `source_path` metadata and is called by the CLI only for final exported copies. |
| `ppb/m3u.py` | M3U8 generation | Writes UTF-8 `playlist.m3u8` from successfully copied and converted files and sanitizes EXTINF text safely. |
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
| `ppb/resume.py` | Added B12.2 file-level resume comparison planning for current operations vs prior report track results and existing output files. |
| `ppb/cli.py` | Builds/prints/logs resume comparison data before normal export execution without feeding candidates into copy/conversion/loudness/tag/M3U8 behavior. |
| `ppb/report.py` | Added B12.2 `resume_comparison` / `resume_comparison_totals` JSON fields and a concise `Resume Comparison` section in `export_report.txt`. |
| `README.md` | Updated the current stage, usage, safety rules, validation commands, current limitations, and next-stage note for B12.2. |
| `PROJECT_FILES.md` | Updated the stage description, file map, current-stage changes, generated file notes, and safety notes for B12.2. |

## Generated/Runtime Files

These files and folders are generated during local runs and should not be edited manually:

| Path | Notes |
|---|---|
| `out/` | Example output root used by smoke commands. Contains timestamped export folders when commands are run. |
| `<final_output_dir>/export_session.json` | Generated session handoff file written by the output-folder stage. |
| `<final_output_dir>/export_report.json` | Generated JSON report with per-track copy/convert statuses plus loudness measurement, normalization, post-normalization verification, tag-writing fields, size fields, run context, totals, loudness totals, tag totals, warning traces, final warnings/errors, M3U8 metadata, B12.2 resume metadata/comparison data when requested, report/log paths, and stderr summaries. |
| `<final_output_dir>/export_report.txt` | Generated human-readable report summarizing the completed real run, including loudness measured/normalized/skipped/failed/ffmpeg-missing totals, post-normalization verification totals, tag-writing status/totals, resume preflight/comparison sections when requested, and per-track loudness/tag issues. |
| `<final_output_dir>/export.log` | Generated standard-library log for the completed real run, including resume preflight/comparison when requested, loudness measurement, normalization, verification, tag-writing, M3U8, and report milestones plus concise per-track outcomes. |
| `<final_output_dir>/playlist.m3u8` | Generated UTF-8 playlist file created after export when `settings.generate_m3u8` is true or omitted. |
| `dry_run_report.json` | Optional dry-run report when `--report` is used without an explicit path. |
| `.<track>.ppb-loudnorm-*.tmp<ext>` inside `<final_output_dir>/` | Temporary loudness normalization output created only during an active normalization attempt. It is replaced into the exported copy on success or removed on failure; it should not be edited manually. |
| `test_runtime/` | Test runtime workspace; ignored by git. |
| `C:\Temp\project_pytest\` | Recommended external pytest temp root for Windows/YandexDisk runs when available. |
| `C:\Temp\project_pytest\b12_2_tags\` | Recommended external temp folder for focused tag-writing regression tests after B12.2. |
| `C:\Temp\project_pytest\b12_2_regression\` | Recommended external temp folder for the focused B12.2 regression subset. |
| `C:\Temp\project_pytest\b12_2_resume_*` | Temporary folders used for local B12.2 resume smoke checks. |
| `.pytest_cache/` | Pytest cache; ignored by git. |
| `__pycache__/` | Python bytecode cache; ignored by git. |

## Safety Notes

- Source audio files are never modified.
- The copy, conversion, tag, M3U8, report, and log stages write only inside the selected final output folder.
- The ffmpeg helper writes only to a destination that resolves inside an explicitly provided output folder and refuses to overwrite unless `overwrite=True`.
- Failed ffmpeg partial output is removed only when the destination did not exist before the failed conversion attempt.
- Source files are passed to ffmpeg only as inputs; conversion never runs in-place on source audio.
- Loudness measurement passes only successfully exported destination files inside the final output folder to ffmpeg and writes output to the `null` muxer.
- Loudness normalization passes only successfully measured exported destination files inside the final output folder to ffmpeg.
- Loudness normalization writes temporary files only inside the final output folder and replaces the exported copy only after ffmpeg succeeds.
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
- The main CLI workflow calls `ppb/tags.py` only after copy/conversion/loudness, only for `copied` or `converted` track results with destination files inside the final output folder, and only when `settings.write_tags=true` and `--skip-tags` is not passed.
- Per-track tag failures keep the exported audio file and are recorded in `export_report.json`, `export_report.txt`, and `export.log`.
- Resume preflight reads only `export_session.json` and `export_report.json` inside the selected final output folder.
- Resume preflight validates known prior final-output paths before reporting the state and refuses to trust generated prior paths outside the selected final output folder.
- Resume comparison checks current operations against prior report track results and existing output files only inside the selected final output folder.
- Resume comparison candidates are reporting data only; copy, conversion, loudness, tag writing, and M3U8 behavior do not consume them.
- B12.2 does not implement actual file-level resume: prior per-track statuses are not reused, existing audio files are not skipped because of prior state, retry is not implemented, and fail-fast is not implemented.
