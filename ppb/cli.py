"""Physical Playlist Builder CLI.

This stage validates neutral playlist input and prints a dry-run summary. It
does not copy, convert, normalize, tag, create playlists, or create output
folders.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ppb.input_readers import AUTO_INPUT_TYPE, InputReadError, read_playlist_input


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ppb",
        description="Prepare a physical playlist plan from neutral playlist input.",
    )
    parser.add_argument(
        "--input",
        required=True,
        metavar="FILE",
        help="Path to JSON, TXT, CSV, M3U, or M3U8 playlist input.",
    )
    parser.add_argument(
        "--input-type",
        choices=[AUTO_INPUT_TYPE, "json", "txt", "csv", "m3u", "m3u8"],
        default=AUTO_INPUT_TYPE,
        help="Input type. Default: auto-detect from file extension.",
    )
    parser.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="Output folder planned for exported playlist files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate and summarize without creating or modifying files.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Fail if any tracks are blocked.",
    )
    return parser


def print_job_summary(input_result, out_dir: Path, dry_run: bool) -> None:
    result = input_result.validation
    print("=" * 52)
    print("  Physical Playlist Builder - Job Summary")
    print("=" * 52)
    print(f"  Input path: {input_result.input_path}")
    print(f"  Detected input type: {input_result.input_type}")

    if result.fatal_errors:
        for line in result.summary_lines():
            print(line)
        print("=" * 52)
        return

    job = result.job
    print(f"  Format: {job.format}")
    if input_result.converted:
        print("  Input normalized: converted into PlaylistJob structure")
    print(f"  Playlist name: {job.playlist_name}")
    print(f"  Track count: {len(job.tracks)}")
    print(f"  Blocked track count: {result.blocked_count}")
    print(f"  Warning count: {result.warning_count}")
    print(f"  Output folder: {out_dir}")
    print(f"  Dry-run mode: {'YES - no files will be created' if dry_run else 'NO - validation only in this stage'}")
    print(
        "  Strict mode: "
        + (
            "YES - blocked tracks cause failure"
            if result.strict
            else "NO - blocked tracks will be skipped later"
        )
    )
    print("=" * 52)

    if result.issues or result.global_warnings:
        print()
        print("  Validation issues:")
        for line in result.summary_lines():
            print(line)
    else:
        print("  All tracks passed validation.")

    if result.blocked_count and not result.strict:
        print()
        print(
            f"[info] Non-strict mode: {result.blocked_count} blocked track(s) "
            "will be skipped later."
        )

    print()


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        input_result = read_playlist_input(
            Path(args.input),
            input_type=args.input_type,
            strict=args.strict,
        )
    except InputReadError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(exc.exit_code)

    result = input_result.validation
    print_job_summary(input_result, Path(args.out), args.dry_run)

    if result.fatal_errors:
        sys.exit(2)

    if not result.ok:
        print(
            f"[strict] Validation failed: {result.blocked_count} blocked track(s). "
            "Run without --strict to allow blocked tracks to be skipped later.",
            file=sys.stderr,
        )
        sys.exit(3)

    if args.dry_run:
        print("[dry-run] No files were created or modified.")
    else:
        print("[info] Real execution is not implemented yet; validation only.")


if __name__ == "__main__":
    main()
