"""Copy-stage execution for planned playlist operations."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Callable

from ppb.planner import ACTION_CONVERT, ACTION_COPY, ACTION_ERROR, ACTION_SKIP_BLOCKED, DryRunPlan, TrackOperation


STATUS_COPIED = "copied"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUS_SOURCE_MISSING = "source_missing"
STATUS_DESTINATION_EXISTS = "destination_exists"
STATUS_NOT_IMPLEMENTED = "not_implemented"

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
    """Per-track result produced by the real copy stage."""

    position: int
    source_path: str
    destination_path: str | None
    expected_output_filename: str
    planned_action: str
    status: str
    source_size: int | None = None
    destination_size: int | None = None
    bytes_copied: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class CopyStageResult:
    """Complete copy-stage result for ``export_report.json``."""

    output_dir: str
    overwrite: bool
    results: list[CopyTrackResult] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        counts = {
            STATUS_COPIED: 0,
            STATUS_SKIPPED: 0,
            STATUS_FAILED: 0,
            STATUS_SOURCE_MISSING: 0,
            STATUS_DESTINATION_EXISTS: 0,
            STATUS_NOT_IMPLEMENTED: 0,
        }
        for result in self.results:
            counts[result.status] = counts.get(result.status, 0) + 1
        counts["total"] = len(self.results)
        return counts


ProgressCallback = Callable[[int, int, CopyTrackResult], None]


def run_copy_stage(
    *,
    plan: DryRunPlan,
    final_output_dir: Path | str,
    overwrite: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> CopyStageResult:
    """Copy planned source files into the already-created final output folder."""

    output_dir = Path(final_output_dir).resolve(strict=False)
    stage_result = CopyStageResult(output_dir=str(output_dir), overwrite=overwrite)
    total = len(plan.operations)

    for index, operation in enumerate(plan.operations, start=1):
        result = _run_operation(operation, output_dir, overwrite=overwrite)
        stage_result.results.append(result)
        if progress_callback is not None:
            progress_callback(index, total, result)

    return stage_result


def _run_operation(
    operation: TrackOperation,
    output_dir: Path,
    *,
    overwrite: bool,
) -> CopyTrackResult:
    warnings = list(operation.warnings)
    errors = list(operation.errors)

    if operation.planned_action == ACTION_SKIP_BLOCKED:
        return _result(operation, STATUS_SKIPPED, warnings=warnings, errors=errors)

    if operation.planned_action == ACTION_CONVERT:
        return _result(
            operation,
            STATUS_NOT_IMPLEMENTED,
            warnings=warnings,
            errors=["Conversion is not implemented in this stage."],
        )

    if operation.planned_action == ACTION_ERROR:
        status = STATUS_SOURCE_MISSING if not operation.source_exists else STATUS_FAILED
        return _result(operation, status, warnings=warnings, errors=errors)

    if operation.planned_action != ACTION_COPY:
        return _result(
            operation,
            STATUS_SKIPPED,
            warnings=warnings,
            errors=[f"Unsupported planned action for copy stage: {operation.planned_action}"],
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


def _result(
    operation: TrackOperation,
    status: str,
    *,
    destination_path: str | None = None,
    source_size: int | None = None,
    destination_size: int | None = None,
    bytes_copied: int = 0,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> CopyTrackResult:
    return CopyTrackResult(
        position=operation.position,
        source_path=operation.source_path,
        destination_path=destination_path if destination_path is not None else operation.destination_path,
        expected_output_filename=operation.expected_output_filename,
        planned_action=operation.planned_action,
        status=status,
        source_size=source_size,
        destination_size=destination_size,
        bytes_copied=bytes_copied,
        warnings=warnings or [],
        errors=errors or [],
    )


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
