"""Export-stage execution for planned playlist operations."""

from __future__ import annotations

import re
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any, Callable

from ppb.ffmpeg_tools import (
    DEFAULT_MP3_QUALITY,
    STATUS_CONVERTED as FFMPEG_STATUS_CONVERTED,
    STATUS_DESTINATION_EXISTS as FFMPEG_STATUS_DESTINATION_EXISTS,
    STATUS_FFMPEG_UNAVAILABLE,
    STATUS_SOURCE_MISSING as FFMPEG_STATUS_SOURCE_MISSING,
    convert_audio_file,
    measure_loudness_first_pass,
    normalize_loudness_and_encode_mp3_from_source,
)
from ppb.planner import ACTION_CONVERT, ACTION_COPY, ACTION_ERROR, ACTION_SKIP_BLOCKED, DryRunPlan, TrackOperation
from ppb.resume import resume_candidates_by_track_index


STATUS_COPIED = "copied"
STATUS_CONVERTED = "converted"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUS_SOURCE_MISSING = "source_missing"
STATUS_DESTINATION_EXISTS = "destination_exists"
STATUS_FFMPEG_MISSING = "ffmpeg_missing"
STATUS_NOT_IMPLEMENTED = "not_implemented"
STATUS_RESUMED = "resumed"

EXPORT_REPORT_FILENAME = "export_report.json"

_INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


@dataclass
class CopyTrackResult:
    """Per-track result produced by the real export stage."""

    position: int
    source_path: str
    destination_path: str | None
    expected_output_filename: str
    planned_action: str
    status: str
    target_format: str | None = None
    source_size: int | None = None
    destination_size: int | None = None
    bytes_copied: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    ffmpeg_returncode: int | None = None
    ffmpeg_stderr_summary: str = ""
    audio_action: str | None = None
    measurement_source: str | None = None
    normalization_output: str | None = None
    output_format_effective: str | None = None
    loudness_status: str | None = None
    input_i: float | None = None
    input_tp: float | None = None
    input_lra: float | None = None
    input_thresh: float | None = None
    target_offset: float | None = None
    loudness_error: str | None = None
    loudness_stderr_summary: str = ""
    loudness_skip_reason: str | None = None
    loudness_measured_path: str | None = None
    loudness_return_code: int | None = None
    loudness_normalization_status: str | None = None
    normalized_output_path: str | None = None
    loudness_normalization_error: str | None = None
    loudness_normalization_stderr_summary: str = ""
    loudness_normalization_skip_reason: str | None = None
    loudness_normalization_return_code: int | None = None
    size_after_export_before_loudness: int | None = None
    size_after_loudness: int | None = None
    final_size: int | None = None
    post_loudness_status: str | None = None
    post_loudness_skip_reason: str | None = None
    post_loudness_measured_path: str | None = None
    resume_reused: bool = False
    resume_reason: str | None = None
    resume_prior_status: str | None = None
    resume_prior_output_path: str | None = None


@dataclass
class CopyStageResult:
    """Complete export-stage result for ``export_report.json``."""

    output_dir: str
    overwrite: bool
    results: list[CopyTrackResult] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        counts = {
            STATUS_COPIED: 0,
            STATUS_CONVERTED: 0,
            STATUS_SKIPPED: 0,
            STATUS_FAILED: 0,
            STATUS_SOURCE_MISSING: 0,
            STATUS_DESTINATION_EXISTS: 0,
            STATUS_FFMPEG_MISSING: 0,
            STATUS_NOT_IMPLEMENTED: 0,
            STATUS_RESUMED: 0,
        }
        for result in self.results:
            counts[result.status] = counts.get(result.status, 0) + 1
        counts["resume_reuse_skipped_processing"] = sum(
            1 for result in self.results if result.resume_reused
        )
        counts["unsafe_resume_candidates"] = sum(
            1
            for result in self.results
            if result.resume_reason
            and not result.resume_reused
            and result.resume_reason.startswith("not a safe resume candidate")
        )
        counts["total"] = len(self.results)
        return counts


ProgressCallback = Callable[[int, int, CopyTrackResult], None]


