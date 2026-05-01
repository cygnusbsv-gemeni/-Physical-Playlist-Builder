"""Dry-run operation planning for validated playlist jobs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any

from ppb.contract import PlaylistJob, TrackEntry


ACTION_COPY = "copy"
ACTION_CONVERT = "convert"
ACTION_SKIP_BLOCKED = "skip_blocked"
ACTION_ERROR = "error"

_INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SPACE_RE = re.compile(r"\s+")
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


@dataclass
class TrackOperation:
    """A dry-run operation or skip/error record for one input track."""

    position: int
    source_path: str
    destination_path: str | None
    planned_action: str
    source_exists: bool
    destination_filename_conflict: bool
    expected_output_filename: str
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def is_safe_to_execute(self) -> bool:
        return (
            self.planned_action in {ACTION_COPY, ACTION_CONVERT}
            and self.source_exists
            and not self.destination_filename_conflict
            and not self.errors
            and self.destination_path is not None
        )


@dataclass
class DryRunPlan:
    """Complete dry-run plan for an output folder stage."""

    output_dir: str
    output_dir_valid: bool
    output_dir_exists: bool
    output_dir_overwrites_source_dir: bool
    output_dir_inside_source_dir: bool = False
    dangerous_output_paths: list[str] = field(default_factory=list)
    duplicate_output_filenames: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    operations: list[TrackOperation] = field(default_factory=list)
    blocked_tracks: list[TrackOperation] = field(default_factory=list)

    @property
    def safe_operations(self) -> list[TrackOperation]:
        if self.errors:
            return []
        return [operation for operation in self.operations if operation.is_safe_to_execute]

    @property
    def has_errors(self) -> bool:
        return bool(self.errors or any(operation.errors for operation in self.operations))

    @property
    def error_count(self) -> int:
        return len(self.errors) + sum(len(operation.errors) for operation in self.operations)

    @property
    def warning_count(self) -> int:
        return len(self.warnings) + sum(len(operation.warnings) for operation in self.operations)


def build_dry_run_plan(job: PlaylistJob, output_dir: Path | str) -> DryRunPlan:
    """Build a dry-run plan without creating folders or music files."""

    output_path = Path(output_dir).expanduser()
    resolved_output = output_path.resolve(strict=False)
    output_dir_exists = output_path.exists()

    errors: list[str] = []
    warnings: list[str] = []
    dangerous_output_paths: list[str] = []

    if not str(output_dir).strip():
        errors.append("Output directory path is empty.")
    output_dir_safety_error = _unsafe_output_dir_reason(output_path)
    if output_dir_safety_error:
        errors.append(output_dir_safety_error)
        dangerous_output_paths.append(str(output_path))
    if _is_filesystem_root(resolved_output):
        errors.append(f"Output directory must not be a filesystem root: {resolved_output}")
        dangerous_output_paths.append(str(resolved_output))
    if output_dir_exists and not output_path.is_dir():
        errors.append(f"Output path exists but is not a directory: {output_path}")
        dangerous_output_paths.append(str(output_path))

    output_overwrites_source = _output_overwrites_source_dir(resolved_output, job.tracks)
    if output_overwrites_source:
        errors.append("Output directory must not be the same as a source track directory.")
        dangerous_output_paths.append(str(resolved_output))
    output_inside_source = _output_inside_source_dir(resolved_output, job.tracks)
    if output_inside_source:
        errors.append("Output directory must not be inside a source track directory.")
        dangerous_output_paths.append(str(resolved_output))

    operations: list[TrackOperation] = []
    blocked_tracks: list[TrackOperation] = []

    for track in job.tracks:
        operation = _plan_track(track, job, resolved_output)
        operations.append(operation)
        if operation.planned_action == ACTION_SKIP_BLOCKED:
            blocked_tracks.append(operation)

    duplicate_names = _duplicate_output_names(operations)
    duplicate_output_filenames = sorted({operation.expected_output_filename for operation in duplicate_names})
    for operation in duplicate_names:
        operation.destination_filename_conflict = True
        operation.errors.append(
            f'Destination filename conflicts with another track: "{operation.expected_output_filename}"'
        )

    for operation in operations:
        if operation.planned_action == ACTION_SKIP_BLOCKED:
            continue
        for error in operation.errors:
            if error.startswith("Dangerous output filename"):
                dangerous_output_paths.append(operation.expected_output_filename)
        if operation.destination_path and not _is_relative_to(
            Path(operation.destination_path).resolve(strict=False), resolved_output
        ):
            operation.errors.append("Destination path escapes the output directory.")
            dangerous_output_paths.append(operation.destination_path)
        if operation.errors or errors:
            operation.planned_action = ACTION_ERROR

    output_dir_valid = not errors
    return DryRunPlan(
        output_dir=str(resolved_output),
        output_dir_valid=output_dir_valid,
        output_dir_exists=output_dir_exists,
        output_dir_overwrites_source_dir=output_overwrites_source,
        output_dir_inside_source_dir=output_inside_source,
        dangerous_output_paths=sorted(set(dangerous_output_paths)),
        duplicate_output_filenames=duplicate_output_filenames,
        warnings=warnings,
        errors=errors,
        operations=operations,
        blocked_tracks=blocked_tracks,
    )


def _plan_track(track: TrackEntry, job: PlaylistJob, output_dir: Path) -> TrackOperation:
    source_path = Path(track.source_path) if track.source_path else Path()
    source_exists = bool(track.source_path) and source_path.is_file()
    warnings = list(track.warnings)
    errors: list[str] = []

    if track.is_blocked:
        errors.extend(track.blockers)
        return TrackOperation(
            position=track.position,
            source_path=track.source_path,
            destination_path=None,
            planned_action=ACTION_SKIP_BLOCKED,
            source_exists=source_exists,
            destination_filename_conflict=False,
            expected_output_filename="",
            warnings=warnings,
            errors=errors,
        )

    filename, filename_errors, filename_warnings = _expected_filename(track, job)
    errors.extend(filename_errors)
    warnings.extend(filename_warnings)

    destination_path = str(output_dir / filename) if filename and not filename_errors else None
    if not source_exists:
        errors.append(f"Source file does not exist on disk: {track.source_path}")

    action = _planned_action(track, job)
    if errors:
        action = ACTION_ERROR

    return TrackOperation(
        position=track.position,
        source_path=track.source_path,
        destination_path=destination_path,
        planned_action=action,
        source_exists=source_exists,
        destination_filename_conflict=False,
        expected_output_filename=filename,
        warnings=warnings,
        errors=errors,
    )


def _expected_filename(track: TrackEntry, job: PlaylistJob) -> tuple[str, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    source_suffix = Path(track.source_path).suffix
    target_suffix = _target_suffix(job, source_suffix)

    if track.output_filename:
        filename = track.output_filename.strip()
        safety_error = _unsafe_explicit_filename_reason(filename)
        if safety_error:
            return filename, [safety_error], warnings
        if not Path(filename).suffix and target_suffix:
            filename = f"{filename}{target_suffix}"
        elif target_suffix and Path(filename).suffix.lower() != target_suffix.lower():
            warnings.append(
                f'Explicit output filename extension differs from target format "{target_suffix}".'
            )
        return filename, errors, warnings

    base = track.filename_hint or _format_filename_template(track, job)
    base = _sanitize_generated_filename(base)
    if not base:
        base = f"{track.position:02d}"

    if target_suffix:
        filename = f"{base}{target_suffix}"
    else:
        filename = base
        warnings.append("Source extension is unknown; planned output filename has no extension.")

    return filename, errors, warnings


def _format_filename_template(track: TrackEntry, job: PlaylistJob) -> str:
    template = job.settings.filename_template or "{position:02d}"
    values: dict[str, Any] = {
        "position": track.position,
        "artist": track.artist or "Unknown Artist",
        "title": track.title or Path(track.source_path).stem or f"Track {track.position}",
        "album": track.album or "",
        "albumartist": track.albumartist or track.artist or "",
        "tracknumber": track.tracknumber or "",
        "date": track.date or track.year or "",
        "year": track.year or "",
        "genre": track.genre or "",
    }
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError) as exc:
        return f"{track.position:02d} - {values['artist']} - {values['title']} ({exc})"


def _planned_action(track: TrackEntry, job: PlaylistJob) -> str:
    output_format = (job.settings.output_format or "source").strip().lower().lstrip(".")
    if not output_format or output_format == "source":
        return ACTION_COPY

    source_format = Path(track.source_path).suffix.lower().lstrip(".")
    if source_format == output_format:
        return ACTION_COPY
    return ACTION_CONVERT


def _target_suffix(job: PlaylistJob, source_suffix: str) -> str:
    output_format = (job.settings.output_format or "source").strip().lower().lstrip(".")
    if not output_format or output_format == "source":
        return source_suffix
    return f".{output_format}"


def _sanitize_generated_filename(value: str) -> str:
    filename = _INVALID_FILENAME_CHARS_RE.sub("_", value.strip())
    filename = _SPACE_RE.sub(" ", filename).strip(" .")
    while "__" in filename:
        filename = filename.replace("__", "_")
    if _is_reserved_windows_name(filename):
        return f"_{filename}"
    return filename


def _unsafe_explicit_filename_reason(filename: str) -> str | None:
    if not filename:
        return "Explicit output filename is empty."
    if Path(filename).is_absolute() or PureWindowsPath(filename).is_absolute():
        return f"Dangerous output filename is absolute: {filename}"
    if Path(filename).name != filename or "\\" in filename or "/" in filename:
        return f"Dangerous output filename must be a filename, not a path: {filename}"
    if ".." in Path(filename).parts:
        return f"Dangerous output filename contains parent traversal: {filename}"
    if _INVALID_FILENAME_CHARS_RE.search(filename):
        return f"Dangerous output filename contains invalid filesystem characters: {filename}"
    if filename in {".", ".."} or not filename.strip(" ."):
        return f"Dangerous output filename is not usable: {filename}"
    if _is_reserved_windows_name(filename):
        return f"Dangerous output filename uses a reserved Windows device name: {filename}"
    return None


def _unsafe_output_dir_reason(output_dir: Path) -> str | None:
    for part in output_dir.parts:
        if part in {output_dir.anchor, output_dir.drive, "\\", "/"}:
            continue
        if part in {".", ".."}:
            continue
        if _INVALID_FILENAME_CHARS_RE.search(part):
            return f"Output directory contains invalid filesystem characters: {output_dir}"
        if _is_reserved_windows_name(part):
            return f"Output directory contains a reserved Windows device name: {output_dir}"
    return None


def _is_reserved_windows_name(filename: str) -> bool:
    stem = filename.split(".", 1)[0].upper()
    return stem in _RESERVED_WINDOWS_NAMES


def _duplicate_output_names(operations: list[TrackOperation]) -> list[TrackOperation]:
    buckets: dict[str, list[TrackOperation]] = {}
    for operation in operations:
        if operation.planned_action == ACTION_SKIP_BLOCKED or not operation.expected_output_filename:
            continue
        key = operation.expected_output_filename.casefold()
        buckets.setdefault(key, []).append(operation)

    duplicates: list[TrackOperation] = []
    for bucket in buckets.values():
        if len(bucket) > 1:
            duplicates.extend(bucket)
    return duplicates


def _output_overwrites_source_dir(output_dir: Path, tracks: list[TrackEntry]) -> bool:
    for track in tracks:
        if not track.source_path:
            continue
        source_parent = Path(track.source_path).resolve(strict=False).parent
        if output_dir == source_parent:
            return True
    return False


def _output_inside_source_dir(output_dir: Path, tracks: list[TrackEntry]) -> bool:
    for track in tracks:
        if not track.source_path:
            continue
        source_parent = Path(track.source_path).resolve(strict=False).parent
        if output_dir != source_parent and _is_relative_to(output_dir, source_parent):
            return True
    return False


def _is_filesystem_root(path: Path) -> bool:
    return path.parent == path


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
