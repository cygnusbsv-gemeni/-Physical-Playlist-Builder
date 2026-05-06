# Project Files - Physical Playlist Builder

## Last Updated

2026-05-06

## Current Stage

B12.4.3 - Minimal fused MP3 execution path.

The project keeps the B12.4.1 runtime-only effective output format override: `--output-format mp3` derives an in-memory effective `PlaylistJob` before dry-run planning and real execution, so planning and execution use the same effective output format without modifying the input JSON or `physical_playlist_job.v1`.

B12.4.2 added the low-level helper `normalize_loudness_and_encode_mp3_from_source()` in `ppb/ffmpeg_tools.py`.

B12.4.3 wires that helper into the real export execution path for the narrow eligible mode only: effective output format is `mp3`, `settings.normalize_loudness=true`, `--skip-loudness` is not passed, and the run is not `--dry-run`. In that mode, eligible tracks are measured directly from `source_path` as read-only FFmpeg input, then loudnorm second pass and MP3 encoding write a temporary `.tmp.mp3` inside `final_output_dir` and create the final `.mp3` only after successful output.

Successful fused exports keep the existing main status `converted` and add fused-specific fields such as `audio_action="fused_loudnorm_encode"`, `measurement_source="source"`, `normalization_output="final_mp3"`, and `output_format_effective="mp3"`. This preserves compatibility with existing tag writing and M3U8 inclusion. The regular loudness stage recognizes fused results and does not measure/normalize them a second time. Fused post-normalization verification is skipped in B12.4.3 and recorded with a clear reason.

The existing pipeline remains intact for non-fused modes: `output_format=source`, `normalize_loudness=false`, `--skip-loudness`, `--dry-run`, missing ffmpeg paths, ordinary copy/convert exports, and conservative `--resume` reuse.

## Project File Map