def run_copy_stage(
    *,
    plan: DryRunPlan,
    final_output_dir: Path | str,
    overwrite: bool = False,
    ffmpeg_path: Path | str | None = None,
    mp3_quality: int = DEFAULT_MP3_QUALITY,
    audio_bitrate: int | str | None = None,
    target_format: str | None = None,
    fused_loudness_enabled: bool = False,
    fused_target_lufs: float | int | str | None = None,
    fused_true_peak_db: float | int | str | None = None,
    fused_loudness_range_lufs: float | int | str | None = None,
    resume_comparison: dict[str, Any] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> CopyStageResult:
    """Export planned source files into the already-created final output folder."""

    output_dir = Path(final_output_dir).resolve(strict=False)
    stage_result = CopyStageResult(output_dir=str(output_dir), overwrite=overwrite)
    total = len(plan.operations)
    resume_enabled = resume_comparison is not None
    resume_candidates = resume_candidates_by_track_index(resume_comparison)

    for index, operation in enumerate(plan.operations, start=1):
        result = _run_operation(
            operation,
            output_dir,
            overwrite=overwrite,
            ffmpeg_path=ffmpeg_path,
            mp3_quality=mp3_quality,
            audio_bitrate=audio_bitrate,
            target_format=target_format,
            fused_loudness_enabled=fused_loudness_enabled,
            fused_target_lufs=fused_target_lufs,
            fused_true_peak_db=fused_true_peak_db,
            fused_loudness_range_lufs=fused_loudness_range_lufs,
            resume_enabled=resume_enabled,
            resume_candidate=resume_candidates.get(index),
        )
        stage_result.results.append(result)
        if progress_callback is not None:
            progress_callback(index, total, result)

    return stage_result


def _run_operation(
    operation: TrackOperation,
    output_dir: Path,
    *,
    overwrite: bool,
    ffmpeg_path: Path | str | None,
    mp3_quality: int,
    audio_bitrate: int | str | None,
    target_format: str | None,
    fused_loudness_enabled: bool,
    fused_target_lufs: float | int | str | None,
    fused_true_peak_db: float | int | str | None,
    fused_loudness_range_lufs: float | int | str | None,
    resume_enabled: bool = False,
    resume_candidate: dict[str, Any] | None = None,
) -> CopyTrackResult:
    resume_result, resume_reason = _try_resume_operation(
        operation,
        output_dir,
        resume_enabled=resume_enabled,
        resume_candidate=resume_candidate,
        target_format=target_format,
    )
    if resume_result is not None:
        return resume_result

    result = _run_operation_without_resume(
        operation,
        output_dir,
        overwrite=overwrite,
        ffmpeg_path=ffmpeg_path,
        mp3_quality=mp3_quality,
        audio_bitrate=audio_bitrate,
        target_format=target_format,
        fused_loudness_enabled=fused_loudness_enabled,
        fused_target_lufs=fused_target_lufs,
        fused_true_peak_db=fused_true_peak_db,
        fused_loudness_range_lufs=fused_loudness_range_lufs,
    )
    _apply_resume_not_reused(result, resume_candidate, resume_reason)
    return result


def _run_operation_without_resume(
    operation: TrackOperation,
    output_dir: Path,
    *,
    overwrite: bool,
    ffmpeg_path: Path | str | None,
    mp3_quality: int,
    audio_bitrate: int | str | None,
    target_format: str | None,
    fused_loudness_enabled: bool = False,
    fused_target_lufs: float | int | str | None = None,
    fused_true_peak_db: float | int | str | None = None,
    fused_loudness_range_lufs: float | int | str | None = None,
) -> CopyTrackResult:
    warnings = list(operation.warnings)
    errors = list(operation.errors)

    if operation.planned_action == ACTION_SKIP_BLOCKED:
        return _result(operation, STATUS_SKIPPED, warnings=warnings, errors=errors)

    if _should_run_fused_mp3_loudness_operation(
        operation,
        fused_loudness_enabled=fused_loudness_enabled,
        target_format=target_format,
    ):
        return _fused_mp3_loudness_operation(
            operation,
            output_dir,
            overwrite=overwrite,
            ffmpeg_path=ffmpeg_path,
            mp3_quality=mp3_quality,
            audio_bitrate=audio_bitrate,
            target_lufs=fused_target_lufs,
            true_peak_db=fused_true_peak_db,
            loudness_range_lufs=fused_loudness_range_lufs,
            warnings=warnings,
            errors=errors,
        )

    if operation.planned_action == ACTION_CONVERT:
        return _convert_operation(
            operation,
            output_dir,
            overwrite=overwrite,
            ffmpeg_path=ffmpeg_path,
            mp3_quality=mp3_quality,
            audio_bitrate=audio_bitrate,
            target_format=target_format,
            warnings=warnings,
            errors=errors,
        )

    if operation.planned_action == ACTION_ERROR:
        status = STATUS_SOURCE_MISSING if not operation.source_exists else STATUS_FAILED
        return _result(operation, status, warnings=warnings, errors=errors)

    if operation.planned_action != ACTION_COPY:
        return _result(
            operation,
            STATUS_SKIPPED,
            warnings=warnings,
            errors=[f"Unsupported planned action for export stage: {operation.planned_action}"],
        )

    filename = operation.expected_output_filename
    filename_error = _unsafe_destination_filename_reason(filename)
    if filename_error:
        return _result(operation, STATUS_FAILED, warnings=warnings, errors=errors + [filename_error])

    destination_path = (output_dir / filename).resolve(strict=False)
    if not _is_relative_to(destination_path, output_dir):
        return _result(
            operation,
            STATUS_FAILED,
            destination_path=str(destination_path),
            warnings=warnings,
            errors=errors + ["Destination path escapes the output directory."],
        )

    source_path = Path(operation.source_path)
    if not source_path.is_file():
        return _result(
            operation,
            STATUS_SOURCE_MISSING,
            destination_path=str(destination_path),
            warnings=warnings,
            errors=errors + [f"Source file does not exist on disk: {operation.source_path}"],
        )

    source_size = source_path.stat().st_size
    if destination_path.exists() and not overwrite:
        destination_size = destination_path.stat().st_size if destination_path.is_file() else None
        return _result(
            operation,
            STATUS_DESTINATION_EXISTS,
            destination_path=str(destination_path),
            source_size=source_size,
            destination_size=destination_size,
            warnings=warnings,
            errors=errors + [f"Destination file already exists: {destination_path}"],
        )
    if destination_path.exists() and not destination_path.is_file():
        return _result(
            operation,
            STATUS_FAILED,
            destination_path=str(destination_path),
            source_size=source_size,
            warnings=warnings,
            errors=errors + [f"Destination path exists but is not a file: {destination_path}"],
        )

    try:
        shutil.copy2(source_path, destination_path)
        writable_warning = _make_exported_file_writable(destination_path)
        destination_size = destination_path.stat().st_size
    except OSError as exc:
        return _result(
            operation,
            STATUS_FAILED,
            destination_path=str(destination_path),
            source_size=source_size,
            warnings=warnings,
            errors=errors + [str(exc)],
        )

    if writable_warning:
        warnings.append(writable_warning)

    if destination_size != source_size:
        return _result(
            operation,
            STATUS_FAILED,
            destination_path=str(destination_path),
            source_size=source_size,
            destination_size=destination_size,
            warnings=warnings,
            errors=errors
            + [
                "Copied file size verification failed: "
                f"source={source_size} destination={destination_size}"
            ],
        )

    return _result(
        operation,
        STATUS_COPIED,
        destination_path=str(destination_path),
        source_size=source_size,
        destination_size=destination_size,
        bytes_copied=destination_size,
        warnings=warnings,
        errors=errors,
    )


def _convert_operation(
    operation: TrackOperation,
    output_dir: Path,
    *,
    overwrite: bool,
    ffmpeg_path: Path | str | None,
    mp3_quality: int,
    audio_bitrate: int | str | None,
    target_format: str | None,
    warnings: list[str],
    errors: list[str],
) -> CopyTrackResult:
    filename = operation.expected_output_filename
    filename_error = _unsafe_destination_filename_reason(filename)
    if filename_error:
        return _result(operation, STATUS_FAILED, warnings=warnings, errors=errors + [filename_error])

    destination_path = (output_dir / filename).resolve(strict=False)
    if not _is_relative_to(destination_path, output_dir):
        return _result(
            operation,
            STATUS_FAILED,
            destination_path=str(destination_path),
            warnings=warnings,
            errors=errors + ["Destination path escapes the output directory."],
        )

    source_path = Path(operation.source_path)
    if not source_path.is_file():
        return _result(
            operation,
            STATUS_SOURCE_MISSING,
            destination_path=str(destination_path),
            warnings=warnings,
            errors=errors + [f"Source file does not exist on disk: {operation.source_path}"],
        )

    source_size = source_path.stat().st_size
    if destination_path.exists() and not overwrite:
        destination_size = destination_path.stat().st_size if destination_path.is_file() else None
        return _result(
            operation,
            STATUS_DESTINATION_EXISTS,
            destination_path=str(destination_path),
            source_size=source_size,
            destination_size=destination_size,
            warnings=warnings,
            errors=errors + [f"Destination file already exists: {destination_path}"],
        )
    if destination_path.exists() and not destination_path.is_file():
        return _result(
            operation,
            STATUS_FAILED,
            destination_path=str(destination_path),
            source_size=source_size,
            warnings=warnings,
            errors=errors + [f"Destination path exists but is not a file: {destination_path}"],
        )

    conversion_format = _target_format_for_conversion(operation, target_format)
    if not conversion_format:
        return _result(
            operation,
            STATUS_FAILED,
            destination_path=str(destination_path),
            source_size=source_size,
            warnings=warnings,
            errors=errors + ["Could not determine target format for conversion."],
        )

    conversion = convert_audio_file(
        source_path=source_path,
        destination_path=destination_path,
        output_folder=output_dir,
        target_format=conversion_format,
        ffmpeg_path=ffmpeg_path,
        mp3_quality=mp3_quality,
        audio_bitrate=audio_bitrate,
        overwrite=overwrite,
    )
    status = _map_conversion_status(conversion.status)
    result_errors = errors + list(conversion.errors)
    result_warnings = warnings + list(conversion.warnings)

    destination_size = destination_path.stat().st_size if destination_path.is_file() else None
    return _result(
        operation,
        status,
        destination_path=str(destination_path),
        target_format=conversion.target_format,
        source_size=source_size,
        destination_size=destination_size,
        bytes_copied=0,
        warnings=result_warnings,
        errors=result_errors,
        ffmpeg_returncode=conversion.returncode,
        ffmpeg_stderr_summary=conversion.stderr_summary,
    )


def _should_run_fused_mp3_loudness_operation(
    operation: TrackOperation,
    *,
    fused_loudness_enabled: bool,
    target_format: str | None,
) -> bool:
    if not fused_loudness_enabled:
        return False
    if operation.planned_action not in {ACTION_COPY, ACTION_CONVERT}:
        return False
    return _target_format_for_conversion(operation, target_format) == "mp3"


def _fused_mp3_loudness_operation(
    operation: TrackOperation,
    output_dir: Path,
    *,
    overwrite: bool,
    ffmpeg_path: Path | str | None,
    mp3_quality: int,
    audio_bitrate: int | str | None,
    target_lufs: float | int | str | None,
    true_peak_db: float | int | str | None,
    loudness_range_lufs: float | int | str | None,
    warnings: list[str],
    errors: list[str],
) -> CopyTrackResult:
    filename = operation.expected_output_filename
    filename_error = _unsafe_destination_filename_reason(filename)
    if filename_error:
        return _result(operation, STATUS_FAILED, warnings=warnings, errors=errors + [filename_error])

    destination_path = (output_dir / filename).resolve(strict=False)
    if not _is_relative_to(destination_path, output_dir):
        return _result(
            operation,
            STATUS_FAILED,
            destination_path=str(destination_path),
            warnings=warnings,
            errors=errors + ["Destination path escapes the output directory."],
        )

    source_path = Path(operation.source_path)
    if not source_path.is_file():
        return _result(
            operation,
            STATUS_SOURCE_MISSING,
            destination_path=str(destination_path),
            target_format="mp3",
            source_size=None,
            warnings=warnings,
            errors=errors + [f"Source file does not exist on disk: {operation.source_path}"],
        )

    source_size = source_path.stat().st_size
    if destination_path.exists() and not overwrite:
        destination_size = destination_path.stat().st_size if destination_path.is_file() else None
        return _result(
            operation,
            STATUS_DESTINATION_EXISTS,
            destination_path=str(destination_path),
            target_format="mp3",
            source_size=source_size,
            destination_size=destination_size,
            warnings=warnings,
            errors=errors + [f"Destination file already exists: {destination_path}"],
        )
    if destination_path.exists() and not destination_path.is_file():
        return _result(
            operation,
            STATUS_FAILED,
            destination_path=str(destination_path),
            target_format="mp3",
            source_size=source_size,
            warnings=warnings,
            errors=errors + [f"Destination path exists but is not a file: {destination_path}"],
        )

    measurement = measure_loudness_first_pass(
        source_path=source_path,
        ffmpeg=ffmpeg_path,
        target_lufs=target_lufs,
        true_peak_db=true_peak_db,
        loudness_range_lufs=loudness_range_lufs,
    )
    if not measurement.success:
        status = _map_fused_measurement_failure_status(measurement.status)
        error = "; ".join(measurement.errors) or measurement.stderr_summary or "loudness measurement failed."
        return _result(
            operation,
            status,
            destination_path=str(destination_path),
            target_format="mp3",
            source_size=source_size,
            warnings=warnings,
            errors=errors + [error],
            ffmpeg_returncode=measurement.return_code,
            ffmpeg_stderr_summary=measurement.stderr_summary,
            **_fused_loudness_failure_fields(
                measurement_status=_map_loudness_status(measurement.status),
                measurement_error=error,
                measurement_stderr_summary=measurement.stderr_summary,
                measurement_return_code=measurement.return_code,
                measured_path=str(Path(measurement.source_path).resolve(strict=False)),
            ),
        )

    destination_existed_before = destination_path.exists()
    normalization = normalize_loudness_and_encode_mp3_from_source(
        source_path=source_path,
        final_mp3_path=destination_path,
        final_output_dir=output_dir,
        input_i=measurement.input_i,
        input_tp=measurement.input_tp,
        input_lra=measurement.input_lra,
        input_thresh=measurement.input_thresh,
        target_offset=measurement.target_offset,
        ffmpeg=measurement.ffmpeg or ffmpeg_path,
        target_lufs=target_lufs,
        true_peak_db=true_peak_db,
        loudness_range_lufs=loudness_range_lufs,
        mp3_quality=mp3_quality,
        audio_bitrate=audio_bitrate,
        overwrite=overwrite,
    )
    if not normalization.success:
        cleanup_warning = _remove_failed_fused_final_output(
            destination_path,
            output_dir,
            destination_existed_before=destination_existed_before,
        )
        result_warnings = warnings + list(normalization.warnings)
        if cleanup_warning:
            result_warnings.append(cleanup_warning)
        status = _map_conversion_status(normalization.status)
        error = (
            "; ".join(normalization.errors)
            or normalization.stderr_summary
            or "fused MP3 loudness encode failed."
        )
        return _result(
            operation,
            status,
            destination_path=str(destination_path),
            target_format="mp3",
            source_size=source_size,
            destination_size=destination_path.stat().st_size if destination_path.is_file() else None,
            warnings=result_warnings,
            errors=errors + list(normalization.errors),
            ffmpeg_returncode=normalization.return_code,
            ffmpeg_stderr_summary=normalization.stderr_summary,
            **_fused_loudness_normalization_failure_fields(
                measurement=measurement,
                normalization_status=_map_loudness_status(normalization.status),
                normalization_error=error,
                normalization_stderr_summary=normalization.stderr_summary,
                normalization_return_code=normalization.return_code,
            ),
        )

    final_size = destination_path.stat().st_size if destination_path.is_file() else None
    return _result(
        operation,
        STATUS_CONVERTED,
        destination_path=str(destination_path),
        target_format="mp3",
        source_size=source_size,
        destination_size=final_size,
        bytes_copied=0,
        warnings=warnings + list(normalization.warnings),
        errors=errors,
        ffmpeg_returncode=normalization.return_code,
        ffmpeg_stderr_summary=normalization.stderr_summary,
        **_fused_loudness_success_fields(
            measurement=measurement,
            normalization=normalization,
            final_path=str(destination_path),
            final_size=final_size,
        ),
    )


def _result(
    operation: TrackOperation,
    status: str,
    *,
    destination_path: str | None = None,
    target_format: str | None = None,
    source_size: int | None = None,
    destination_size: int | None = None,
    bytes_copied: int = 0,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    ffmpeg_returncode: int | None = None,
    ffmpeg_stderr_summary: str = "",
    audio_action: str | None = None,
    measurement_source: str | None = None,
    normalization_output: str | None = None,
    output_format_effective: str | None = None,
    loudness_status: str | None = None,
    input_i: float | None = None,
    input_tp: float | None = None,
    input_lra: float | None = None,
    input_thresh: float | None = None,
    target_offset: float | None = None,
    loudness_error: str | None = None,
    loudness_stderr_summary: str = "",
    loudness_skip_reason: str | None = None,
    loudness_measured_path: str | None = None,
    loudness_return_code: int | None = None,
    loudness_normalization_status: str | None = None,
    normalized_output_path: str | None = None,
    loudness_normalization_error: str | None = None,
    loudness_normalization_stderr_summary: str = "",
    loudness_normalization_skip_reason: str | None = None,
    loudness_normalization_return_code: int | None = None,
    size_after_export_before_loudness: int | None = None,
    size_after_loudness: int | None = None,
    final_size: int | None = None,
    post_loudness_status: str | None = None,
    post_loudness_skip_reason: str | None = None,
    post_loudness_measured_path: str | None = None,
    resume_reused: bool = False,
    resume_reason: str | None = None,
    resume_prior_status: str | None = None,
    resume_prior_output_path: str | None = None,
) -> CopyTrackResult:
    return CopyTrackResult(
        position=operation.position,
        source_path=operation.source_path,
        destination_path=destination_path if destination_path is not None else operation.destination_path,
        expected_output_filename=operation.expected_output_filename,
        planned_action=operation.planned_action,
        status=status,
        target_format=target_format,
        source_size=source_size,
        destination_size=destination_size,
        bytes_copied=bytes_copied,
        warnings=warnings or [],
        errors=errors or [],
        ffmpeg_returncode=ffmpeg_returncode,
        ffmpeg_stderr_summary=ffmpeg_stderr_summary,
        audio_action=audio_action,
        measurement_source=measurement_source,
        normalization_output=normalization_output,
        output_format_effective=output_format_effective,
        loudness_status=loudness_status,
        input_i=input_i,
        input_tp=input_tp,
        input_lra=input_lra,
        input_thresh=input_thresh,
        target_offset=target_offset,
        loudness_error=loudness_error,
        loudness_stderr_summary=loudness_stderr_summary,
        loudness_skip_reason=loudness_skip_reason,
        loudness_measured_path=loudness_measured_path,
        loudness_return_code=loudness_return_code,
        loudness_normalization_status=loudness_normalization_status,
        normalized_output_path=normalized_output_path,
        loudness_normalization_error=loudness_normalization_error,
        loudness_normalization_stderr_summary=loudness_normalization_stderr_summary,
        loudness_normalization_skip_reason=loudness_normalization_skip_reason,
        loudness_normalization_return_code=loudness_normalization_return_code,
        size_after_export_before_loudness=size_after_export_before_loudness,
        size_after_loudness=size_after_loudness,
        final_size=final_size,
        post_loudness_status=post_loudness_status,
        post_loudness_skip_reason=post_loudness_skip_reason,
        post_loudness_measured_path=post_loudness_measured_path,
        resume_reused=resume_reused,
        resume_reason=resume_reason,
        resume_prior_status=resume_prior_status,
        resume_prior_output_path=resume_prior_output_path,
    )


def _fused_common_fields() -> dict[str, Any]:
    return {
        "audio_action": "fused_loudnorm_encode",
        "measurement_source": "source",
        "normalization_output": "final_mp3",
        "output_format_effective": "mp3",
    }


def _fused_loudness_success_fields(
    *,
    measurement,
    normalization,
    final_path: str,
    final_size: int | None,
) -> dict[str, Any]:
    return {
        **_fused_common_fields(),
        "loudness_status": "measured",
        "input_i": measurement.input_i,
        "input_tp": measurement.input_tp,
        "input_lra": measurement.input_lra,
        "input_thresh": measurement.input_thresh,
        "target_offset": measurement.target_offset,
        "loudness_error": None,
        "loudness_stderr_summary": measurement.stderr_summary,
        "loudness_skip_reason": None,
        "loudness_measured_path": str(Path(measurement.source_path).resolve(strict=False)),
        "loudness_return_code": measurement.return_code,
        "loudness_normalization_status": "normalized",
        "normalized_output_path": str(Path(final_path).resolve(strict=False)),
        "loudness_normalization_error": None,
        "loudness_normalization_stderr_summary": normalization.stderr_summary,
        "loudness_normalization_skip_reason": None,
        "loudness_normalization_return_code": normalization.return_code,
        "size_after_export_before_loudness": None,
        "size_after_loudness": final_size,
        "final_size": final_size,
        "post_loudness_status": "skipped",
        "post_loudness_skip_reason": (
            "post-normalization verification was skipped for fused source-to-final MP3 export."
        ),
        "post_loudness_measured_path": str(Path(final_path).resolve(strict=False)),
    }


def _fused_loudness_failure_fields(
    *,
    measurement_status: str,
    measurement_error: str,
    measurement_stderr_summary: str,
    measurement_return_code: int | None,
    measured_path: str,
) -> dict[str, Any]:
    return {
        **_fused_common_fields(),
        "loudness_status": measurement_status,
        "loudness_error": measurement_error,
        "loudness_stderr_summary": measurement_stderr_summary,
        "loudness_skip_reason": None,
        "loudness_measured_path": measured_path,
        "loudness_return_code": measurement_return_code,
        "loudness_normalization_status": measurement_status,
        "loudness_normalization_error": (
            "fused MP3 encode was not attempted because source loudness measurement failed: "
            f"{measurement_error}"
        ),
        "loudness_normalization_stderr_summary": measurement_stderr_summary,
        "loudness_normalization_skip_reason": None,
        "loudness_normalization_return_code": measurement_return_code,
        "post_loudness_status": "skipped",
        "post_loudness_skip_reason": (
            "post-normalization verification was skipped because fused normalization did not succeed."
        ),
        "post_loudness_measured_path": None,
    }


def _fused_loudness_normalization_failure_fields(
    *,
    measurement,
    normalization_status: str,
    normalization_error: str,
    normalization_stderr_summary: str,
    normalization_return_code: int | None,
) -> dict[str, Any]:
    return {
        **_fused_common_fields(),
        "loudness_status": "measured",
        "input_i": measurement.input_i,
        "input_tp": measurement.input_tp,
        "input_lra": measurement.input_lra,
        "input_thresh": measurement.input_thresh,
        "target_offset": measurement.target_offset,
        "loudness_error": None,
        "loudness_stderr_summary": measurement.stderr_summary,
        "loudness_skip_reason": None,
        "loudness_measured_path": str(Path(measurement.source_path).resolve(strict=False)),
        "loudness_return_code": measurement.return_code,
        "loudness_normalization_status": normalization_status,
        "loudness_normalization_error": normalization_error,
        "loudness_normalization_stderr_summary": normalization_stderr_summary,
        "loudness_normalization_skip_reason": None,
        "loudness_normalization_return_code": normalization_return_code,
        "post_loudness_status": "skipped",
        "post_loudness_skip_reason": (
            "post-normalization verification was skipped because fused normalization did not succeed."
        ),
        "post_loudness_measured_path": None,
    }


def _map_fused_measurement_failure_status(status: str) -> str:
    if status == STATUS_FFMPEG_UNAVAILABLE:
        return STATUS_FFMPEG_MISSING
    if status == FFMPEG_STATUS_SOURCE_MISSING:
        return STATUS_SOURCE_MISSING
    return STATUS_FAILED


def _map_loudness_status(status: str) -> str:
    if status == STATUS_FFMPEG_UNAVAILABLE:
        return "ffmpeg_missing"
    if status == FFMPEG_STATUS_CONVERTED or status == "normalized":
        return "normalized"
    return "failed"


def _remove_failed_fused_final_output(
    destination_path: Path,
    output_dir: Path,
    *,
    destination_existed_before: bool,
) -> str | None:
    if destination_existed_before or not destination_path.is_file():
        return None
    if not _is_relative_to(destination_path, output_dir):
        return "Could not remove failed fused MP3 output because it is outside final output folder."
    try:
        destination_path.unlink()
    except OSError as exc:
        return f"Could not remove failed fused MP3 output: {exc}"
    return None


def _try_resume_operation(
    operation: TrackOperation,
    output_dir: Path,
    *,
    resume_enabled: bool,
    resume_candidate: dict[str, Any] | None,
    target_format: str | None,
) -> tuple[CopyTrackResult | None, str | None]:
    if not resume_enabled:
        return None, None
    if not isinstance(resume_candidate, dict):
        return None, "no resume comparison candidate was available; processing normally."

    prior_status = _text_or_none(resume_candidate.get("prior_status"))
    prior_output_path = _text_or_none(resume_candidate.get("prior_output_path"))
    if not resume_candidate.get("safe_to_reuse_candidate"):
        reason = _text_or_none(resume_candidate.get("reason")) or "candidate was not safe."
        return None, f"not a safe resume candidate: {reason}"

    if operation.planned_action not in {ACTION_COPY, ACTION_CONVERT}:
        return None, (
            "safe resume candidate invalid at execution time: "
            f"current planned action is not reusable: {operation.planned_action}."
        )
    if operation.errors:
        return None, "safe resume candidate invalid at execution time: current operation has errors."

    prior_destination_path = None
    if prior_output_path:
        prior_destination_path = _resolve_inside_output(prior_output_path, output_dir)
        if prior_destination_path is None:
            return None, (
                "safe resume candidate invalid at execution time: "
                "prior output path is outside the selected final output folder."
            )

    destination_path = _resolve_inside_output(operation.destination_path, output_dir)
    if destination_path is None:
        return None, (
            "safe resume candidate invalid at execution time: "
            "current planned output path is missing or outside final output folder."
        )
    if prior_destination_path is not None and prior_destination_path != destination_path:
        return None, (
            "safe resume candidate invalid at execution time: "
            "prior output path does not match the current planned output path."
        )
    if not destination_path.is_file():
        return None, (
            "safe resume candidate invalid at execution time: "
            "planned output file is missing."
        )

    destination_size = _file_size(destination_path)
    if destination_size is None:
        return None, (
            "safe resume candidate invalid at execution time: "
            "existing output size is unavailable."
        )

    source_size = _file_size(Path(operation.source_path)) if operation.source_path else None
    if operation.planned_action == ACTION_COPY:
        if source_size is None:
            return None, (
                "safe resume candidate invalid at execution time: "
                "current source file size is unavailable for copy verification."
            )
        if source_size != destination_size:
            return None, (
                "safe resume candidate invalid at execution time: "
                "existing output size differs from current source file size."
            )

    if operation.planned_action == ACTION_CONVERT and destination_size <= 0:
        return None, (
            "safe resume candidate invalid at execution time: "
            "existing converted output file is empty."
        )

    if prior_status not in {STATUS_COPIED, STATUS_CONVERTED}:
        return None, (
            "safe resume candidate invalid at execution time: "
            f"prior status is not successful: {prior_status or '(missing)'}."
        )

    return (
        _result(
            operation,
            STATUS_RESUMED,
            destination_path=str(destination_path),
            target_format=(
                _target_format_for_conversion(operation, target_format)
                if operation.planned_action == ACTION_CONVERT
                else None
            ),
            source_size=source_size,
            destination_size=destination_size,
            bytes_copied=0,
            warnings=list(operation.warnings),
            errors=list(operation.errors),
            resume_reused=True,
            resume_reason=_resume_success_reason(operation.planned_action),
            resume_prior_status=prior_status,
            resume_prior_output_path=prior_output_path,
        ),
        None,
    )


def _apply_resume_not_reused(
    result: CopyTrackResult,
    resume_candidate: dict[str, Any] | None,
    resume_reason: str | None,
) -> None:
    if resume_reason is None:
        return
    result.resume_reused = False
    result.resume_reason = resume_reason
    if isinstance(resume_candidate, dict):
        result.resume_prior_status = _text_or_none(resume_candidate.get("prior_status"))
        result.resume_prior_output_path = _text_or_none(resume_candidate.get("prior_output_path"))


def _resume_success_reason(planned_action: str) -> str:
    if planned_action == ACTION_COPY:
        return "safe resume candidate reused; existing copy size matches current source size."
    if planned_action == ACTION_CONVERT:
        return "safe resume candidate reused; prior conversion succeeded and existing output is present."
    return "safe resume candidate reused."


def _resolve_inside_output(value: str | None, output_dir: Path) -> Path | None:
    if value in (None, ""):
        return None
    try:
        candidate = Path(str(value)).expanduser().resolve(strict=False)
    except (OSError, ValueError):
        return None
    if candidate == output_dir or not _is_relative_to(candidate, output_dir):
        return None
    return candidate


def _file_size(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        return path.stat().st_size if path.is_file() else None
    except OSError:
        return None


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _make_exported_file_writable(path: Path) -> str | None:
    """Clear read-only mode on an exported copy without touching source files."""

    try:
        if not path.is_file():
            return None
        current_mode = path.stat().st_mode
        writable_mode = current_mode | stat.S_IWRITE | stat.S_IWUSR
        if writable_mode != current_mode:
            path.chmod(writable_mode)
    except OSError as exc:
        return f"Could not make exported copy writable after copy: {exc}"
    return None


def _target_format_for_conversion(operation: TrackOperation, target_format: str | None) -> str:
    normalized = (target_format or "").strip().lower().lstrip(".")
    if normalized and normalized != "source":
        return normalized
    return Path(operation.expected_output_filename).suffix.lower().lstrip(".")


def _map_conversion_status(status: str) -> str:
    if status == FFMPEG_STATUS_CONVERTED:
        return STATUS_CONVERTED
    if status == STATUS_FFMPEG_UNAVAILABLE:
        return STATUS_FFMPEG_MISSING
    if status == FFMPEG_STATUS_SOURCE_MISSING:
        return STATUS_SOURCE_MISSING
    if status == FFMPEG_STATUS_DESTINATION_EXISTS:
        return STATUS_DESTINATION_EXISTS
    return STATUS_FAILED


def _unsafe_destination_filename_reason(filename: str) -> str | None:
    if not filename:
        return "Destination filename is empty."
    if Path(filename).is_absolute() or PureWindowsPath(filename).is_absolute():
        return f"Destination filename is absolute: {filename}"
    if Path(filename).name != filename or "\\" in filename or "/" in filename:
        return f"Destination filename must be a leaf filename, not a path: {filename}"
    if ".." in Path(filename).parts:
        return f"Destination filename contains parent traversal: {filename}"
    if _INVALID_FILENAME_CHARS_RE.search(filename):
        return f"Destination filename contains invalid filesystem characters: {filename}"
    if filename in {".", ".."} or not filename.strip(" ."):
        return f"Destination filename is not usable: {filename}"
    if _is_reserved_windows_name(filename):
        return f"Destination filename uses a reserved Windows device name: {filename}"
    return None


def _is_reserved_windows_name(filename: str) -> bool:
    stem = filename.split(".", 1)[0].upper()
    return stem in _RESERVED_WINDOWS_NAMES


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
