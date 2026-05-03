"""Export-stage execution for planned playlist operations."""

from __future__ import annotations

import re
import shutil
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
) -> CopyTrackResult:
    warnings = list(operation.warnings)
    errors = list(operation.errors)

    if operation.planned_action == ACTION_SKIP_BLOCKED:
        return _result(operation, STATUS_SKIPPED, warnings=warnings, errors=errors)

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
        resume_reused=resume_reused,
        resume_reason=resume_reason,
        resume_prior_status=resume_prior_status,
        resume_prior_output_path=resume_prior_output_path,
    )


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
