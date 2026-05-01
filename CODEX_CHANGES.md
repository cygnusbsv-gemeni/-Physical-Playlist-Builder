# CODEX_CHANGES

## 2026-05-01

- Renamed the canonical contract directory from `doc` to `DOC`.
- Reworked `ppb/contract.py` around neutral `PlaylistJob`, `PlaylistSettings`, and `TrackEntry` objects for `physical_playlist_job.v1`.
- Reworked `ppb/validator.py` to validate canonical `format + playlist + settings + tracks` input and to isolate legacy `schema` normalization behind a migration warning.
- Updated `ppb/cli.py` to print canonical `Format: physical_playlist_job.v1`, strict/non-strict validation behavior, and no-op stage messaging.
- Updated `README.md` after reviewing the new validation flow; it now points to the authoritative contract and canonical examples without producer-specific dependencies.
- Replaced `example_playlist_job.json` with canonical input.
- Added `DOC/examples/playlist_job.v1.canonical.json`.
- Updated CLI and validator tests for canonical input, strict mode, optional metadata, unknown fields, and legacy normalization.
- Added `requirements.txt` for test dependency installation.
- Adjusted CLI tests to use a project-local runtime workspace instead of pytest `tmp_path`, avoiding restricted temp-directory permissions in this environment.
- Updated `.gitignore` for pytest/cache/runtime temp directories created during validation runs.
- Added generic input readers for JSON, TXT, CSV, M3U, and M3U8 in `ppb/input_readers.py`; non-JSON inputs are converted to canonical `physical_playlist_job.v1` raw jobs before validation.
- Updated `ppb/cli.py` with `--input-type auto|json|txt|csv|m3u|m3u8`, input type reporting, and normalization summary output for convenience formats.
- Added tests for canonical and legacy JSON, TXT path lists, CSV comma/semicolon parsing and metadata mapping, M3U/M3U8 parsing, EXTINF metadata, normalized `PlaylistJob` shape, producer independence, and no output/audio writes by readers.
- Reviewed and updated `README.md` to document supported input types, canonical JSON as the rich contract, generic input normalization, relative path behavior, and dry-run command examples.
- Fixed CSV delimiter fallback so single-column CSV files still parse predictably when `csv.Sniffer` cannot infer a delimiter.
- Added `ppb/planner.py` with dry-run operation planning dataclasses, source existence checks, output filename planning, duplicate destination detection, blocked-track skip records, and output path safety checks.
- Added `ppb/report.py` for serializing dry-run plans to JSON.
- Updated `ppb/cli.py` so `--dry-run` prints an operation plan and `--report [FILE]` can write a `dry_run_report.json`-style report without creating music/output files.
- Added dry-run planner and CLI report tests for copy/convert planning, missing sources, duplicate output filenames, blocked-track skips, unsafe output paths, source-directory output rejection, and report JSON generation.
- Extended dry-run output directory validation to reject invalid filesystem characters and reserved Windows device names before any output stage can run.