| File path | Responsibility | Important notes |
|---|---|---|
| `README.md` | Project documentation and usage instructions | Must describe only implemented behavior. Updated for B12.4.3 minimal fused MP3 execution path, validation commands, safety rules, current limitations, and next-stage handoff. |
| `PROJECT_FILES.md` | File map, current stage changes, and generated/runtime file notes | Updated after each completed stage. |
| `requirements.txt` | Python/test dependencies | Includes `pytest` for tests and `mutagen` for tag writing. |
| `ppb/__init__.py` | Package marker | No runtime logic. |
| `ppb/cli.py` | CLI entry point: args, input read, validation, effective runtime settings derivation, planning, fused-mode decision, resume preflight/comparison/execution logging, output-folder creation or resume-folder preparation, export/loudness/tag/M3U8/report/log execution, progress output, final summary | B12.4.1 adds `--output-format mp3` and `_derive_effective_job()`. B12.4.3 adds the narrow fused-enabled decision for real MP3 loudness exports and ensures fused results are not measured/normalized again by the regular loudness stage. `--resume` still requires `--no-create-subfolder` and an existing final output folder. Source files are never modified. |
| `ppb/contract.py` | Neutral input contract dataclasses (`TrackEntry`, `PlaylistJob`, settings) | Independent of MusicLib Web or any database. |
| `ppb/validator.py` | Validation and normalization for `physical_playlist_job.v1` | Blocked tracks remain allowed in non-strict mode and are skipped later. |
| `ppb/input_readers.py` | TXT / CSV / M3U / M3U8 input readers | Converts convenience inputs into the neutral `PlaylistJob` path. |
| `ppb/planner.py` | Dry-run and operation planning | Produces planned `copy`, `convert`, blocked, and error operations. |
| `ppb/filesystem.py` | Safe output folder creation and `export_session.json` writing | Does not copy audio files directly. |
| `ppb/copier.py` | Real export-stage logic | Copies planned `copy` operations, makes copied exported files writable after copy, converts planned `convert` operations through ffmpeg helpers, verifies copied size, skips blocked tracks, maps conversion failures to explicit statuses, reuses execution-validated safe resume candidates as `resumed`, and never writes outside final output folder. B12.4.3 adds minimal fused MP3 execution for eligible real exports: source loudness measurement, source-to-final-MP3 loudnorm encode through `normalize_loudness_and_encode_mp3_from_source()`, successful result as `converted` with `audio_action="fused_loudnorm_encode"`, and failure cleanup without successful final MP3 reporting. |
| `ppb/report.py` | JSON and text reports | Writes dry-run reports, export sessions, `export_report.json` with copy/convert/resumed/loudness measurement/loudness normalization/post-normalization verification/tag/M3U8/report/log metadata, tag totals, per-track `tag_status` fields, per-track resume fields, B12.3 resume comparison data, and resume execution totals when requested; writes human-readable `export_report.txt` with loudness summary, verification summary, tag-writing summary, resume preflight/comparison/execution sections, and per-track loudness/tag issues. |
| `ppb/resume.py` | Resume preflight state discovery, conservative comparison planning, and candidate indexing helper | Loads only `export_session.json` and `export_report.json` from the selected final output folder, handles missing/malformed/unexpected JSON safely, validates known prior final-output paths, rejects trusted generated paths outside the selected final output folder, compares current planned operations with prior track results and existing output files, returns conservative comparison candidates/totals, and exposes candidates by operation index for execution. It does not implement retry or fail-fast behavior. |
| `ppb/logging_setup.py` | Logging setup | B8 standard-library logging helper for per-export `export.log` inside the final output folder. |
| `ppb/ffmpeg_tools.py` | Isolated ffmpeg executable resolution, single-file conversion helper, low-level loudness measurement helper, low-level loudness normalization helper, and source-to-final-MP3 loudnorm helper | Resolves ffmpeg from `PATH` or an explicit path, validates with `ffmpeg -version`, converts one source file into one destination inside an explicit output folder, removes failed partial destinations only when safe, preserves sample rate by default, supports mp3/flac/wav/m4a/aac helper-level targets, measures loudness via read-only ffmpeg loudnorm first pass, and normalizes exported copies via loudnorm second pass using safe temporary files inside the final output folder. `normalize_loudness_and_encode_mp3_from_source()` reads the original source file only as input, writes a unique `.tmp.mp3` only inside `final_output_dir`, requires a final `.mp3` path inside `final_output_dir`, avoids source metadata with `-map_metadata -1`, verifies non-empty temp output, and moves/replaces final MP3 only after success. Starting with B12.4.3 it is used by the narrow fused MP3 execution path. |
| `ppb/tags.py` | Isolated tag-writing helper for exported copies | Provides `TagWriteResult` and `write_tags_to_exported_file()` for one already-exported file inside `final_output_dir`; makes the exported copy writable before writing and retries permission-only failures a few times; supports MP3 ID3v2.3/ID3v2.4, FLAC VorbisComment, and M4A/MP4 metadata atoms through mutagen; ignores `source_path` metadata and is called by the CLI only for final exported copies. |
| `ppb/m3u.py` | M3U8 generation | Writes UTF-8 `playlist.m3u8` from successfully copied, converted, and safely resumed files while preserving playlist order, and sanitizes EXTINF text safely. |
| `DOC/physical_playlist_job_v1_contract.md` | External neutral JSON contract documentation | Contract was not changed in B11.3. |
| `DOC/examples/playlist_job.v1.canonical.json` | Canonical sample job | Not edited in B11.3. |
| `example_playlist_job.json` | Additional sample job | Not edited in B11.3. |
| `tests/test_cli_u1.py` | CLI smoke/regression tests from earlier stages plus focused B12.4.1 runtime output-format override coverage | Includes focused checks for `--output-format` help output, dry-run override behavior, omitted override behavior, unchanged input JSON, and effective format propagation into the execution path. |
| `tests/test_copier.py` | Focused copy-stage tests | Covers copy success, unchanged sources, export report serialization, missing sources, blocked tracks, convert failure/ffmpeg-missing behavior for invalid fixture audio, and destination conflict behavior. |
| `tests/test_ffmpeg_conversion.py` | Focused B9.3 conversion tests | Uses only pytest temporary folders and synthetic WAV files from Python stdlib `wave`; covers successful WAV to MP3 conversion when ffmpeg is available, ffmpeg missing, ffmpeg failure, destination conflicts, overwrite, reports, logs, M3U8 behavior, and source immutability. Not changed in B11.3. |
| `tests/test_loudness_processing.py` | Focused loudness tests | Uses only pytest temporary folders and synthetic audio. Includes B12.4.2 helper coverage and B12.4.3 fused execution coverage: fused eligibility, source-path measurement, final MP3 output, `converted` result with `audio_action="fused_loudnorm_encode"`, prevention of duplicate regular loudness processing, output_format=source fallback, dry-run safety, and failure cleanup. |
| `tests/test_tag_writing.py` | Focused B11.3 tag-writing tests | Uses only synthetic audio and temporary files; covers MP3 ID3v2.4/v2.3, FLAC VorbisComment, M4A/MP4, `write_tags=false`, `--skip-tags`, unsupported WAV, path safety, skipped non-exported tracks, preserved playlist paths, and per-track tag failures. |
| `tests/test_validator_u2.py` | Validator tests | Not edited in B11.3. |
| `tests/test_input_readers_u3.py` | Input reader tests | Not edited in B11.3. |
| `tests/test_planner_u4.py` | Planner tests | Not edited in B11.3. |

