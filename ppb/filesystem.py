"""Safe filesystem helpers for output-folder creation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ppb.contract import PlaylistJob
from ppb.planner import DryRunPlan
from ppb.report import write_export_session


EXPORT_SESSION_FILENAME = "export_session.json"

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


class OutputFolderError(RuntimeError):
    """Raised when the requested output folder cannot be created safely."""


@dataclass
class OutputFolderTarget:
    """Resolved output folder target before filesystem writes happen."""

    requested_out: str
    final_output_dir: Path
    create_subfolder: bool


@dataclass
class OutputFolderResult:
    """Result of creating the safe output folder and session file."""

    requested_out: str
    final_output_dir: str
    export_session_path: str
    created_output_dir: bool
    existing_non_empty_allowed: bool


def sanitize_windows_filename(value: str, fallback: str = "playlist") -> str:
    """Return a Windows-safe leaf name for generated folders."""

    filename = _INVALID_FILENAME_CHARS_RE.sub("_", value.strip())
    filename = _SPACE_RE.sub(" ", filename).strip(" .")
    while "__" in filename:
        filename = filename.replace("__", "_")
    if not filename:
        filename = fallback
    if _is_reserved_windows_name(filename):
        filename = f"_{filename}"
    return filename[:180].rstrip(" .") or fallback


def build_output_folder_target(
    requested_out: Path | str,
    playlist_name: str,
    *,
    create_subfolder: bool = True,
    timestamp: datetime | None = None,
) -> OutputFolderTarget:
    """Compute the final output folder path without creating it."""

    requested_text = str(requested_out).strip()
    if not requested_text:
        raise OutputFolderError("Output directory path is empty.")

    requested_path = Path(requested_out).expanduser()
    requested_resolved = requested_path.resolve(strict=False)
    if _is_filesystem_root(requested_resolved):
        raise OutputFolderError(f"Output directory must not be a filesystem root: {requested_resolved}")

    final_output_dir = requested_path
    if create_subfolder:
        moment = timestamp or datetime.now()
        safe_playlist_name = sanitize_windows_filename(playlist_name)
        final_output_dir = requested_path / f"{safe_playlist_name}_{moment:%Y%m%d_%H%M%S}"

    return OutputFolderTarget(
        requested_out=str(requested_path),
        final_output_dir=final_output_dir.resolve(strict=False),
        create_subfolder=create_subfolder,
    )


def create_output_folder(
    *,
    job: PlaylistJob,
    plan: DryRunPlan,
    target: OutputFolderTarget,
    overwrite: bool = False,
    input_path: Path | str | None = None,
    input_type: str | None = None,
) -> OutputFolderResult:
    """Create the output folder and session JSON without copying audio files."""

    if plan.errors:
        raise OutputFolderError("; ".join(plan.errors))

    output_dir = Path(plan.output_dir)
    existed_before = output_dir.exists()
    if existed_before and not output_dir.is_dir():
        raise OutputFolderError(f"Output path exists but is not a directory: {output_dir}")

    existing_non_empty = existed_before and any(output_dir.iterdir())
    if existing_non_empty and not overwrite:
        raise OutputFolderError(
            f"Output folder already exists and is not empty: {output_dir}. "
            "Pass --overwrite to allow writing the session file there."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    session_path = output_dir / EXPORT_SESSION_FILENAME
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
        created_output_dir=not existed_before,
        existing_non_empty_allowed=existing_non_empty and overwrite,
    )


def _is_reserved_windows_name(filename: str) -> bool:
    stem = filename.split(".", 1)[0].upper()
    return stem in _RESERVED_WINDOWS_NAMES


def _is_filesystem_root(path: Path) -> bool:
    return path.parent == path
