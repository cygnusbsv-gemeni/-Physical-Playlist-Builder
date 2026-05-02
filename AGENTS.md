# Physical Playlist Builder — Codex Project Instructions

This project is Physical Playlist Builder.

It is an independent Python CLI / desktop utility for creating physical playlist folders from generic playlist input.

## Project identity

Physical Playlist Builder must be a standalone file-processing tool.

It must not depend on:

- any external application
- any web framework
- any database
- application-specific routes
- application-specific table names
- internal modules from other projects
- internal data models from other projects

This utility must not know about MusicLib Web or any other specific application.

MusicLib Web may produce a neutral input file, but Physical Playlist Builder must treat it only as generic input.

## Input model

The main neutral input format is:

- physical_playlist_job.v1
- playlist_job.json

The utility must also be designed to support:

- TXT lists of paths
- CSV files
- M3U / M3U8 playlists
- folders
- manual file lists

Do not make the architecture depend on only one input source.

## File safety rules

Source audio files must never be modified.

The utility may only perform operations inside the selected output folder, including:

- copying files
- conversion
- loudness processing
- tag writing
- M3U8 generation
- report generation
- log generation

Tags may be written only to exported copies.

Never write tags to source files.

Never normalize, convert, rename, move, or delete source files.

## Output folder boundary

All generated files must be placed inside the selected output folder.

The selected output folder is the safety boundary for write operations.

The utility must avoid writing outside the output folder unless explicitly required by the user.

## Development process

Work stage by stage.

Do not implement multiple roadmap stages at once unless explicitly asked.

Each stage must produce a runnable checkpoint.

Prefer small, verifiable changes over large rewrites.

## Documentation requirements

After every completed stage, update:

1. README.md
2. PROJECT_FILES.md

README.md must describe:

- current real implemented state
- usage
- safety rules
- limitations
- next stage

PROJECT_FILES.md must list:

- project files
- file responsibilities
- files changed in the current stage
- reason for each changed file

Do not describe planned features as already working.

Mark unfinished features as:

- Planned
- Not implemented yet
- Limitation

## Testing expectations

At minimum, after each implementation stage, try to run:

- Python syntax check
- relevant unit tests, if present
- CLI help command, if available
- one small smoke test, if test data is available

Do not claim that conversion, loudness processing, tag writing, or playlist generation works unless it was actually implemented and tested.

## User-facing explanations

When reporting results to the user, write explanations in Russian.

CLI command names, file names, option names, logs, code identifiers, and technical constants may remain in English.