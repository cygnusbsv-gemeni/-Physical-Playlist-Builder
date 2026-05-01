# Project Files — Physical Playlist Builder

## Last updated

2025-05-01

## Core files

| File | Purpose | Last changed in stage |
|---|---|---|
| README.md | Project documentation and usage instructions | U2 |
| requirements.txt | Python dependencies | U1 |
| ppb/__init__.py | Package marker | U1 |
| ppb/cli.py | CLI entry point — args, JSON load, validation, summary | U2 |
| ppb/contract.py | Neutral input contract dataclasses (TrackEntry, PlaylistJob) | U2 |
| ppb/validator.py | Validation for physical_playlist_job.v1 — produces ValidationResult + normalized PlaylistJob | U2 |
| ppb/input_readers.py | TXT / CSV / M3U / M3U8 input readers | Planned |
| ppb/planner.py | Dry-run and operation planning | Planned |
| ppb/filesystem.py | Safe output folder and path handling | Planned |
| ppb/copier.py | File copy logic | Planned |
| ppb/m3u.py | M3U8 generation | Planned |
| ppb/report.py | JSON/TXT reports | Planned |
| ppb/logging_setup.py | Logging setup | Planned |
| ppb/ffmpeg_tools.py | ffmpeg conversion / loudness helpers | Planned |
| ppb/tags.py | Tag writing into exported copies | Planned |

## Tests

| File | Purpose | Last changed in stage |
|---|---|---|
| tests/__init__.py | Package marker | U1 |
| tests/test_cli_u1.py | Stage U1 CLI smoke tests | U1 |
| tests/test_validator_u2.py | Stage U2 validator tests (39 tests) | U2 |

## Notes

- Do not remove files from this map unless they are actually deleted.
- If a file is renamed, record the rename.
- If a file changes responsibility, update its description.
