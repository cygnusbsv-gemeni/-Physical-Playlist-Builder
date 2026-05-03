"""Resume preflight state discovery and comparison planning."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from ppb.planner import ACTION_CONVERT, ACTION_COPY, DryRunPlan, TrackOperation


EXPORT_SESSION_FILENAME = "export_session.json"
EXPORT_REPORT_FILENAME = "export_report.json"
SESSION_FORMAT = "physical_playlist_export_session.v1"
REPORT_FORMAT = "physical_playlist_export_report.v1"
PRIOR_SUCCESS_STATUSES = {"copied", "converted"}
UNSAFE_POST_PROCESSING_STATUSES = {"failed", "ffmpeg_missing"}
UNKNOWN = "unknown"


@dataclass
class ResumeState:
    """Structured result for resume preflight discovery."""

    requested: bool
    final_output_dir: str
    session_path: str
    report_path: str
    session_found: bool = False
    report_found: bool = False
    session_data: dict[str, Any] | None = None
    report_data: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def state_found(self) -> bool:
        """Return true when at least one prior state file was loaded as JSON object."""

        return self.session_data is not None or self.report_data is not None

    def to_report_metadata(self) -> dict[str, Any]:
        """Return stable report metadata without embedding prior state."""

        return {
            "resume_requested": self.requested,
            "resume_state_found": self.state_found,
            "resume_session_found": self.session_found,
            "resume_report_found": self.report_found,
            "resume_warnings": list(self.warnings),
            "resume_errors": list(self.errors),
            "resume_comparison": self.comparison,
        }


def discover_resume_state(final_output_dir: Path | str) -> ResumeState:
    """Detect and validate prior export metadata inside one final output folder.

    This intentionally loads prior state for reporting only. It never searches
    outside ``final_output_dir`` and never returns prior paths for processing.
    """

    output_dir = Path(final_output_dir).expanduser().resolve(strict=False)
    state = ResumeState(
        requested=True,
        final_output_dir=str(output_dir),
        session_path=str(output_dir / EXPORT_SESSION_FILENAME),
        report_path=str(output_dir / EXPORT_REPORT_FILENAME),
    )

    if not output_dir.exists():
        state.errors.append(f"Resume output folder does not exist: {output_dir}")
        return state
    if not output_dir.is_dir():
        state.errors.append(f"Resume output path is not a directory: {output_dir}")
        return state

    session_path = output_dir / EXPORT_SESSION_FILENAME
    report_path = output_dir / EXPORT_REPORT_FILENAME

    state.session_found = session_path.is_file()
    state.report_found = report_path.is_file()

    state.session_data = _load_json_object(
        session_path,
        label=EXPORT_SESSION_FILENAME,
        warnings=state.warnings,
        errors=state.errors,
    )
    state.report_data = _load_json_object(
        report_path,
        label=EXPORT_REPORT_FILENAME,
        warnings=state.warnings,
        errors=state.errors,
    )

    if state.session_data is not None:
        _validate_prior_state_paths(
            state.session_data,
            expected_format=SESSION_FORMAT,
            label=EXPORT_SESSION_FILENAME,
            output_dir=output_dir,
            output_dir_fields=[
                ("output.final_path", ("output", "final_path")),
                ("handoff.final_output_dir", ("handoff", "final_output_dir")),
                ("final_output_dir", ("final_output_dir",)),
            ],
            generated_path_fields=[
                ("handoff.copy_report_path", ("handoff", "copy_report_path")),
            ],
            warnings=state.warnings,
            errors=state.errors,
        )

    if state.report_data is not None:
        _validate_prior_state_paths(
            state.report_data,
            expected_format=REPORT_FORMAT,
            label=EXPORT_REPORT_FILENAME,
            output_dir=output_dir,
            output_dir_fields=[
                ("final_output_dir", ("final_output_dir",)),
                ("output.final_path", ("output", "final_path")),
            ],
            generated_path_fields=[
                ("m3u_path", ("m3u_path",)),
                ("report_txt_path", ("report_txt_path",)),
                ("log_path", ("log_path",)),
            ],
            warnings=state.warnings,
            errors=state.errors,
        )

    if not state.session_found:
        state.warnings.append(f"{EXPORT_SESSION_FILENAME} was not found in {output_dir}.")
    if not state.report_found:
        state.warnings.append(f"{EXPORT_REPORT_FILENAME} was not found in {output_dir}.")
    if state.state_found:
        state.warnings.append(
            "B12.2 resume is comparison-only; prior track statuses are not reused yet."
        )

    return state


def build_resume_comparison(
    *,
    resume_state: ResumeState,
    plan: DryRunPlan,
    final_output_dir: Path | str,
) -> dict[str, Any]:
    """Compare the current operation plan with prior report results.

    B12.2 produces conservative planning data only. The returned candidates are
    never consumed by copy/conversion/loudness/tag/M3U8 execution.
    """

    output_dir = Path(final_output_dir).expanduser().resolve(strict=False)
    warnings: list[str] = []
    prior_tracks = _prior_report_tracks(resume_state.report_data, warnings)
    prior_index = _build_prior_index(prior_tracks, output_dir)
    candidates = [
        _build_candidate(operation, index, prior_index, output_dir)
        for index, operation in enumerate(plan.operations, start=1)
    ]
    totals = _comparison_totals(candidates)

    if not resume_state.report_found:
        warnings.append(
            f"{EXPORT_REPORT_FILENAME} is missing; resume comparison has no prior track results."
        )
    elif resume_state.report_data is None:
        warnings.append(
            f"{EXPORT_REPORT_FILENAME} was not loaded; resume comparison has no prior track results."
        )
    elif not prior_tracks:
        warnings.append(
            f"{EXPORT_REPORT_FILENAME} has no usable tracks list for resume comparison."
        )

    for warning in warnings:
        if warning not in resume_state.warnings:
            resume_state.warnings.append(warning)

    comparison = {
        "mode": "comparison_only",
        "applies_to_execution": False,
        "final_output_dir": str(output_dir),
        "totals": totals,
        "warnings": warnings,
        "candidates": candidates,
    }
    resume_state.comparison = comparison
    return comparison


def _load_json_object(
    path: Path,
    *,
    label: str,
    warnings: list[str],
    errors: list[str],
) -> dict[str, Any] | None:
    if not path.is_file():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        errors.append(f"{label} could not be read: {exc}")
        return None
    except JSONDecodeError as exc:
        errors.append(f"{label} contains malformed JSON: {exc.msg} at line {exc.lineno}.")
        return None

    if not isinstance(data, dict):
        errors.append(f"{label} has unexpected structure: top-level JSON value is not an object.")
        return None

    return data


def _prior_report_tracks(
    report_data: dict[str, Any] | None,
    warnings: list[str],
) -> list[dict[str, Any]]:
    if report_data is None:
        return []

    tracks = report_data.get("tracks")
    if tracks is None:
        warnings.append(f"{EXPORT_REPORT_FILENAME} does not contain a tracks list.")
        return []
    if not isinstance(tracks, list):
        warnings.append(f"{EXPORT_REPORT_FILENAME} tracks field is not a list.")
        return []

    usable_tracks: list[dict[str, Any]] = []
    for index, value in enumerate(tracks, start=1):
        if isinstance(value, dict):
            usable_tracks.append(value)
        else:
            warnings.append(
                f"{EXPORT_REPORT_FILENAME} track result #{index} is not an object and was ignored."
            )
    return usable_tracks


def _build_prior_index(
    prior_tracks: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, Any]:
    by_position: dict[int, list[dict[str, Any]]] = {}
    by_output_filename: dict[str, list[dict[str, Any]]] = {}
    by_output_path: dict[str, list[dict[str, Any]]] = {}
    by_source_path: dict[str, list[dict[str, Any]]] = {}

    for track in prior_tracks:
        position = _int_or_none(track.get("position"))
        if position is not None:
            by_position.setdefault(position, []).append(track)

        filename = _text_or_none(track.get("expected_output_filename"))
        if filename:
            by_output_filename.setdefault(filename.casefold(), []).append(track)

        prior_path = _trusted_prior_output_path(track, output_dir)
        if prior_path is not None:
            by_output_path.setdefault(_normalize_path_key(prior_path), []).append(track)
            by_output_filename.setdefault(prior_path.name.casefold(), []).append(track)

        source_key = _safe_source_path_key(track.get("source_path"))
        if source_key is not None:
            by_source_path.setdefault(source_key, []).append(track)

    return {
        "by_position": by_position,
        "by_output_filename": by_output_filename,
        "by_output_path": by_output_path,
        "by_source_path": by_source_path,
    }


def _build_candidate(
    operation: TrackOperation,
    index: int,
    prior_index: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    prior, match_method, match_warning = _match_prior_track(operation, prior_index)
    current_destination = _trusted_current_destination(operation.destination_path, output_dir)
    prior_output_raw = _prior_output_path_value(prior) if prior is not None else None
    prior_output_trusted = _trusted_prior_output_path(prior, output_dir) if prior is not None else None
    existing_path = current_destination or prior_output_trusted
    existing_output_file_found = bool(existing_path and existing_path.is_file())
    existing_output_size = _file_size(existing_path) if existing_output_file_found else None
    current_source_size = _file_size(Path(operation.source_path)) if operation.source_path else None
    file_size_match = _file_size_match(
        operation=operation,
        source_size=current_source_size,
        existing_output_size=existing_output_size,
    )

    safe, reason = _candidate_decision(
        operation=operation,
        prior=prior,
        current_destination=current_destination,
        prior_output_raw=prior_output_raw,
        prior_output_trusted=prior_output_trusted,
        existing_output_file_found=existing_output_file_found,
        existing_output_size=existing_output_size,
        file_size_match=file_size_match,
        match_warning=match_warning,
    )

    return {
        "track_position": operation.position,
        "track_index": index,
        "current_planned_action": operation.planned_action,
        "current_source_path": operation.source_path,
        "current_planned_output_filename": operation.expected_output_filename or None,
        "current_planned_output_path": operation.destination_path,
        "prior_match_method": match_method,
        "prior_status": _text_or_none(prior.get("status")) if prior is not None else None,
        "prior_output_path": prior_output_raw,
        "existing_output_file_found": existing_output_file_found,
        "file_size_match": file_size_match,
        "safe_to_reuse_candidate": safe,
        "reason": reason,
    }


def _match_prior_track(
    operation: TrackOperation,
    prior_index: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    matches = prior_index["by_position"].get(operation.position) or []
    if len(matches) == 1:
        prior = matches[0]
        warning = _stable_field_mismatch_warning(operation, prior)
        return prior, "position", warning
    if len(matches) > 1:
        return None, "position", "multiple prior results matched this track position."

    destination_key = _safe_destination_key(operation.destination_path)
    if destination_key is not None:
        matches = prior_index["by_output_path"].get(destination_key) or []
        if len(matches) == 1:
            return matches[0], "planned_output_path", None
        if len(matches) > 1:
            return None, "planned_output_path", "multiple prior results matched this output path."

    if operation.expected_output_filename:
        filename_key = operation.expected_output_filename.casefold()
        matches = prior_index["by_output_filename"].get(filename_key) or []
        if len(matches) == 1:
            return matches[0], "planned_output_filename", None
        if len(matches) > 1:
            return (
                None,
                "planned_output_filename",
                "multiple prior results matched this output filename.",
            )

    source_key = _safe_source_path_key(operation.source_path)
    if source_key is not None:
        matches = prior_index["by_source_path"].get(source_key) or []
        if len(matches) == 1:
            return matches[0], "source_path", None
        if len(matches) > 1:
            return None, "source_path", "multiple prior results matched this source path."

    return None, None, None


def _candidate_decision(
    *,
    operation: TrackOperation,
    prior: dict[str, Any] | None,
    current_destination: Path | None,
    prior_output_raw: str | None,
    prior_output_trusted: Path | None,
    existing_output_file_found: bool,
    existing_output_size: int | None,
    file_size_match: bool | str,
    match_warning: str | None,
) -> tuple[bool, str]:
    if match_warning is not None:
        return False, match_warning
    if prior is None:
        return False, "no matching prior track result was found."
    if operation.planned_action not in {ACTION_COPY, ACTION_CONVERT}:
        return False, f"current planned action is not reusable: {operation.planned_action}."
    if operation.errors:
        return False, "current operation has planning errors."
    if current_destination is None:
        return False, "current planned output path is missing or outside final output folder."
    if prior_output_raw and prior_output_trusted is None:
        return False, "prior output path is outside the selected final output folder."

    prior_status = _text_or_none(prior.get("status"))
    if prior_status not in PRIOR_SUCCESS_STATUSES:
        return False, f"prior status is not a successful export status: {prior_status or '(missing)'}."

    prior_action = _text_or_none(prior.get("planned_action"))
    if prior_action and prior_action != operation.planned_action:
        return False, (
            f"prior planned action {prior_action!r} differs from current "
            f"{operation.planned_action!r}."
        )

    unsafe_post_processing = _unsafe_post_processing_reason(prior)
    if unsafe_post_processing is not None:
        return False, unsafe_post_processing

    if not existing_output_file_found:
        return False, "planned output file does not exist in the final output folder."

    if operation.planned_action == ACTION_COPY:
        if file_size_match is True:
            return (
                True,
                "prior copy succeeded and existing output size matches current source size.",
            )
        if file_size_match is False:
            return False, "existing output size differs from current source file size."
        return False, "file size comparison is unknown for the current copy candidate."

    if operation.planned_action == ACTION_CONVERT:
        if prior_status != "converted":
            return False, f"current conversion does not match prior status {prior_status!r}."
        if existing_output_size is None or existing_output_size <= 0:
            return False, "existing converted output file is empty or its size is unknown."
        return (
            True,
            "prior conversion succeeded and existing output file is present; "
            "converted output size is not compared in B12.2.",
        )

    return False, "resume comparison is conservative for this operation type."


def _comparison_totals(candidates: list[dict[str, Any]]) -> dict[str, int]:
    totals = {
        "candidates_total": len(candidates),
        "safe_to_reuse_candidates": 0,
        "unsafe_candidates": 0,
        "missing_prior_results": 0,
        "existing_output_files": 0,
        "size_matches": 0,
        "size_mismatches": 0,
    }
    for candidate in candidates:
        if candidate.get("safe_to_reuse_candidate"):
            totals["safe_to_reuse_candidates"] += 1
        else:
            totals["unsafe_candidates"] += 1
        if candidate.get("prior_status") is None:
            totals["missing_prior_results"] += 1
        if candidate.get("existing_output_file_found"):
            totals["existing_output_files"] += 1
        if candidate.get("file_size_match") is True:
            totals["size_matches"] += 1
        elif candidate.get("file_size_match") is False:
            totals["size_mismatches"] += 1
    return totals


def _file_size_match(
    *,
    operation: TrackOperation,
    source_size: int | None,
    existing_output_size: int | None,
) -> bool | str:
    if operation.planned_action != ACTION_COPY:
        return UNKNOWN
    if source_size is None or existing_output_size is None:
        return UNKNOWN
    return source_size == existing_output_size


def _unsafe_post_processing_reason(prior: dict[str, Any]) -> str | None:
    loudness_status = _text_or_none(prior.get("loudness_normalization_status"))
    if loudness_status in UNSAFE_POST_PROCESSING_STATUSES:
        return f"prior loudness normalization status is unsafe: {loudness_status}."

    post_loudness_status = _text_or_none(prior.get("post_loudness_status"))
    if post_loudness_status in UNSAFE_POST_PROCESSING_STATUSES:
        return f"prior post-normalization verification status is unsafe: {post_loudness_status}."

    tag_status = _text_or_none(prior.get("tag_status") or prior.get("tags_status"))
    if tag_status in UNSAFE_POST_PROCESSING_STATUSES:
        return f"prior tag-writing status is unsafe: {tag_status}."
    return None


def _stable_field_mismatch_warning(operation: TrackOperation, prior: dict[str, Any]) -> str | None:
    prior_filename = _text_or_none(prior.get("expected_output_filename"))
    if (
        prior_filename
        and operation.expected_output_filename
        and prior_filename.casefold() != operation.expected_output_filename.casefold()
    ):
        return "prior result matched by position but planned output filename changed."

    prior_source = _safe_source_path_key(prior.get("source_path"))
    current_source = _safe_source_path_key(operation.source_path)
    if prior_source and current_source and prior_source != current_source:
        return "prior result matched by position but source path changed."
    return None


def _trusted_current_destination(value: Any, output_dir: Path) -> Path | None:
    if value in (None, ""):
        return None
    try:
        candidate = Path(str(value)).expanduser().resolve(strict=False)
    except (OSError, ValueError):
        return None
    if candidate == output_dir or not _is_relative_to(candidate, output_dir):
        return None
    return candidate


def _trusted_prior_output_path(track: dict[str, Any] | None, output_dir: Path) -> Path | None:
    value = _prior_output_path_value(track)
    if value in (None, ""):
        return None
    try:
        candidate = Path(str(value)).expanduser().resolve(strict=False)
    except (OSError, ValueError):
        return None
    if candidate == output_dir or not _is_relative_to(candidate, output_dir):
        return None
    return candidate


def _prior_output_path_value(track: dict[str, Any] | None) -> str | None:
    if not isinstance(track, dict):
        return None
    for key in ("destination_path", "normalized_output_path", "output_path"):
        value = _text_or_none(track.get(key))
        if value:
            return value
    return None


def _safe_destination_key(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return _normalize_path_key(Path(str(value)))
    except (OSError, ValueError):
        return None


def _safe_source_path_key(value: Any) -> str | None:
    value = _text_or_none(value)
    if not value:
        return None
    try:
        return _normalize_path_key(Path(value))
    except (OSError, ValueError):
        return None


def _file_size(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        return path.stat().st_size if path.is_file() else None
    except OSError:
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _validate_prior_state_paths(
    data: dict[str, Any],
    *,
    expected_format: str,
    label: str,
    output_dir: Path,
    output_dir_fields: list[tuple[str, tuple[str, ...]]],
    generated_path_fields: list[tuple[str, tuple[str, ...]]],
    warnings: list[str],
    errors: list[str],
) -> None:
    value = data.get("format")
    if value is None:
        warnings.append(f"{label} does not declare a format.")
    elif value != expected_format:
        warnings.append(f"{label} format is {value!r}, expected {expected_format!r}.")

    found_output_path = False
    for display_name, keys in output_dir_fields:
        path_value = _dig(data, keys)
        if path_value is None:
            continue
        found_output_path = True
        if not _path_equivalent(path_value, output_dir):
            errors.append(
                f"{label} {display_name} does not match selected final output folder: "
                f"{path_value!r}"
            )

    if not found_output_path:
        warnings.append(f"{label} has no prior final_output_dir field to validate.")

    for display_name, keys in generated_path_fields:
        path_value = _dig(data, keys)
        if path_value in (None, ""):
            continue
        if not _path_inside_output(path_value, output_dir):
            errors.append(
                f"{label} {display_name} points outside selected final output folder: "
                f"{path_value!r}"
            )


def _dig(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _path_equivalent(value: Any, expected: Path) -> bool:
    try:
        return _normalize_path_key(Path(str(value))) == _normalize_path_key(expected)
    except (OSError, ValueError):
        return False


def _path_inside_output(value: Any, output_dir: Path) -> bool:
    try:
        candidate = Path(str(value)).expanduser().resolve(strict=False)
    except (OSError, ValueError):
        return False
    candidate_key = _normalize_path_key(candidate)
    output_key = _normalize_path_key(output_dir)
    if candidate_key == output_key:
        return False
    try:
        return os.path.commonpath([candidate_key, output_key]) == output_key
    except ValueError:
        return False


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _normalize_path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path.expanduser().resolve(strict=False))))
