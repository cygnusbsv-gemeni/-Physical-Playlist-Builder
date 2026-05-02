"""Physical Playlist Builder CLI.

This stage validates neutral playlist input, prints a dry-run operation plan,
creates a safe output folder, copies or converts planned source files into it,
optionally normalizes loudness for those exported copies, and then generates an
M3U8 playlist from the final exported files. It does not write tags.
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
    CopyTrackResult,
    run_copy_stage,
)
from ppb.ffmpeg_tools import (
    DEFAULT_LOUDNESS_RANGE_LUFS,
    STATUS_FFMPEG_UNAVAILABLE as FFMPEG_STATUS_UNAVAILABLE,
    measure_loudness_first_pass,
    normalize_loudness_second_pass,
)
from ppb.filesystem import OutputFolderError, build_output_folder_target, create_output_folder
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


def print_copy_progress(index: int, total: int, result: CopyTrackResult) -> None:
    filename = result.expected_output_filename or Path(result.source_path).name or "(no filename)"
    print(f"[export] {index}/{total} {result.status}: {filename}")


def print_copy_summary(copy_result) -> None:
    summary = copy_result.summary
    print("=" * 52)
    print("  Export Stage Summary")
    print("=" * 52)
    print(f"  Tracks processed: {summary['total']}")
    print(f"  Copied: {summary['copied']}")
    print(f"  Converted: {summary['converted']}")
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


def print_final_export_summary(
    *,
    final_output_dir: Path | str,
    copy_result,
    m3u_result,
    report_path: Path | str,
    report_txt_path: Path | str,
    log_path: Path | str,
) -> None:
    summary = copy_result.summary
    print("=" * 52)
    print("  Final Export Summary")
    print("=" * 52)
    print(f"  Final output folder: {final_output_dir}")
    print(f"  Copied: {summary['copied']}")
    print(f"  Converted: {summary['converted']}")
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
        elif result.status == STATUS_FFMPEG_MISSING:
            logger.error("track %s ffmpeg missing: %s", result.position, result.destination_path)
        for warning in result.warnings:
            logger.warning("track %s warning: %s", result.position, warning)
        for error in result.errors:
            logger.error("track %s error: %s", result.position, error)
        if result.ffmpeg_stderr_summary:
            logger.error(
                "track %s ffmpeg stderr summary: %s",
                result.position,
                result.ffmpeg_stderr_summary,
            )


def log_m3u_details(logger, m3u_result) -> None:
    for warning in m3u_result.warnings:
        logger.warning("m3u warning: %s", warning)
    for error in m3u_result.errors:
        logger.error("m3u error: %s", error)


def run_loudness_measurement_stage(
    *,
    copy_result,
    final_output_dir: Path | str,
    settings,
    skip_loudness: bool,
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

    for track_result in copy_result.results:
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

        destination_path = str(Path(track_result.destination_path).resolve(strict=False))
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
        )
        if measurement.success:
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
        else:
            loudness_result.update(_normalization_not_attempted_after_measurement_failure(measurement))
        results.append(loudness_result)
        _log_loudness_track_result(logger, track_result, loudness_result)
        _log_loudness_normalization_track_result(logger, track_result, loudness_result)

    totals = _count_loudness_results(results)
    measurement_totals = _count_loudness_measurement_results(results)
    normalization_totals = _count_loudness_normalization_results(results)
    summary = {
        "status": _loudness_stage_status(
            should_process=should_process,
            eligible_count=eligible_count,
            totals=totals,
        ),
        "reason": reason,
        "normalize_loudness": normalize_loudness,
        "skip_loudness": skip_loudness,
        "target_lufs": target_lufs,
        "true_peak_db": true_peak_db,
        "loudness_range_lufs": loudness_range_lufs,
        "eligible_track_count": eligible_count,
        "totals": totals,
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
    }


def _loudness_measurement_to_report(
    measurement,
    *,
    measured_path: str,
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
            "loudness_stderr_summary": "",
            "loudness_skip_reason": None,
            "loudness_measured_path": measured_path,
            "loudness_return_code": measurement.return_code,
            "loudness_normalization_status": LOUDNESS_STATUS_SKIPPED,
            "normalized_output_path": None,
            "loudness_normalization_error": None,
            "loudness_normalization_stderr_summary": "",
            "loudness_normalization_skip_reason": "loudness normalization was not evaluated.",
            "loudness_normalization_return_code": None,
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
            "loudness_normalization_stderr_summary": "",
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
        try:
            logger, log_path = setup_export_logger(output_result.final_output_dir)
        except OSError as exc:
            print(f"ERROR: could not write export.log: {exc}", file=sys.stderr)
            sys.exit(4)

        logger.info("validation completed")
        log_validation_details(logger, result)
        logger.info("output folder created: %s", output_result.final_output_dir)
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
            progress_callback=print_copy_progress,
        )
        logger.info("export stage completed: %s", copy_result.summary)
        log_copy_details(logger, copy_result)
        loudness_results, loudness_summary = run_loudness_measurement_stage(
            copy_result=copy_result,
            final_output_dir=output_result.final_output_dir,
            settings=result.job.settings,
            skip_loudness=args.skip_loudness,
            ffmpeg_path=args.ffmpeg,
            mp3_quality=args.mp3_quality,
            audio_bitrate=args.audio_bitrate,
            logger=logger,
        )
        report_path = Path(output_result.final_output_dir) / EXPORT_REPORT_FILENAME
        report_txt_path = Path(output_result.final_output_dir) / EXPORT_REPORT_TEXT_FILENAME
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
        )
        close_export_logger(logger)
        if m3u_result.status == M3U_STATUS_FAILED:
            sys.exit(4)


if __name__ == "__main__":
    main()
