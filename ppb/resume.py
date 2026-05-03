"""Resume preflight state discovery for prior export folders."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from json import JSONDecodeError
from pathlib import Path
from typing import Any


EXPORT_SESSION_FILENAME = "export_session.json"
EXPORT_REPORT_FILENAME = "export_report.json"
SESSION_FORMAT = "physical_playlist_export_session.v1"
REPORT_FORMAT = "physical_playlist_export_report.v1"


@dataclass
class ResumeState:
    """Structured result for B12.1 resume preflight discovery."""

    requested: bool
    final_output_dir: str
    session_path: str
    report_path: str
    session_found: bool = False
    report_found: bool = False
    session_data: dict[str, Any] | None = None
    report_data: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def state_found(self) -> bool:
        """Return true when at least one prior state file was loaded as JSON object."""

        return self.session_data is not None or self.report_data is not None

    def to_report_metadata(self) -> dict[str, Any]:
        """Return the stable B12.1 report metadata without embedding prior state."""

        return {
            "resume_requested": self.requested,
            "resume_state_found": self.state_found,
            "resume_session_found": self.session_found,
            "resume_report_found": self.report_found,
            "resume_warnings": list(self.warnings),
            "resume_errors": list(self.errors),
        }


def discover_resume_state(final_output_dir: Path | str) -> ResumeState:
    """Detect and validate prior export metadata inside one final output folder.

    B12.1 intentionally loads prior state for reporting only. It never searches
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
            "B12.1 resume is preflight-only; prior track statuses are not reused yet."
        )

    return state


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


def _normalize_path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path.expanduser().resolve(strict=False))))
