PROJECT_FILES.md is the current file map of the project.
It helps the next development session understand what files exist and what each file is responsible for.

# Project Files — Physical Playlist Builder

## Last updated

2025-01-01 (Stage U1)

## Core files

| File | Purpose | Last changed in stage |
|---|---|---|
| README.md | Project documentation, usage, safety rules, limitations, next stage | U1 |
| requirements.txt | Python dependencies (pytest only at U1; no third-party libs needed) | U1 |
| example_playlist_job.json | Example input file for manual testing and documentation | U1 |
| ppb/__init__.py | Package marker | U1 |
| ppb/cli.py | CLI entry point — parses args, loads JSON, prints summary | U1 |
| ppb/contract.py | Neutral input contract dataclasses: PlaylistJob, TrackEntry (stub — documented, not yet used by CLI) | U1 |
| ppb/input_readers.py | TXT / CSV / M3U / M3U8 input readers | Planned |
| ppb/validator.py | Input validation against PlaylistJob contract | Planned |
| ppb/planner.py | Dry-run and operation planning | Planned |
| ppb/filesystem.py | Safe output folder creation and path handling | Planned |
| ppb/copier.py | File copy logic (source files never modified) | Planned |
| ppb/m3u.py | M3U8 playlist generation into output folder | Planned |
| ppb/report.py | JSON/TXT execution reports into output folder | Planned |
| ppb/logging_setup.py | Logging configuration (log files inside output folder only) | Planned |
| ppb/ffmpeg_tools.py | ffmpeg conversion and loudness normalization on exported copies only | Planned |
| ppb/tags.py | Tag writing to exported copies only | Planned |

## Tests

| File | Purpose | Last changed in stage |
|---|---|---|
| tests/__init__.py | Test package marker | U1 |
| tests/test_cli_u1.py | Stage U1 smoke tests: valid JSON, missing file, malformed JSON, dry-run, no output files created | U1 |

## Notes

- Do not remove files from this map unless they are actually deleted.
- If a file is renamed, record the rename.
- If a file changes responsibility, update its description.
- Stub files exist for all planned modules — they contain only comments describing future responsibilities.
