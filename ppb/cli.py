"""Physical Playlist Builder CLI.

This stage validates neutral playlist input, prints a dry-run operation plan,
creates a safe output folder, copies or converts planned source files into it,
optionally normalizes loudness for those exported copies, and then generates an
M3U8 playlist from the final exported files. When requested by the job, it
writes normalized tags only to final exported copies before reports and M3U8.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ppb.copier import (
    EXPORT_REPORT_FILENAME,
    STATUS_CONVERTED,
    STATUS_COPIED,
    STATUS_FFMPEG_MISSING,
    STATUS_RESUMED,
    CopyTrackResult,
    run_copy_stage,
)
from ppb.ffmpeg_tools import (
    DEFAULT_LOUDNESS_RANGE_LUFS,
    STATUS_FFMPEG_UNAVAILABLE as FFMPEG_STATUS_UNAVAILABLE,
    measure_loudness_first_pass,
    normalize_loudness_second_pass,
)
from ppb.filesystem import (
    OutputFolderError,
    OutputFolderResult,
    build_output_folder_target,
    create_output_folder,
)
from ppb.input_readers import AUTO_INPUT_TYPE, InputReadError, read_playlist_input
from ppb.logging_setup import close_export_logger, setup_export_logger
from ppb.m3u import (
    DEFAULT_M3U_FILENAME,
    M3U_STATUS_FAILED,
    M3U_STATUS_GENERATED,
    generate_m3u8_playlist,
    validate_m3u_filename,
)
from ppb.planner import ACTION_CONVERT, ACTION_COPY, ACTION_ERROR, build_dry_run_plan
from ppb.report import (
    EXPORT_REPORT_TEXT_FILENAME,
    LOUDNESS_STATUS_FAILED,
    LOUDNESS_STATUS_FFMPEG_MISSING,
    LOUDNESS_STATUS_MEASURED,
    LOUDNESS_STATUS_NORMALIZED,
    LOUDNESS_STATUS_SKIPPED,
    update_export_session_copy_summary,
    write_dry_run_report,
    write_export_report,
    write_export_report_text,
    write_export_session,
    TAG_STATUS_FAILED,
    TAG_STATUS_SKIPPED,
    TAG_STATUS_UNSUPPORTED_FORMAT,
    TAG_STATUS_WRITTEN,
)
from ppb.resume import ResumeState, build_resume_comparison, discover_resume_state
from ppb.tags import (
    ID3_VERSION_V23,
    ID3_VERSION_V24,
    STATUS_NO_SUPPORTED_FIELDS as TAG_HELPER_STATUS_NO_SUPPORTED_FIELDS,
    STATUS_UNSUPPORTED_FORMAT as TAG_HELPER_STATUS_UNSUPPORTED_FORMAT,
    STATUS_WRITTEN as TAG_HELPER_STATUS_WRITTEN,
    write_tags_to_exported_file,
)


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
        "--resume",
        action="store_true",
        default=False,
        help=(
            "Load prior export_session.json/export_report.json from the selected final output "
            "folder and safely reuse validated candidates. Requires --no-create-subfolder."
        ),
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
    parser.add_argument(
        "--ffmpeg",
        metavar="FILE",
        help=(
            "Path to ffmpeg for planned convert operations. "
            "Defaults to ffmpeg discovered on PATH."
        ),
    )
    parser.add_argument(
        "--mp3-quality",
        type=int,
        choices=range(10),
        default=2,
        metavar="0-9",
        help=(
            "MP3 VBR quality for planned MP3 conversion. Default: 2."
        ),
    )
    parser.add_argument(
        "--audio-bitrate",
        metavar="BITRATE",
        help=(
            "Audio bitrate such as 192k for planned conversion."
        ),
    )
    parser.add_argument(
        "--skip-loudness",
        action="store_true",
        default=False,
        help=(
            "Skip loudness measurement and normalization during real export."
        ),
    )
    parser.add_argument(
        "--skip-loudness-verification",
        action="store_true",
        default=False,
        help=(
            "Skip post-normalization loudness verification during real export. "
            "Measurement and normalization still run when loudness processing is enabled."
        ),
    )
    parser.add_argument(
        "--skip-tags",
        action="store_true",
        default=False,
        help=(
            "Skip tag writing during real export, even when settings.write_tags is true."
        ),
    )
    parser.add_argument(
        "--id3-version",
        choices=[ID3_VERSION_V23, ID3_VERSION_V24],
        default=ID3_VERSION_V24,
        help="ID3 version for MP3 tag writing. Default: v24.",
    )
    return parser


def print_job_summary(
    input_result,
    out_dir: Path,
    dry_run: bool,
    create_subfolder: bool,
    overwrite: bool,
    resume: bool = False,
) -> None:
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
    if resume:
        if dry_run:
            print("  Resume requested: YES - dry-run reports candidates only")
        else:
            print("  Resume requested: YES - safe candidates may be reused after validation")
    print(
        "  Dry-run mode: "
        + (
            "YES - no files will be created"
            if dry_run
            else "NO - output folder, export_session.json, copies, conversions, "
            "playlist.m3u8, export_report.json, export_report.txt, and export.log may be created"
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


def print_resume_preflight(resume_state: ResumeState) -> None:
    print("=" * 52)
    print("  Resume Preflight")
    print("=" * 52)
    print(f"  Final output folder: {resume_state.final_output_dir}")
    print(f"  export_session.json found: {'YES' if resume_state.session_found else 'NO'}")
    print(f"  export_report.json found: {'YES' if resume_state.report_found else 'NO'}")
    print(f"  Prior state loaded: {'YES' if resume_state.state_found else 'NO'}")
    print("  B12.3 mode: safe candidates may be reused after execution-time validation.")
    print(f"  Warnings: {len(resume_state.warnings)}")
    print(f"  Errors: {len(resume_state.errors)}")
    print("=" * 52)
    for warning in resume_state.warnings:
        print(f"  [warning] {warning}")
    for error in resume_state.errors:
        print(f"  [error] {error}")
    print()


def print_resume_comparison(resume_state: ResumeState) -> None:
    comparison = resume_state.comparison or {}
    totals = comparison.get("totals") or {}
    print("=" * 52)
    print("  Resume Comparison")
    print("=" * 52)
    if comparison.get("applies_to_execution"):
        print("  Mode: safe candidates can skip processing after validation")
    else:
        print("  Mode: comparison-only for dry-run; no files will be skipped or reused")
    print(f"  Candidates total: {totals.get('candidates_total', 0)}")
    print(f"  Safe-to-reuse candidates: {totals.get('safe_to_reuse_candidates', 0)}")
    print(f"  Unsafe candidates: {totals.get('unsafe_candidates', 0)}")
    print(f"  Missing prior results: {totals.get('missing_prior_results', 0)}")
    print(f"  Existing output files: {totals.get('existing_output_files', 0)}")
    print(f"  Size matches: {totals.get('size_matches', 0)}")
    print(f"  Size mismatches: {totals.get('size_mismatches', 0)}")
    print("=" * 52)
    for warning in comparison.get("warnings") or []:
        print(f"  [warning] {warning}")
    print()


def print_copy_progress(index: int, total: int, result: CopyTrackResult) -> None:
    filename = result.expected_output_filename or Path(result.source_path).name or "(no filename)"
    print(f"[export] {index}/{total} {result.status}: {filename}", flush=True)


def _progress_filename(track_result) -> str:
    return (
        track_result.expected_output_filename
        or Path(track_result.destination_path or track_result.source_path).name
        or "(no filename)"
    )


def print_loudness_stage_start(
    *,
    should_process: bool,
    eligible_count: int,
    target_lufs: float | None,
    true_peak_db: float | None,
    skip_verification: bool,
    reason: str | None,
) -> None:
    if should_process:
        verification_note = "verification=skipped" if skip_verification else "verification=enabled"
        print(
            "[loudness] Starting: "
            f"eligible={eligible_count}, target={target_lufs} LUFS, "
            f"true_peak={true_peak_db} dBTP, {verification_note}",
            flush=True,
        )
        return
    print(f"[loudness] Skipped: {reason or 'no eligible tracks.'}", flush=True)


def print_loudness_step(index: int, total: int, step: str, track_result) -> None:
    print(
        f"[loudness] {index}/{total} {step}: {_progress_filename(track_result)}",
        flush=True,
    )


def print_loudness_done(index: int, total: int, track_result, loudness_result: dict[str, Any]) -> None:
    print(
        "[loudness] "
        f"{index}/{total} done: {_progress_filename(track_result)} | "
        f"measure={loudness_result.get('loudness_status')} "
        f"normalize={loudness_result.get('loudness_normalization_status')} "
        f"verify={loudness_result.get('post_loudness_status')} "
        f"input_i={loudness_result.get('input_i')} "
        f"post_i={loudness_result.get('post_input_i')}",
        flush=True,
    )


def print_tag_step(index: int, total: int, step: str, track_result) -> None:
    print(f"[tags] {index}/{total} {step}: {_progress_filename(track_result)}", flush=True)


def print_copy_summary(copy_result) -> None:
    summary = copy_result.summary
    print("=" * 52)
    print("  Export Stage Summary")
    print("=" * 52)
    print(f"  Tracks processed: {summary['total']}")
    print(f"  Copied: {summary['copied']}")
    print(f"  Converted: {summary['converted']}")
    print(f"  Resumed: {summary[STATUS_RESUMED]}")
    print(f"  Skipped: {summary['skipped']}")
    print(f"  Source missing: {summary['source_missing']}")
    print(f"  Destination exists: {summary['destination_exists']}")
    print(f"  FFmpeg missing: {summary['ffmpeg_missing']}")
    print(f"  Failed: {summary['failed']}")
    print("=" * 52)


def print_loudness_summary(loudness_summary: dict[str, Any]) -> None:
    totals = loudness_summary.get("totals", {})
    print(
        "[output] Loudness processing: "
        f"measured={totals.get(LOUDNESS_STATUS_MEASURED, 0)} "
        f"normalized={totals.get(LOUDNESS_STATUS_NORMALIZED, 0)} "
        f"skipped={totals.get(LOUDNESS_STATUS_SKIPPED, 0)} "
        f"failed={totals.get(LOUDNESS_STATUS_FAILED, 0)} "
        f"ffmpeg_missing={totals.get(LOUDNESS_STATUS_FFMPEG_MISSING, 0)}"
    )


def print_tag_summary(tag_summary: dict[str, Any]) -> None:
    totals = tag_summary.get("totals", {})
    print(
        "[output] Tag writing: "
        f"written={totals.get(TAG_STATUS_WRITTEN, 0)} "
        f"skipped={totals.get(TAG_STATUS_SKIPPED, 0)} "
        f"failed={totals.get(TAG_STATUS_FAILED, 0)} "
        f"unsupported_format={totals.get(TAG_STATUS_UNSUPPORTED_FORMAT, 0)}"
    )


def print_final_export_summary(
    *,
    final_output_dir: Path | str,
    copy_result,
    m3u_result,
    report_path: Path | str,
    report_txt_path: Path | str,
    log_path: Path | str,
    resume_state: ResumeState | None = None,
) -> None:
    summary = copy_result.summary
    print("=" * 52)
    print("  Final Export Summary")
    print("=" * 52)
    print(f"  Final output folder: {final_output_dir}")
    print(f"  Copied: {summary['copied']}")
    print(f"  Converted: {summary['converted']}")
    print(f"  Resumed: {summary[STATUS_RESUMED]}")
    print(f"  Skipped: {summary['skipped']}")
    print(f"  Failed: {summary['failed']}")
    print(f"  Missing: {summary['source_missing']}")
    print(f"  Conflict: {summary['destination_exists']}")
    print(f"  FFmpeg missing: {summary['ffmpeg_missing']}")
    print(f"  M3U8 status: {m3u_result.status}")
    print(f"  M3U8 path: {m3u_result.m3u_path or '(none)'}")
    print(f"  export_report.json: {report_path}")
    print(f"  export_report.txt: {report_txt_path}")
    print(f"  export.log: {log_path}")
    if resume_state is not None:
        print(
            "  Resume preflight: "
            f"state_found={'YES' if resume_state.state_found else 'NO'} "
            f"warnings={len(resume_state.warnings)} "
            f"errors={len(resume_state.errors)}"
        )
        comparison_totals = (resume_state.comparison or {}).get("totals") or {}
        print(
            "  Resume comparison: "
            f"safe={comparison_totals.get('safe_to_reuse_candidates', 0)} "
            f"unsafe={comparison_totals.get('unsafe_candidates', 0)} "
            f"reused={summary.get(STATUS_RESUMED, 0)}"
        )
    print("=" * 52)


def log_validation_details(logger, validation_result) -> None:
    for warning in validation_result.global_warnings:
        logger.warning("validation warning: %s", warning)
    for issue in validation_result.issues:
        if issue.level == "warning":
            logger.warning("validation warning: %s", issue)
        else:
            logger.error("validation blocker: %s", issue)


def log_copy_details(logger, copy_result) -> None:
    for result in copy_result.results:
        if result.status == STATUS_COPIED:
            logger.info("track %s copied: %s", result.position, result.destination_path)
        elif result.status == STATUS_CONVERTED:
            logger.info(
                "track %s converted to %s: %s",
                result.position,
                result.target_format or "(unknown)",
                result.destination_path,
            )
        elif result.status == STATUS_RESUMED:
            logger.info(
                "track %s resumed: %s (%s)",
                result.position,
                result.destination_path,
                result.resume_reason or "safe resume candidate reused.",
            )
        elif result.status == STATUS_FFMPEG_MISSING:
            logger.error("track %s ffmpeg missing: %s", result.position, result.destination_path)
        if result.resume_reason and result.status != STATUS_RESUMED:
            logger.info(
                "track %s resume not reused: %s",
                result.position,
                result.resume_reason,
            )
        for warning in result.warnings:
            logger.warning("track %s warning: %s", result.position, warning)
        for error in result.errors:
            logger.error("track %s error: %s", result.position, error)
        if result.ffmpeg_stderr_summary:
            log_method = logger.error if result.status != STATUS_CONVERTED else logger.info
            log_method(
                "track %s ffmpeg stderr summary: %s",
                result.position,
                result.ffmpeg_stderr_summary,
            )


def log_m3u_details(logger, m3u_result) -> None:
    for warning in m3u_result.warnings:
        logger.warning("m3u warning: %s", warning)
    for error in m3u_result.errors:
        logger.error("m3u error: %s", error)


def log_resume_preflight(logger, resume_state: ResumeState) -> None:
    logger.info("resume requested: safe reuse enabled")
    logger.info(
        "resume preflight files: session_found=%s report_found=%s state_found=%s",
        resume_state.session_found,
        resume_state.report_found,
        resume_state.state_found,
    )
    logger.info(
        "prior export_session.json %s: %s",
        "found" if resume_state.session_found else "missing",
        resume_state.session_path,
    )
    logger.info(
        "prior export_report.json %s: %s",
        "found" if resume_state.report_found else "missing",
        resume_state.report_path,
    )
    for warning in resume_state.warnings:
        logger.warning("resume preflight warning: %s", warning)
    for error in resume_state.errors:
        logger.error("resume preflight error: %s", error)


def log_resume_comparison(logger, resume_state: ResumeState) -> None:
    comparison = resume_state.comparison or {}
    totals = comparison.get("totals") or {}
    logger.info("resume comparison started: mode=safe_reuse_candidates")
    logger.info("resume comparison totals: %s", totals)

    unsafe_reasons: dict[str, int] = {}
    for candidate in comparison.get("candidates") or []:
        if candidate.get("safe_to_reuse_candidate"):
            continue
        reason = str(candidate.get("reason") or "unknown unsafe reason.")
        unsafe_reasons[reason] = unsafe_reasons.get(reason, 0) + 1
    if not unsafe_reasons:
        logger.info("resume comparison unsafe reasons: none")
    for reason, count in sorted(unsafe_reasons.items(), key=lambda item: (-item[1], item[0])):
        logger.warning("resume comparison unsafe reason (%s): %s", count, reason)

    for warning in comparison.get("warnings") or []:
        logger.warning("resume comparison warning: %s", warning)


def log_resume_execution_started(logger, resume_state: ResumeState) -> None:
    totals = (resume_state.comparison or {}).get("totals") or {}
    logger.info(
        "resume execution started: safe_candidates=%s unsafe_candidates=%s",
        totals.get("safe_to_reuse_candidates", 0),
        totals.get("unsafe_candidates", 0),
    )


def log_resume_execution_completed(logger, copy_result, resume_state: ResumeState) -> None:
    summary = copy_result.summary
    comparison_totals = (resume_state.comparison or {}).get("totals") or {}
    logger.info(
        "resume execution completed: resumed=%s skipped_processing=%s unsafe_candidates=%s",
        summary.get(STATUS_RESUMED, 0),
        summary.get("resume_reuse_skipped_processing", 0),
        comparison_totals.get("unsafe_candidates", 0),
    )


def prepare_resume_output_folder(
    *,
    job,
    plan,
    target,
    overwrite: bool,
    input_path: Path | str,
    input_type: str,
) -> OutputFolderResult:
    """Write a fresh session file for an explicit existing resume folder."""

    if plan.errors:
        raise OutputFolderError("; ".join(plan.errors))

    output_dir = Path(plan.output_dir).resolve(strict=False)
    if not output_dir.is_dir():
        raise OutputFolderError(f"Resume output path is not a directory: {output_dir}")

    session_path = output_dir / "export_session.json"
    write_export_session(
        job=job,
        plan=plan,
        session_path=session_path,
        requested_out=target.requested_out,
        create_subfolder=target.create_subfolder,
        overwrite=overwrite,
        input_path=input_path,
        input_type=input_type,
    )
    return OutputFolderResult(
        requested_out=target.requested_out,
        final_output_dir=str(output_dir),
        export_session_path=str(session_path),
        created_output_dir=False,
        existing_non_empty_allowed=True,
    )


def run_tag_writing_stage(
    *,
    job,
    copy_result,
    final_output_dir: Path | str,
    skip_tags: bool,
    id3_version: str,
    logger,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output_dir = Path(final_output_dir).resolve(strict=False)
    write_tags_requested = bool(job.settings.write_tags)
    eligible_count = sum(
        1
        for result in copy_result.results
        if result.status in {STATUS_COPIED, STATUS_CONVERTED}
    )
    should_write = write_tags_requested and not skip_tags
    reason = _tag_skip_reason(
        write_tags_requested=write_tags_requested,
        skip_tags=skip_tags,
        eligible_count=eligible_count,
    )
    results: list[dict[str, Any]] = []

    logger.info(
        "tag writing started: enabled=%s requested=%s skip_tags=%s eligible=%s id3_version=%s reason=%s",
        should_write,
        write_tags_requested,
        skip_tags,
        eligible_count,
        id3_version,
        reason or "(none)",
    )
    if should_write:
        print(f"[tags] Starting: eligible={eligible_count}, id3_version={id3_version}", flush=True)
    else:
        print(f"[tags] Skipped: {reason or 'tag writing not enabled.'}", flush=True)

    eligible_index = 0
    for index, track_result in enumerate(copy_result.results):
        track = job.tracks[index] if index < len(job.tracks) else None
        if track_result.status == STATUS_RESUMED:
            tag_result = _tag_skipped_result(
                "track was reused by resume; tag writing was skipped to avoid modifying it."
            )
            results.append(tag_result)
            _log_tag_track_result(logger, track_result, tag_result)
            continue
        if track_result.status not in {STATUS_COPIED, STATUS_CONVERTED}:
            tag_result = _tag_skipped_result(
                "track was not successfully exported; tag writing was skipped."
            )
            results.append(tag_result)
            _log_tag_track_result(logger, track_result, tag_result)
            continue

        if not should_write:
            tag_result = _tag_skipped_result(reason or "tag writing skipped.")
            results.append(tag_result)
            _log_tag_track_result(logger, track_result, tag_result)
            continue

        destination_error = _validate_tag_destination(track_result.destination_path, output_dir)
        if destination_error is not None:
            tag_result = _tag_failed_result(destination_error)
            results.append(tag_result)
            _log_tag_track_result(logger, track_result, tag_result)
            continue

        eligible_index += 1
        print_tag_step(eligible_index, eligible_count, "writing", track_result)
        metadata = _track_tag_metadata(track)
        helper_result = write_tags_to_exported_file(
            file_path=Path(track_result.destination_path).resolve(strict=False),
            final_output_dir=output_dir,
            metadata=metadata,
            id3_version=id3_version,
        )
        tag_result = _tag_helper_result_to_report(helper_result)
        results.append(tag_result)
        _log_tag_track_result(logger, track_result, tag_result)
        print_tag_step(eligible_index, eligible_count, str(tag_result.get("tag_status") or "done"), track_result)

    totals = _count_tag_results(results)
    summary = {
        "requested": write_tags_requested,
        "skip_tags": skip_tags,
        "enabled": should_write,
        "id3_version": id3_version,
        "eligible_track_count": eligible_count,
        "status": _tag_stage_status(
            should_write=should_write,
            eligible_count=eligible_count,
            totals=totals,
        ),
        "reason": reason,
        "totals": totals,
    }
    logger.info(
        "tag writing completed: written=%s skipped=%s failed=%s unsupported_format=%s",
        totals.get(TAG_STATUS_WRITTEN, 0),
        totals.get(TAG_STATUS_SKIPPED, 0),
        totals.get(TAG_STATUS_FAILED, 0),
        totals.get(TAG_STATUS_UNSUPPORTED_FORMAT, 0),
    )
    return results, summary


def run_loudness_measurement_stage(
    *,
    copy_result,
    final_output_dir: Path | str,
    settings,
    skip_loudness: bool,
    skip_loudness_verification: bool,
    ffmpeg_path: Path | str | None,
    mp3_quality: int,
    audio_bitrate: str | None,
    logger,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output_dir = Path(final_output_dir).resolve(strict=False)
    normalize_loudness = bool(settings.normalize_loudness)
    target_lufs = settings.target_lufs
    true_peak_db = settings.true_peak_db
    loudness_range_lufs = getattr(settings, "loudness_range_lufs", DEFAULT_LOUDNESS_RANGE_LUFS)
    eligible_count = sum(
        1
        for result in copy_result.results
        if result.status in {STATUS_COPIED, STATUS_CONVERTED}
    )
    should_process = normalize_loudness and not skip_loudness
    reason = _loudness_skip_reason(
        normalize_loudness=normalize_loudness,
        skip_loudness=skip_loudness,
        eligible_count=eligible_count,
    )
    results: list[dict[str, Any]] = []

    logger.info(
        "loudness measurement started: enabled=%s eligible=%s target_lufs=%s true_peak_db=%s reason=%s",
        should_process,
        eligible_count,
        target_lufs,
        true_peak_db,
        reason or "(none)",
    )
    logger.info(
        "loudness normalization started: enabled=%s eligible=%s target_lufs=%s true_peak_db=%s lra=%s reason=%s",
        should_process,
        eligible_count,
        target_lufs,
        true_peak_db,
        loudness_range_lufs,
        reason or "(none)",
    )
    logger.info(
        "loudness verification mode: enabled=%s skip_loudness_verification=%s",
        should_process and not skip_loudness_verification,
        skip_loudness_verification,
    )
    print_loudness_stage_start(
        should_process=should_process,
        eligible_count=eligible_count,
        target_lufs=target_lufs,
        true_peak_db=true_peak_db,
        skip_verification=skip_loudness_verification,
        reason=reason,
    )

    eligible_index = 0
    for track_result in copy_result.results:
        if track_result.status == STATUS_RESUMED:
            skip_result = _loudness_skipped_result(
                "track was reused by resume; loudness processing was skipped to avoid modifying it.",
                measured_path=track_result.destination_path,
            )
            results.append(skip_result)
            logger.info(
                "track %s loudness skipped: %s",
                track_result.position,
                skip_result["loudness_skip_reason"],
            )
            logger.info(
                "track %s loudness normalization skipped: %s",
                track_result.position,
                skip_result["loudness_normalization_skip_reason"],
            )
            continue
        if track_result.status not in {STATUS_COPIED, STATUS_CONVERTED}:
            skip_result = _loudness_skipped_result(
                "track was not successfully exported; loudness processing was skipped."
            )
            results.append(skip_result)
            logger.info(
                "track %s loudness skipped: %s",
                track_result.position,
                skip_result["loudness_skip_reason"],
            )
            logger.info(
                "track %s loudness normalization skipped: %s",
                track_result.position,
                skip_result["loudness_normalization_skip_reason"],
            )
            continue

        if not should_process:
            skip_result = _loudness_skipped_result(
                reason or "loudness processing skipped.",
                measured_path=track_result.destination_path,
            )
            results.append(skip_result)
            logger.info(
                "track %s loudness skipped: %s",
                track_result.position,
                skip_result["loudness_skip_reason"],
            )
            logger.info(
                "track %s loudness normalization skipped: %s",
                track_result.position,
                skip_result["loudness_normalization_skip_reason"],
            )
            continue

        destination_error = _validate_loudness_destination(
            track_result.destination_path,
            output_dir,
        )
        if destination_error is not None:
            loudness_result = _loudness_failed_result(
                destination_error,
                measured_path=track_result.destination_path,
                normalization_error=destination_error,
            )
            results.append(loudness_result)
            logger.error(
                "track %s loudness failed: %s",
                track_result.position,
                destination_error,
            )
            logger.error(
                "track %s loudness normalization failed: %s",
                track_result.position,
                destination_error,
            )
            continue

        eligible_index += 1
        destination_path = str(Path(track_result.destination_path).resolve(strict=False))
        size_after_export_before_loudness = (
            Path(destination_path).stat().st_size if Path(destination_path).is_file() else None
        )
        print_loudness_step(eligible_index, eligible_count, "measuring", track_result)
        measurement = measure_loudness_first_pass(
            source_path=destination_path,
            ffmpeg=ffmpeg_path,
            target_lufs=target_lufs,
            true_peak_db=true_peak_db,
            loudness_range_lufs=loudness_range_lufs,
        )
        loudness_result = _loudness_measurement_to_report(
            measurement,
            measured_path=destination_path,
            size_after_export_before_loudness=size_after_export_before_loudness,
        )
        if measurement.success:
            print_loudness_step(eligible_index, eligible_count, "normalizing", track_result)
            normalization = normalize_loudness_second_pass(
                exported_path=destination_path,
                output_folder=output_dir,
                measured_input_i=measurement.input_i,
                measured_input_tp=measurement.input_tp,
                measured_input_lra=measurement.input_lra,
                measured_input_thresh=measurement.input_thresh,
                measured_target_offset=measurement.target_offset,
                ffmpeg=measurement.ffmpeg or ffmpeg_path,
                target_lufs=target_lufs,
                true_peak_db=true_peak_db,
                loudness_range_lufs=loudness_range_lufs,
                mp3_quality=mp3_quality,
                audio_bitrate=audio_bitrate,
            )
            loudness_result.update(_loudness_normalization_to_report(normalization))
            if normalization.success and normalization.output_path:
                final_path = Path(normalization.output_path).resolve(strict=False)
                track_result.destination_path = str(final_path)
                track_result.destination_size = final_path.stat().st_size if final_path.is_file() else None
                if skip_loudness_verification:
                    loudness_result.update(
                        _post_loudness_skipped_result(
                            "--skip-loudness-verification was passed.",
                            measured_path=str(final_path),
                        )
                    )
                    logger.info(
                        "track %s post-normalization loudness verification skipped by CLI option",
                        track_result.position,
                    )
                else:
                    print_loudness_step(eligible_index, eligible_count, "verifying", track_result)
                    loudness_result.update(
                        _post_loudness_verification_to_report(
                            measure_loudness_first_pass(
                                source_path=str(final_path),
                                ffmpeg=(
                                    getattr(normalization, "ffmpeg", None)
                                    or measurement.ffmpeg
                                    or ffmpeg_path
                                ),
                                target_lufs=target_lufs,
                                true_peak_db=true_peak_db,
                                loudness_range_lufs=loudness_range_lufs,
                            ),
                            measured_path=str(final_path),
                        )
                    )
                loudness_result["size_after_loudness"] = track_result.destination_size
                loudness_result["final_size"] = track_result.destination_size
            else:
                loudness_result.update(
                    _post_loudness_skipped_result(
                        "loudness verification was skipped because normalization did not succeed."
                    )
                )
        else:
            loudness_result.update(_normalization_not_attempted_after_measurement_failure(measurement))
            loudness_result.update(
                _post_loudness_skipped_result(
                    "loudness verification was skipped because normalization did not succeed."
                )
            )
        results.append(loudness_result)
        _log_loudness_track_result(logger, track_result, loudness_result)
        _log_loudness_normalization_track_result(logger, track_result, loudness_result)
        _log_post_loudness_track_result(logger, track_result, loudness_result)
        print_loudness_done(eligible_index, eligible_count, track_result, loudness_result)

    totals = _count_loudness_results(results)
    measurement_totals = _count_loudness_measurement_results(results)
    normalization_totals = _count_loudness_normalization_results(results)
    verification_totals = _count_post_loudness_results(results)
    summary = {
        "status": _loudness_stage_status(
            should_process=should_process,
            eligible_count=eligible_count,
            totals=totals,
        ),
        "reason": reason,
        "normalize_loudness": normalize_loudness,
        "skip_loudness": skip_loudness,
        "skip_loudness_verification": skip_loudness_verification,
        "verification_enabled": should_process and not skip_loudness_verification,
        "target_lufs": target_lufs,
        "true_peak_db": true_peak_db,
        "loudness_range_lufs": loudness_range_lufs,
        "eligible_track_count": eligible_count,
        "totals": totals,
        "measurement_totals": measurement_totals,
        "normalization_totals": normalization_totals,
        "verification_totals": verification_totals,
    }
    logger.info(
        "loudness measurement completed: measured=%s skipped=%s failed=%s ffmpeg_missing=%s",
        measurement_totals.get(LOUDNESS_STATUS_MEASURED, 0),
        measurement_totals.get(LOUDNESS_STATUS_SKIPPED, 0),
        measurement_totals.get(LOUDNESS_STATUS_FAILED, 0),
        measurement_totals.get(LOUDNESS_STATUS_FFMPEG_MISSING, 0),
    )
    logger.info(
        "loudness normalization completed: normalized=%s skipped=%s failed=%s ffmpeg_missing=%s",
        normalization_totals.get(LOUDNESS_STATUS_NORMALIZED, 0),
        normalization_totals.get(LOUDNESS_STATUS_SKIPPED, 0),
        normalization_totals.get(LOUDNESS_STATUS_FAILED, 0),
        normalization_totals.get(LOUDNESS_STATUS_FFMPEG_MISSING, 0),
    )
    logger.info(
        "loudness verification completed: measured=%s skipped=%s failed=%s ffmpeg_missing=%s",
        verification_totals.get(LOUDNESS_STATUS_MEASURED, 0),
        verification_totals.get(LOUDNESS_STATUS_SKIPPED, 0),
        verification_totals.get(LOUDNESS_STATUS_FAILED, 0),
        verification_totals.get(LOUDNESS_STATUS_FFMPEG_MISSING, 0),
    )
    return results, summary


def _loudness_skip_reason(
    *,
    normalize_loudness: bool,
    skip_loudness: bool,
    eligible_count: int,
) -> str | None:
    if skip_loudness:
        return "--skip-loudness was passed."
    if not normalize_loudness:
        return "settings.normalize_loudness is false."
    if eligible_count == 0:
        return "no successfully exported files were eligible for loudness processing."
    return None


def _validate_loudness_destination(destination_path: str | None, output_dir: Path) -> str | None:
    if not destination_path:
        return "Successful export result has no destination path to measure."
    destination = Path(destination_path).resolve(strict=False)
    if destination == output_dir or not _is_relative_to(destination, output_dir):
        return f"Refused loudness measurement outside final output folder: {destination}"
    if not destination.is_file():
        return f"Exported destination file is missing during loudness measurement: {destination}"
    return None


def _loudness_skipped_result(
    reason: str,
    *,
    measured_path: str | None = None,
) -> dict[str, Any]:
    return {
        "loudness_status": LOUDNESS_STATUS_SKIPPED,
        "input_i": None,
        "input_tp": None,
        "input_lra": None,
        "input_thresh": None,
        "target_offset": None,
        "loudness_error": None,
        "loudness_stderr_summary": "",
        "loudness_skip_reason": reason,
        "loudness_measured_path": measured_path,
        "loudness_normalization_status": LOUDNESS_STATUS_SKIPPED,
        "normalized_output_path": None,
        "loudness_normalization_error": None,
        "loudness_normalization_stderr_summary": "",
        "loudness_normalization_skip_reason": reason,
        "loudness_normalization_return_code": None,
        "size_after_export_before_loudness": None,
        "size_after_loudness": None,
        "final_size": None,
        **_post_loudness_skipped_result(reason),
    }


def _loudness_failed_result(
    error: str,
    *,
    measured_path: str | None = None,
    stderr_summary: str = "",
    ffmpeg_missing: bool = False,
    return_code: int | None = None,
    normalization_error: str | None = None,
) -> dict[str, Any]:
    status = LOUDNESS_STATUS_FFMPEG_MISSING if ffmpeg_missing else LOUDNESS_STATUS_FAILED
    return {
        "loudness_status": status,
        "input_i": None,
        "input_tp": None,
        "input_lra": None,
        "input_thresh": None,
        "target_offset": None,
        "loudness_error": error,
        "loudness_stderr_summary": stderr_summary,
        "loudness_skip_reason": None,
        "loudness_measured_path": measured_path,
        "loudness_return_code": return_code,
        "loudness_normalization_status": status,
        "normalized_output_path": None,
        "loudness_normalization_error": normalization_error or error,
        "loudness_normalization_stderr_summary": stderr_summary,
        "loudness_normalization_skip_reason": None,
        "loudness_normalization_return_code": return_code,
        "size_after_export_before_loudness": None,
        "size_after_loudness": None,
        "final_size": None,
        **_post_loudness_skipped_result(
            "loudness verification was skipped because normalization did not succeed."
        ),
    }


def _loudness_measurement_to_report(
    measurement,
    *,
    measured_path: str,
    size_after_export_before_loudness: int | None = None,
) -> dict[str, Any]:
    if measurement.success:
        return {
            "loudness_status": LOUDNESS_STATUS_MEASURED,
            "input_i": measurement.input_i,
            "input_tp": measurement.input_tp,
            "input_lra": measurement.input_lra,
            "input_thresh": measurement.input_thresh,
            "target_offset": measurement.target_offset,
            "loudness_error": None,
            "loudness_stderr_summary": measurement.stderr_summary,
            "loudness_skip_reason": None,
            "loudness_measured_path": measured_path,
            "loudness_return_code": measurement.return_code,
            "loudness_normalization_status": LOUDNESS_STATUS_SKIPPED,
            "normalized_output_path": None,
            "loudness_normalization_error": None,
            "loudness_normalization_stderr_summary": "",
            "loudness_normalization_skip_reason": "loudness normalization was not evaluated.",
            "loudness_normalization_return_code": None,
            "size_after_export_before_loudness": size_after_export_before_loudness,
            "size_after_loudness": None,
            "final_size": size_after_export_before_loudness,
            **_post_loudness_skipped_result(
                "loudness verification was skipped because normalization was not evaluated."
            ),
        }

    error = "; ".join(measurement.errors) or measurement.stderr_summary or "loudness measurement failed."
    return _loudness_failed_result(
        error,
        measured_path=measured_path,
        stderr_summary=measurement.stderr_summary,
        ffmpeg_missing=measurement.status == FFMPEG_STATUS_UNAVAILABLE,
        return_code=measurement.return_code,
    )


def _loudness_normalization_to_report(normalization) -> dict[str, Any]:
    if normalization.success:
        return {
            "loudness_normalization_status": LOUDNESS_STATUS_NORMALIZED,
            "normalized_output_path": normalization.output_path,
            "loudness_normalization_error": None,
            "loudness_normalization_stderr_summary": normalization.stderr_summary,
            "loudness_normalization_skip_reason": None,
            "loudness_normalization_return_code": normalization.return_code,
        }

    error = (
        "; ".join(normalization.errors)
        or normalization.stderr_summary
        or "loudness normalization failed."
    )
    return {
        "loudness_normalization_status": (
            LOUDNESS_STATUS_FFMPEG_MISSING
            if normalization.status == FFMPEG_STATUS_UNAVAILABLE
            else LOUDNESS_STATUS_FAILED
        ),
        "normalized_output_path": None,
        "loudness_normalization_error": error,
        "loudness_normalization_stderr_summary": normalization.stderr_summary,
        "loudness_normalization_skip_reason": None,
        "loudness_normalization_return_code": normalization.return_code,
    }


def _normalization_not_attempted_after_measurement_failure(measurement) -> dict[str, Any]:
    error = (
        "; ".join(measurement.errors)
        or measurement.stderr_summary
        or "loudness measurement failed."
    )
    return {
        "loudness_normalization_status": (
            LOUDNESS_STATUS_FFMPEG_MISSING
            if measurement.status == FFMPEG_STATUS_UNAVAILABLE
            else LOUDNESS_STATUS_FAILED
        ),
        "normalized_output_path": None,
        "loudness_normalization_error": (
            "loudness normalization was not attempted because measurement failed: "
            f"{error}"
        ),
        "loudness_normalization_stderr_summary": measurement.stderr_summary,
        "loudness_normalization_skip_reason": None,
        "loudness_normalization_return_code": measurement.return_code,
    }


def _post_loudness_skipped_result(
    reason: str,
    *,
    measured_path: str | None = None,
) -> dict[str, Any]:
    return {
        "post_loudness_status": LOUDNESS_STATUS_SKIPPED,
        "post_input_i": None,
        "post_input_tp": None,
        "post_input_lra": None,
        "post_input_thresh": None,
        "post_target_offset": None,
        "post_loudness_error": None,
        "post_loudness_stderr_summary": "",
        "post_loudness_skip_reason": reason,
        "post_loudness_measured_path": measured_path,
        "post_loudness_return_code": None,
    }


def _post_loudness_verification_to_report(measurement, *, measured_path: str) -> dict[str, Any]:
    if measurement.success:
        return {
            "post_loudness_status": LOUDNESS_STATUS_MEASURED,
            "post_input_i": measurement.input_i,
            "post_input_tp": measurement.input_tp,
            "post_input_lra": measurement.input_lra,
            "post_input_thresh": measurement.input_thresh,
            "post_target_offset": measurement.target_offset,
            "post_loudness_error": None,
            "post_loudness_stderr_summary": measurement.stderr_summary,
            "post_loudness_skip_reason": None,
            "post_loudness_measured_path": measured_path,
            "post_loudness_return_code": measurement.return_code,
        }

    error = "; ".join(measurement.errors) or measurement.stderr_summary or "post-normalization loudness verification failed."
    return {
        "post_loudness_status": (
            LOUDNESS_STATUS_FFMPEG_MISSING
            if measurement.status == FFMPEG_STATUS_UNAVAILABLE
            else LOUDNESS_STATUS_FAILED
        ),
        "post_input_i": None,
        "post_input_tp": None,
        "post_input_lra": None,
        "post_input_thresh": None,
        "post_target_offset": None,
        "post_loudness_error": error,
        "post_loudness_stderr_summary": measurement.stderr_summary,
        "post_loudness_skip_reason": None,
        "post_loudness_measured_path": measured_path,
        "post_loudness_return_code": measurement.return_code,
    }


def _log_loudness_track_result(logger, track_result, loudness_result: dict[str, Any]) -> None:
    status = loudness_result.get("loudness_status")
    if status == LOUDNESS_STATUS_MEASURED:
        logger.info(
            "track %s loudness measured: input_i=%s input_tp=%s input_lra=%s",
            track_result.position,
            loudness_result.get("input_i"),
            loudness_result.get("input_tp"),
            loudness_result.get("input_lra"),
        )
        return

    message = loudness_result.get("loudness_error") or "loudness measurement failed."
    if status == LOUDNESS_STATUS_FFMPEG_MISSING:
        logger.error("track %s loudness ffmpeg missing: %s", track_result.position, message)
    elif status == LOUDNESS_STATUS_FAILED:
        logger.error("track %s loudness failed: %s", track_result.position, message)


def _log_loudness_normalization_track_result(
    logger,
    track_result,
    loudness_result: dict[str, Any],
) -> None:
    status = loudness_result.get("loudness_normalization_status")
    if status == LOUDNESS_STATUS_NORMALIZED:
        logger.info(
            "track %s loudness normalized: %s",
            track_result.position,
            loudness_result.get("normalized_output_path") or track_result.destination_path,
        )
        return
    if status == LOUDNESS_STATUS_SKIPPED:
        logger.info(
            "track %s loudness normalization skipped: %s",
            track_result.position,
            loudness_result.get("loudness_normalization_skip_reason")
            or "loudness normalization skipped.",
        )
        return

    message = (
        loudness_result.get("loudness_normalization_error")
        or loudness_result.get("loudness_normalization_stderr_summary")
        or "loudness normalization failed."
    )
    if status == LOUDNESS_STATUS_FFMPEG_MISSING:
        logger.error(
            "track %s loudness normalization ffmpeg missing: %s",
            track_result.position,
            message,
        )
    elif status == LOUDNESS_STATUS_FAILED:
        logger.error(
            "track %s loudness normalization failed: %s",
            track_result.position,
            message,
        )


def _log_post_loudness_track_result(logger, track_result, loudness_result: dict[str, Any]) -> None:
    status = loudness_result.get("post_loudness_status")
    if status == LOUDNESS_STATUS_MEASURED:
        logger.info(
            "track %s post-normalization loudness verified: input_i=%s input_tp=%s input_lra=%s",
            track_result.position,
            loudness_result.get("post_input_i"),
            loudness_result.get("post_input_tp"),
            loudness_result.get("post_input_lra"),
        )
        return
    if status == LOUDNESS_STATUS_SKIPPED:
        logger.info(
            "track %s post-normalization loudness verification skipped: %s",
            track_result.position,
            loudness_result.get("post_loudness_skip_reason")
            or "post-normalization loudness verification skipped.",
        )
        return

    message = (
        loudness_result.get("post_loudness_error")
        or loudness_result.get("post_loudness_stderr_summary")
        or "post-normalization loudness verification failed."
    )
    if status == LOUDNESS_STATUS_FFMPEG_MISSING:
        logger.error(
            "track %s post-normalization loudness verification ffmpeg missing: %s",
            track_result.position,
            message,
        )
    elif status == LOUDNESS_STATUS_FAILED:
        logger.error(
            "track %s post-normalization loudness verification failed: %s",
            track_result.position,
            message,
        )


def _count_loudness_results(results: list[dict[str, Any]]) -> dict[str, int]:
    totals = {
        LOUDNESS_STATUS_MEASURED: 0,
        LOUDNESS_STATUS_NORMALIZED: 0,
        LOUDNESS_STATUS_SKIPPED: 0,
        LOUDNESS_STATUS_FAILED: 0,
        LOUDNESS_STATUS_FFMPEG_MISSING: 0,
        "total": len(results),
    }
    for result in results:
        measurement_status = str(result.get("loudness_status") or LOUDNESS_STATUS_SKIPPED)
        normalization_status = str(
            result.get("loudness_normalization_status") or LOUDNESS_STATUS_SKIPPED
        )
        if measurement_status == LOUDNESS_STATUS_MEASURED:
            totals[LOUDNESS_STATUS_MEASURED] += 1

        if normalization_status == LOUDNESS_STATUS_NORMALIZED:
            totals[LOUDNESS_STATUS_NORMALIZED] += 1
        elif normalization_status == LOUDNESS_STATUS_FFMPEG_MISSING:
            totals[LOUDNESS_STATUS_FFMPEG_MISSING] += 1
        elif normalization_status == LOUDNESS_STATUS_FAILED:
            totals[LOUDNESS_STATUS_FAILED] += 1
        elif measurement_status == LOUDNESS_STATUS_FFMPEG_MISSING:
            totals[LOUDNESS_STATUS_FFMPEG_MISSING] += 1
        elif measurement_status == LOUDNESS_STATUS_FAILED:
            totals[LOUDNESS_STATUS_FAILED] += 1
        else:
            totals[LOUDNESS_STATUS_SKIPPED] += 1
    return totals


def _count_loudness_measurement_results(results: list[dict[str, Any]]) -> dict[str, int]:
    totals = {
        LOUDNESS_STATUS_MEASURED: 0,
        LOUDNESS_STATUS_SKIPPED: 0,
        LOUDNESS_STATUS_FAILED: 0,
        LOUDNESS_STATUS_FFMPEG_MISSING: 0,
        "total": len(results),
    }
    for result in results:
        status = str(result.get("loudness_status") or LOUDNESS_STATUS_SKIPPED)
        if status not in totals:
            status = LOUDNESS_STATUS_FAILED
        totals[status] += 1
    return totals


def _count_loudness_normalization_results(results: list[dict[str, Any]]) -> dict[str, int]:
    totals = {
        LOUDNESS_STATUS_NORMALIZED: 0,
        LOUDNESS_STATUS_SKIPPED: 0,
        LOUDNESS_STATUS_FAILED: 0,
        LOUDNESS_STATUS_FFMPEG_MISSING: 0,
        "total": len(results),
    }
    for result in results:
        status = str(result.get("loudness_normalization_status") or LOUDNESS_STATUS_SKIPPED)
        if status not in totals:
            status = LOUDNESS_STATUS_FAILED
        totals[status] += 1
    return totals


def _count_post_loudness_results(results: list[dict[str, Any]]) -> dict[str, int]:
    totals = {
        LOUDNESS_STATUS_MEASURED: 0,
        LOUDNESS_STATUS_SKIPPED: 0,
        LOUDNESS_STATUS_FAILED: 0,
        LOUDNESS_STATUS_FFMPEG_MISSING: 0,
        "total": len(results),
        "verified": 0,
        "verification_failed": 0,
    }
    for result in results:
        status = str(result.get("post_loudness_status") or LOUDNESS_STATUS_SKIPPED)
        if status not in {
            LOUDNESS_STATUS_MEASURED,
            LOUDNESS_STATUS_SKIPPED,
            LOUDNESS_STATUS_FAILED,
            LOUDNESS_STATUS_FFMPEG_MISSING,
        }:
            status = LOUDNESS_STATUS_FAILED
        totals[status] += 1
        if status == LOUDNESS_STATUS_MEASURED:
            totals["verified"] += 1
        elif status in {LOUDNESS_STATUS_FAILED, LOUDNESS_STATUS_FFMPEG_MISSING}:
            totals["verification_failed"] += 1
    return totals


def _loudness_stage_status(
    *,
    should_process: bool,
    eligible_count: int,
    totals: dict[str, int],
) -> str:
    if not should_process or eligible_count == 0:
        return LOUDNESS_STATUS_SKIPPED
    if totals.get(LOUDNESS_STATUS_FAILED, 0) or totals.get(LOUDNESS_STATUS_FFMPEG_MISSING, 0):
        if totals.get(LOUDNESS_STATUS_NORMALIZED, 0):
            return "completed_with_failures"
        return LOUDNESS_STATUS_FAILED
    if totals.get(LOUDNESS_STATUS_NORMALIZED, 0):
        return LOUDNESS_STATUS_NORMALIZED
    return LOUDNESS_STATUS_MEASURED


def _tag_skip_reason(
    *,
    write_tags_requested: bool,
    skip_tags: bool,
    eligible_count: int,
) -> str | None:
    if skip_tags:
        return "--skip-tags was passed."
    if not write_tags_requested:
        return "settings.write_tags is false or missing."
    if eligible_count == 0:
        return "no successfully exported files were eligible for tag writing."
    return None


def _validate_tag_destination(destination_path: str | None, output_dir: Path) -> str | None:
    if not destination_path:
        return "Successful export result has no destination path to tag."
    destination = Path(destination_path).resolve(strict=False)
    if destination == output_dir or not _is_relative_to(destination, output_dir):
        return f"Refused tag writing outside final output folder: {destination}"
    if not destination.is_file():
        return f"Exported destination file is missing during tag writing: {destination}"
    return None


def _track_tag_metadata(track) -> dict[str, Any]:
    if track is None:
        return {}
    fields = ("title", "artist", "album", "albumartist", "tracknumber", "date", "year", "genre")
    return {
        field_name: value
        for field_name in fields
        if (value := getattr(track, field_name, None)) is not None
    }


def _tag_skipped_result(reason: str, *, tag_format: str | None = None) -> dict[str, Any]:
    return {
        "tag_status": TAG_STATUS_SKIPPED,
        "tag_format": tag_format,
        "tag_written_fields": [],
        "tag_warnings": [reason],
        "tag_error": None,
    }


def _tag_failed_result(error: str, *, tag_format: str | None = None) -> dict[str, Any]:
    return {
        "tag_status": TAG_STATUS_FAILED,
        "tag_format": tag_format,
        "tag_written_fields": [],
        "tag_warnings": [],
        "tag_error": error,
    }


def _tag_helper_result_to_report(helper_result) -> dict[str, Any]:
    warnings = list(helper_result.warnings)
    error = helper_result.error

    if helper_result.success and helper_result.status == TAG_HELPER_STATUS_WRITTEN:
        status = TAG_STATUS_WRITTEN
        error = None
    elif helper_result.status == TAG_HELPER_STATUS_UNSUPPORTED_FORMAT:
        status = TAG_STATUS_UNSUPPORTED_FORMAT
    elif helper_result.status == TAG_HELPER_STATUS_NO_SUPPORTED_FIELDS:
        status = TAG_STATUS_SKIPPED
        if error:
            warnings.append(error)
        error = None
    else:
        status = TAG_STATUS_FAILED

    return {
        "tag_status": status,
        "tag_format": helper_result.tag_format,
        "tag_written_fields": list(helper_result.written_fields),
        "tag_warnings": warnings,
        "tag_error": error,
    }


def _log_tag_track_result(logger, track_result, tag_result: dict[str, Any]) -> None:
    status = tag_result.get("tag_status")
    if status == TAG_STATUS_WRITTEN:
        logger.info(
            "track %s tag written: format=%s fields=%s",
            track_result.position,
            tag_result.get("tag_format") or "(unknown)",
            ", ".join(tag_result.get("tag_written_fields") or []) or "(none)",
        )
        return
    if status == TAG_STATUS_SKIPPED:
        logger.info(
            "track %s tag skipped: %s",
            track_result.position,
            "; ".join(tag_result.get("tag_warnings") or []) or "tag writing skipped.",
        )
        return
    if status == TAG_STATUS_UNSUPPORTED_FORMAT:
        logger.info(
            "track %s tag skipped: unsupported_format: %s",
            track_result.position,
            tag_result.get("tag_error") or "unsupported tag-writing format.",
        )
        return

    logger.error(
        "track %s tag failed: %s",
        track_result.position,
        tag_result.get("tag_error") or "tag writing failed.",
    )


def _count_tag_results(results: list[dict[str, Any]]) -> dict[str, int]:
    totals = {
        TAG_STATUS_WRITTEN: 0,
        TAG_STATUS_SKIPPED: 0,
        TAG_STATUS_FAILED: 0,
        TAG_STATUS_UNSUPPORTED_FORMAT: 0,
        "total": len(results),
    }
    for result in results:
        status = str(result.get("tag_status") or TAG_STATUS_SKIPPED)
        if status not in totals:
            status = TAG_STATUS_FAILED
        totals[status] += 1
    return totals


def _tag_stage_status(
    *,
    should_write: bool,
    eligible_count: int,
    totals: dict[str, int],
) -> str:
    if not should_write or eligible_count == 0:
        return TAG_STATUS_SKIPPED
    if totals.get(TAG_STATUS_FAILED, 0):
        if totals.get(TAG_STATUS_WRITTEN, 0) or totals.get(TAG_STATUS_UNSUPPORTED_FORMAT, 0):
            return "completed_with_failures"
        return TAG_STATUS_FAILED
    if totals.get(TAG_STATUS_WRITTEN, 0):
        return TAG_STATUS_WRITTEN
    if totals.get(TAG_STATUS_UNSUPPORTED_FORMAT, 0):
        return TAG_STATUS_UNSUPPORTED_FORMAT
    return TAG_STATUS_SKIPPED


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    run_started_at = datetime.now(timezone.utc).isoformat()
    if args.report and not args.dry_run:
        parser.error("--report requires --dry-run")
    if args.resume and args.create_subfolder:
        parser.error(
            "--resume requires an explicit existing final output folder. "
            "Pass --no-create-subfolder and set --out to the prior export folder."
        )
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
        print_job_summary(
            input_result,
            Path(args.out),
            args.dry_run,
            args.create_subfolder,
            args.overwrite,
            args.resume,
        )
        sys.exit(2)

    if not result.ok:
        print_job_summary(
            input_result,
            Path(args.out),
            args.dry_run,
            args.create_subfolder,
            args.overwrite,
            args.resume,
        )
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
        print_job_summary(
            input_result,
            Path(args.out),
            args.dry_run,
            args.create_subfolder,
            args.overwrite,
            args.resume,
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(4)

    resume_state: ResumeState | None = None
    if args.resume:
        if not target.final_output_dir.exists():
            print_job_summary(
                input_result,
                target.final_output_dir,
                args.dry_run,
                args.create_subfolder,
                args.overwrite,
                args.resume,
            )
            print(
                "ERROR: --resume requires --out to point to an existing final output folder.",
                file=sys.stderr,
            )
            sys.exit(4)
        if not target.final_output_dir.is_dir():
            print_job_summary(
                input_result,
                target.final_output_dir,
                args.dry_run,
                args.create_subfolder,
                args.overwrite,
                args.resume,
            )
            print(
                "ERROR: --resume requires --out to point to a directory.",
                file=sys.stderr,
            )
            sys.exit(4)
        resume_state = discover_resume_state(target.final_output_dir)

    plan = build_dry_run_plan(result.job, target.final_output_dir)
    if resume_state is not None:
        build_resume_comparison(
            resume_state=resume_state,
            plan=plan,
            final_output_dir=target.final_output_dir,
            applies_to_execution=not args.dry_run,
        )

    print_job_summary(
        input_result,
        target.final_output_dir,
        args.dry_run,
        args.create_subfolder,
        args.overwrite,
        args.resume,
    )
    if resume_state is not None:
        print_resume_preflight(resume_state)
        print_resume_comparison(resume_state)

    if args.dry_run:
        print_dry_run_plan(plan)
        if args.report:
            report_path = write_dry_run_report(plan, args.report)
            print(f"[dry-run] Report written: {report_path}")
        print("[dry-run] No files were created or modified.")
    else:
        try:
            if resume_state is not None:
                output_result = prepare_resume_output_folder(
                    job=result.job,
                    plan=plan,
                    target=target,
                    overwrite=args.overwrite,
                    input_path=input_result.input_path,
                    input_type=input_result.input_type,
                )
            else:
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
        try:
            logger, log_path = setup_export_logger(output_result.final_output_dir)
        except OSError as exc:
            print(f"ERROR: could not write export.log: {exc}", file=sys.stderr)
            sys.exit(4)

        logger.info("validation completed")
        log_validation_details(logger, result)
        logger.info("output folder created: %s", output_result.final_output_dir)
        if resume_state is not None:
            log_resume_preflight(logger, resume_state)
            log_resume_comparison(logger, resume_state)
            log_resume_execution_started(logger, resume_state)
        print(f"[output] Folder ready: {output_result.final_output_dir}")
        print(f"[output] Export session written: {output_result.export_session_path}")
        copy_result = run_copy_stage(
            plan=plan,
            final_output_dir=output_result.final_output_dir,
            overwrite=args.overwrite,
            ffmpeg_path=args.ffmpeg,
            mp3_quality=args.mp3_quality,
            audio_bitrate=args.audio_bitrate,
            target_format=result.job.settings.output_format,
            resume_comparison=(
                resume_state.comparison if resume_state is not None else None
            ),
            progress_callback=print_copy_progress,
        )
        logger.info("export stage completed: %s", copy_result.summary)
        log_copy_details(logger, copy_result)
        if resume_state is not None:
            log_resume_execution_completed(logger, copy_result, resume_state)
        loudness_results, loudness_summary = run_loudness_measurement_stage(
            copy_result=copy_result,
            final_output_dir=output_result.final_output_dir,
            settings=result.job.settings,
            skip_loudness=args.skip_loudness,
            skip_loudness_verification=args.skip_loudness_verification,
            ffmpeg_path=args.ffmpeg,
            mp3_quality=args.mp3_quality,
            audio_bitrate=args.audio_bitrate,
            logger=logger,
        )
        tag_results, tag_summary = run_tag_writing_stage(
            job=result.job,
            copy_result=copy_result,
            final_output_dir=output_result.final_output_dir,
            skip_tags=args.skip_tags,
            id3_version=args.id3_version,
            logger=logger,
        )
        report_path = Path(output_result.final_output_dir) / EXPORT_REPORT_FILENAME
        report_txt_path = Path(output_result.final_output_dir) / EXPORT_REPORT_TEXT_FILENAME
        print("[m3u8] Generating playlist...", flush=True)
        m3u_result = generate_m3u8_playlist(
            job=result.job,
            copy_result=copy_result,
            final_output_dir=output_result.final_output_dir,
            m3u_name=m3u_name,
        )
        if m3u_result.status == M3U_STATUS_GENERATED:
            logger.info(
                "m3u8 generated: %s (%s track(s))",
                m3u_result.m3u_path,
                m3u_result.track_count,
            )
        elif m3u_result.status == M3U_STATUS_FAILED:
            logger.error("m3u8 failed: %s", "; ".join(m3u_result.errors))
        else:
            logger.info("m3u8 skipped")
        log_m3u_details(logger, m3u_result)
        try:
            run_finished_at = datetime.now(timezone.utc).isoformat()
            print("[report] Writing export reports...", flush=True)
            write_export_report(
                copy_result,
                report_path,
                m3u_result=m3u_result,
                loudness_results=loudness_results,
                loudness_summary=loudness_summary,
                started_at=run_started_at,
                finished_at=run_finished_at,
                input_path=input_result.input_path,
                final_output_dir=output_result.final_output_dir,
                playlist_name=result.job.playlist_name,
                report_txt_path=report_txt_path,
                log_path=log_path,
                write_tags_requested=result.job.settings.write_tags,
                tag_results=tag_results,
                tag_summary=tag_summary,
                resume_metadata=(
                    resume_state.to_report_metadata() if resume_state is not None else None
                ),
            )
            write_export_report_text(
                copy_result,
                report_txt_path,
                m3u_result=m3u_result,
                loudness_results=loudness_results,
                loudness_summary=loudness_summary,
                input_path=input_result.input_path,
                final_output_dir=output_result.final_output_dir,
                playlist_name=result.job.playlist_name,
                report_json_path=report_path,
                log_path=log_path,
                write_tags_requested=result.job.settings.write_tags,
                tag_results=tag_results,
                tag_summary=tag_summary,
                resume_metadata=(
                    resume_state.to_report_metadata() if resume_state is not None else None
                ),
            )
            update_export_session_copy_summary(
                session_path=output_result.export_session_path,
                copy_result=copy_result,
                report_path=report_path,
            )
        except OSError as exc:
            logger.error("report writing failed: %s", exc)
            close_export_logger(logger)
            print(f"ERROR: could not write export report/log files: {exc}", file=sys.stderr)
            sys.exit(4)
        logger.info(
            "reports written: json=%s text=%s log=%s",
            report_path,
            report_txt_path,
            log_path,
        )
        print_copy_summary(copy_result)
        print_loudness_summary(loudness_summary)
        print_tag_summary(tag_summary)
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
        print(f"[output] Human-readable report written: {report_txt_path}")
        print(f"[output] Export log written: {log_path}")
        print_final_export_summary(
            final_output_dir=output_result.final_output_dir,
            copy_result=copy_result,
            m3u_result=m3u_result,
            report_path=report_path,
            report_txt_path=report_txt_path,
            log_path=log_path,
            resume_state=resume_state,
        )
        close_export_logger(logger)
        if m3u_result.status == M3U_STATUS_FAILED:
            sys.exit(4)


if __name__ == "__main__":
    main()
