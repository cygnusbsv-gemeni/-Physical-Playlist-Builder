"""Physical Playlist Builder CLI.

This stage validates neutral playlist input, prints a dry-run operation plan,
creates a safe output folder, copies planned source files into it, and then
generates an M3U8 playlist from successfully copied files. It does not convert,
normalize, or write tags.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ppb.copier import EXPORT_REPORT_FILENAME, CopyTrackResult, run_copy_stage
from ppb.filesystem import OutputFolderError, build_output_folder_target, create_output_folder
from ppb.input_readers import AUTO_INPUT_TYPE, InputReadError, read_playlist_input
from ppb.m3u import (
    DEFAULT_M3U_FILENAME,
    M3U_STATUS_FAILED,
    M3U_STATUS_GENERATED,
    generate_m3u8_playlist,
    validate_m3u_filename,
)
from ppb.planner import ACTION_CONVERT, ACTION_COPY, ACTION_ERROR, build_dry_run_plan
from ppb.report import update_export_session_copy_summary, write_dry_run_report, write_export_report


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
        help="Base output folder for exported playlist files.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Allow writing export_session.json into an existing non-empty output folder.",
    )
    parser.add_argument(
        "--create-subfolder",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Create a timestamped playlist subfolder under --out. Default: enabled.",
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
    parser.add_argument(
        "--report",
        nargs="?",
        const="dry_run_report.json",
        metavar="FILE",
        help="Write dry-run JSON report. Default when passed: dry_run_report.json.",
    )
    parser.add_argument(
        "--m3u-name",
        default=DEFAULT_M3U_FILENAME,
        metavar="FILE",
        help="Leaf filename for the generated M3U8 playlist. Default: playlist.m3u8.",
    )
    return parser


def print_job_summary(input_result, out_dir: Path, dry_run: bool, create_subfolder: bool, overwrite: bool) -> None:
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
    print(f"  Create subfolder: {'YES' if create_subfolder else 'NO'}")
    print(f"  Overwrite existing non-empty output: {'YES' if overwrite else 'NO'}")
    print(
        "  Dry-run mode: "
        + (
            "YES - no files will be created"
            if dry_run
            else "NO - output folder, export_session.json, copies, playlist.m3u8, "
            "and export_report.json may be created"
        )
    )
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


def print_dry_run_plan(plan) -> None:
    copy_count = sum(1 for operation in plan.operations if operation.planned_action == ACTION_COPY)
    convert_count = sum(
        1 for operation in plan.operations if operation.planned_action == ACTION_CONVERT
    )
    error_count = sum(1 for operation in plan.operations if operation.planned_action == ACTION_ERROR)

    print("=" * 52)
    print("  Dry-Run Operation Plan")
    print("=" * 52)
    print(f"  Output folder valid: {'YES' if plan.output_dir_valid else 'NO'}")
    print(f"  Output folder exists: {'YES' if plan.output_dir_exists else 'NO'}")
    print(
        "  Output overwrites source dir: "
        + ("YES" if plan.output_dir_overwrites_source_dir else "NO")
    )
    print(
        "  Output inside source dir: "
        + ("YES" if plan.output_dir_inside_source_dir else "NO")
    )
    print(f"  Planned copies: {copy_count}")
    print(f"  Planned conversions: {convert_count}")
    print(f"  Blocked tracks: {len(plan.blocked_tracks)}")
    print(f"  Operations with errors: {error_count}")
    print(f"  Duplicate output filenames: {len(plan.duplicate_output_filenames)}")
    print(f"  Safe for next output-folder stage: {len(plan.safe_operations)}")
    print("=" * 52)

    if plan.errors:
        print()
        print("  Global errors:")
        for error in plan.errors:
            print(f"  [ERROR] {error}")

    if plan.duplicate_output_filenames:
        print()
        print("  Duplicate output filenames:")
        for filename in plan.duplicate_output_filenames:
            print(f"  [CONFLICT] {filename}")

    if plan.blocked_tracks:
        print()
        print("  Blocked tracks:")
        for operation in plan.blocked_tracks:
            print(f"  [SKIP] track {operation.position}: {operation.source_path}")
            for error in operation.errors:
                print(f"    - {error}")

    problem_operations = [
        operation
        for operation in plan.operations
        if operation.planned_action == ACTION_ERROR or operation.warnings
    ]
    if problem_operations:
        print()
        print("  Track details:")
        for operation in problem_operations:
            print(
                f"  [{operation.planned_action.upper()}] track {operation.position}: "
                f"{operation.expected_output_filename or '(no output)'}"
            )
            print(f"    source_exists: {'YES' if operation.source_exists else 'NO'}")
            if operation.destination_path:
                print(f"    destination: {operation.destination_path}")
            for warning in operation.warnings:
                print(f"    [warning] {warning}")
            for error in operation.errors:
                print(f"    [error] {error}")

    print()
    print("[handoff] Safe next-stage operations are copy/convert records with existing sources,")
    print("          no duplicate destination filename, no path errors, and no global errors.")
    print()


def print_copy_progress(index: int, total: int, result: CopyTrackResult) -> None:
    filename = result.expected_output_filename or Path(result.source_path).name or "(no filename)"
    print(f"[copy] {index}/{total} {result.status}: {filename}")


def print_copy_summary(copy_result) -> None:
    summary = copy_result.summary
    print("=" * 52)
    print("  Copy Stage Summary")
    print("=" * 52)
    print(f"  Tracks processed: {summary['total']}")
    print(f"  Copied: {summary['copied']}")
    print(f"  Skipped: {summary['skipped']}")
    print(f"  Source missing: {summary['source_missing']}")
    print(f"  Destination exists: {summary['destination_exists']}")
    print(f"  Not implemented: {summary['not_implemented']}")
    print(f"  Failed: {summary['failed']}")
    print("=" * 52)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.report and not args.dry_run:
        parser.error("--report requires --dry-run")
    try:
        m3u_name = validate_m3u_filename(args.m3u_name)
    except ValueError as exc:
        parser.error(str(exc))

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

    if result.fatal_errors:
        print_job_summary(input_result, Path(args.out), args.dry_run, args.create_subfolder, args.overwrite)
        sys.exit(2)

    if not result.ok:
        print_job_summary(input_result, Path(args.out), args.dry_run, args.create_subfolder, args.overwrite)
        print(
            f"[strict] Validation failed: {result.blocked_count} blocked track(s). "
            "Run without --strict to allow blocked tracks to be skipped later.",
            file=sys.stderr,
        )
        sys.exit(3)

    try:
        target = build_output_folder_target(
            args.out,
            result.job.playlist_name,
            create_subfolder=args.create_subfolder,
        )
    except OutputFolderError as exc:
        print_job_summary(input_result, Path(args.out), args.dry_run, args.create_subfolder, args.overwrite)
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(4)

    print_job_summary(
        input_result,
        target.final_output_dir,
        args.dry_run,
        args.create_subfolder,
        args.overwrite,
    )
    plan = build_dry_run_plan(result.job, target.final_output_dir)

    if args.dry_run:
        print_dry_run_plan(plan)
        if args.report:
            report_path = write_dry_run_report(plan, args.report)
            print(f"[dry-run] Report written: {report_path}")
        print("[dry-run] No files were created or modified.")
    else:
        try:
            output_result = create_output_folder(
                job=result.job,
                plan=plan,
                target=target,
                overwrite=args.overwrite,
                input_path=input_result.input_path,
                input_type=input_result.input_type,
            )
        except (OSError, OutputFolderError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(4)
        print(f"[output] Folder ready: {output_result.final_output_dir}")
        print(f"[output] Export session written: {output_result.export_session_path}")
        copy_result = run_copy_stage(
            plan=plan,
            final_output_dir=output_result.final_output_dir,
            overwrite=args.overwrite,
            progress_callback=print_copy_progress,
        )
        report_path = Path(output_result.final_output_dir) / EXPORT_REPORT_FILENAME
        m3u_result = generate_m3u8_playlist(
            job=result.job,
            copy_result=copy_result,
            final_output_dir=output_result.final_output_dir,
            m3u_name=m3u_name,
        )
        try:
            write_export_report(copy_result, report_path, m3u_result=m3u_result)
            update_export_session_copy_summary(
                session_path=output_result.export_session_path,
                copy_result=copy_result,
                report_path=report_path,
            )
        except OSError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(4)
        print_copy_summary(copy_result)
        if m3u_result.status == M3U_STATUS_GENERATED:
            print(
                f"[output] M3U8 written: {m3u_result.m3u_path} "
                f"({m3u_result.track_count} track(s))"
            )
        elif m3u_result.status == M3U_STATUS_FAILED:
            for error in m3u_result.errors:
                print(f"[m3u] ERROR: {error}", file=sys.stderr)
        else:
            print("[output] M3U8 skipped by settings.generate_m3u8=false")
        print(f"[output] Export report written: {report_path}")
        if m3u_result.status == M3U_STATUS_FAILED:
            sys.exit(4)


if __name__ == "__main__":
    main()