## Current Stage Changes

| File path | Reason for change |
|---|---|
| `ppb/cli.py` | Added the narrow B12.4.3 fused-enabled decision for real exports and ensured fused results are not measured/normalized a second time by the regular loudness stage. |
| `ppb/copier.py` | Wired the existing low-level source-to-final-MP3 loudnorm helper into the eligible real export path. Successful fused exports return `status="converted"` plus `audio_action="fused_loudnorm_encode"` and related loudness/effective-format fields. Measurement failures and fused encode failures are reported without crashing the whole export or leaving a successful final MP3. |
| `tests/test_loudness_processing.py` | Added focused B12.4.3 coverage for fused eligibility, source-path measurement, final MP3 creation, converted result shape, duplicate loudness-stage prevention, output_format=source fallback, dry-run safety, and failure cleanup. |

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
| `.<track>.ppb-loudnorm-*.tmp<ext>` inside `<final_output_dir>/` | Temporary loudness normalization output created by the existing exported-copy normalization helper. It is replaced into the exported copy on success or removed on failure; it should not be edited manually. |
| `.<stem>.ppb-loudnorm-*.tmp.mp3` inside `<final_output_dir>/` | Temporary MP3 output created by the fused source-to-final-MP3 loudnorm path. Starting with B12.4.3 this file may appear during eligible real CLI/export runs. It is removed on failure when possible and moved/replaced as the final `.mp3` only after successful non-empty FFmpeg output. It must not appear in `playlist.m3u8`. |
| `test_runtime/` | Test runtime workspace; ignored by git. |
| `C:\Temp\project_pytest\` | Recommended external pytest temp root for Windows/YandexDisk runs when available. |
| `C:\Temp\project_pytest\b12_4_1_cli\` | Recommended external temp folder for focused B12.4.1 CLI override tests. In one local Windows run, pytest reached session cleanup and then hit `PermissionError: [WinError 5]`; this should be treated as a temp/cleanup environment issue unless reproduced before cleanup with a safe external `--basetemp`. |
| `C:\Temp\project_pytest\b12_4_2_loudness\` | Recommended external temp folder for focused B12.4.2 loudness helper tests. In one local Windows run, direct pytest setup/removal hit `PermissionError: [WinError 5]`; a diagnostic rerun with a temporary directory-permission workaround passed `18 passed in 2.38s`. |
| `C:\Temp\project_pytest\b12_3_copier\` | Recommended external temp folder for focused copy-stage regression tests after B12.3. |
| `C:\Temp\project_pytest\b12_3_regression\` | Recommended external temp folder for the focused B12.3 regression subset. |
| `C:\Temp\project_pytest\b12_3_resume_*` | Temporary folders used for local B12.3 resume smoke checks. |
| `.pytest_cache/` | Pytest cache; ignored by git. |
| `__pycache__/` | Python bytecode cache; ignored by git. |

B12.4.3 wires the fused helper into eligible real CLI/export runs, so the fused `.tmp.mp3` pattern may now appear during real exports. These temporary files are implementation details and should not be edited manually or referenced in playlists.

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
- `--output-format mp3` is applied only to the current CLI run.
- The CLI derives an in-memory effective job/settings value and does not rewrite the input JSON file.
- The override does not change `physical_playlist_job.v1` or canonical examples.
- The same effective output format is used for dry-run planning and real execution to prevent plan/execution mismatch.
- B12.4.3 wires `normalize_loudness_and_encode_mp3_from_source()` into the CLI/export path only for the narrow fused eligibility mode: effective output format `mp3`, `settings.normalize_loudness=true`, no `--skip-loudness`, and real non-dry-run export.
- In fused mode, source files are read only as FFmpeg inputs for measurement and source-to-final-MP3 encoding.
- In fused mode, source files are never modified, tagged, normalized in-place, renamed, moved, or deleted.
- Fused temporary and final MP3 files are written only inside `final_output_dir`.
- Fused temporary `.tmp.mp3` files must not be included in `playlist.m3u8` or treated as final outputs.
- Successful fused tracks keep status `converted` and add `audio_action="fused_loudnorm_encode"` so existing tag writing and M3U8 generation continue through the normal converted-track path.
- The regular loudness stage skips second measurement/normalization for fused results.
- Fused post-normalization verification is skipped in B12.4.3 and recorded with a clear reason.
- B12.4.3 does not implement fused report redesign, loudness cache, parallel processing, album normalization, ReplayGain/R128 tag-only mode, or changes to `physical_playlist_job.v1`.
