"""
Physical Playlist Builder — CLI entry point.
Stage U1: parse arguments, load JSON, print summary.
No file copying, no ffmpeg, no tag writing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ppb",
        description="Physical Playlist Builder — create a physical playlist folder from generic input.",
    )
    parser.add_argument(
        "--input",
        required=True,
        metavar="FILE",
        help="Path to playlist_job.json (physical_playlist_job.v1) or other supported input file.",
    )
    parser.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="Output folder where the physical playlist will be created.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print what would be done without creating or copying any files.",
    )
    return parser


def load_json_input(path: Path) -> dict:
    """Load and return a JSON file as a dict. Exits with a clear error on failure."""
    if not path.exists():
        print(f"ERROR: Input file not found: {path}", file=sys.stderr)
        sys.exit(1)
    if not path.is_file():
        print(f"ERROR: Input path is not a file: {path}", file=sys.stderr)
        sys.exit(1)
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        print(f"ERROR: Could not parse JSON — {exc}", file=sys.stderr)
        sys.exit(1)
    return data


def print_summary(data: dict, out_dir: Path, dry_run: bool) -> None:
    """Print a human-readable job summary to stdout."""
    schema = data.get("schema", "unknown")
    playlist_name = data.get("playlist_name") or data.get("name") or "(no name)"
    tracks: list = data.get("tracks", [])
    track_count = len(tracks)

    print("=" * 52)
    print("  Physical Playlist Builder — Job Summary")
    print("=" * 52)
    print(f"  Schema        : {schema}")
    print(f"  Playlist name : {playlist_name}")
    print(f"  Track count   : {track_count}")
    print(f"  Output folder : {out_dir}")
    print(f"  Dry-run mode  : {'YES — no files will be created' if dry_run else 'NO — real execution'}")
    print("=" * 52)

    if track_count == 0:
        print("  (!) No tracks found in input.")
    else:
        print(f"  First track   : {tracks[0]}" if isinstance(tracks[0], str) else f"  First track   : {tracks[0]}")

    print()


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    out_dir = Path(args.out)

    # Stage U1 only handles JSON input.
    # Future stages will detect and delegate to other readers.
    data = load_json_input(input_path)

    print_summary(data, out_dir, args.dry_run)

    if args.dry_run:
        print("[dry-run] No files were created or modified.")
    else:
        print("[info] Real execution is not implemented yet (Stage U1 skeleton).")


if __name__ == "__main__":
    main()
