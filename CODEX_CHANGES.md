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
