"""Generic input readers for neutral physical playlist jobs."""

from __future__ import annotations

import csv
import json
import locale
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from ppb.contract import DEFAULT_FILENAME_TEMPLATE, SUPPORTED_FORMAT
from ppb.validator import ValidationResult, validate_job


SUPPORTED_INPUT_TYPES = ("json", "txt", "csv", "m3u", "m3u8")
AUTO_INPUT_TYPE = "auto"

_EXTENSION_TO_TYPE = {
    ".json": "json",
    ".txt": "txt",
    ".csv": "csv",
    ".m3u": "m3u",
    ".m3u8": "m3u8",
}

_CSV_STRING_FIELDS = (
    "output_filename",
    "filename_hint",
    "title",
    "artist",
    "album",
    "albumartist",
    "tracknumber",
    "date",
    "year",
    "genre",
    "codec",
    "tag_format",
)
_CSV_FLOAT_FIELDS = ("duration_sec",)
_CSV_INT_FIELDS = (
    "bitrate_kbps",
    "sample_rate_hz",
    "channels",
    "bit_depth",
)
_EXTINF_RE = re.compile(r"^#EXTINF:(?P<duration>[^,]*),(?P<label>.*)$", re.IGNORECASE)


@dataclass
class InputReadResult:
    """Validated input plus metadata about how it was read."""

    input_path: Path
    input_type: str
    raw_job: dict[str, Any]
    validation: ValidationResult
    converted: bool = False


class InputReadError(Exception):
    """Raised when an input file cannot be read as a supported playlist input."""

    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def detect_input_type(path: Path, requested: str = AUTO_INPUT_TYPE) -> str:
    """Resolve an explicit or extension-based input type."""

    requested = requested.lower()
    if requested != AUTO_INPUT_TYPE:
        if requested not in SUPPORTED_INPUT_TYPES:
            supported = ", ".join((AUTO_INPUT_TYPE, *SUPPORTED_INPUT_TYPES))
            raise InputReadError(f"Unsupported input type {requested!r}. Use one of: {supported}.")
        return requested

    detected = _EXTENSION_TO_TYPE.get(path.suffix.lower())
    if detected:
        return detected

    supported_ext = ", ".join(sorted(_EXTENSION_TO_TYPE))
    raise InputReadError(
        f"Could not detect input type from extension {path.suffix!r}. "
        f"Supported extensions: {supported_ext}. Use --input-type to override."
    )


def read_playlist_input(
    input_path: Path | str,
    input_type: str = AUTO_INPUT_TYPE,
    strict: bool = False,
) -> InputReadResult:
    """Read any supported input and validate it as ``PlaylistJob``."""

    path = Path(input_path)
    _require_readable_file(path)
    detected_type = detect_input_type(path, input_type)

    if detected_type == "json":
        raw_job = _read_json_job(path)
        converted = False
    elif detected_type == "txt":
        raw_job = _read_txt_job(path, detected_type)
        converted = True
    elif detected_type == "csv":
        raw_job = _read_csv_job(path, detected_type)
        converted = True
    elif detected_type in {"m3u", "m3u8"}:
        raw_job = _read_m3u_job(path, detected_type)
        converted = True
    else:
        raise InputReadError(f"Unsupported input type: {detected_type}")

    validation = validate_job(raw_job, strict=strict)
    return InputReadResult(
        input_path=path,
        input_type=detected_type,
        raw_job=raw_job,
        validation=validation,
        converted=converted,
    )


def _require_readable_file(path: Path) -> None:
    if not path.exists():
        raise InputReadError(f"Input file not found: {path}", exit_code=1)
    if not path.is_file():
        raise InputReadError(f"Input path is not a file: {path}", exit_code=1)


def _read_json_job(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            raw = json.load(fh)
    except json.JSONDecodeError as exc:
        raise InputReadError(f"Could not parse JSON: {exc}") from exc
    return raw


def _read_txt_job(path: Path, input_type: str) -> dict[str, Any]:
    tracks: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        source_path = line.strip()
        if not source_path or source_path.startswith("#"):
            continue
        tracks.append(_track_from_path(source_path, path.parent, len(tracks) + 1))
    return _canonical_job(path, input_type, tracks)


def _read_csv_job(path: Path, input_type: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise InputReadError("CSV input is empty.")

    dialect = _sniff_csv_dialect(text)
    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    fieldnames = [name.strip() for name in (reader.fieldnames or []) if name is not None]
    if "source_path" not in fieldnames:
        raise InputReadError('CSV input must contain a "source_path" column.')

    tracks: list[dict[str, Any]] = []
    for row_number, row in enumerate(reader, start=2):
        normalized_row = {
            (key.strip() if key is not None else ""): (value.strip() if value else "")
            for key, value in row.items()
        }
        track = _csv_row_to_track(normalized_row, path.parent, len(tracks) + 1, row_number)
        tracks.append(track)

    return _canonical_job(path, input_type, tracks)


def _sniff_csv_dialect(text: str) -> csv.Dialect:
    sample = text[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;")
    except csv.Error:
        fallback_delimiter = ";" if sample.count(";") > sample.count(",") else ","
        dialect = csv.get_dialect("excel")

        class FallbackDialect(csv.Dialect):
            delimiter = fallback_delimiter
            quotechar = dialect.quotechar
            escapechar = dialect.escapechar
            doublequote = dialect.doublequote
            skipinitialspace = dialect.skipinitialspace
            lineterminator = dialect.lineterminator
            quoting = dialect.quoting

        return FallbackDialect


def _csv_row_to_track(
    row: dict[str, str],
    base_dir: Path,
    implicit_position: int,
    row_number: int,
) -> dict[str, Any]:
    source_path = row.get("source_path", "")
    position = _parse_int(row.get("position", "")) or implicit_position
    track = _track_from_path(source_path, base_dir, position)
    track["producer_meta"]["csv_row"] = row_number

    for field_name in _CSV_STRING_FIELDS:
        value = row.get(field_name, "")
        if value:
            track[field_name] = value

    for field_name in _CSV_FLOAT_FIELDS:
        value = row.get(field_name, "")
        if value:
            track[field_name] = _parse_float_or_original(value)

    for field_name in _CSV_INT_FIELDS:
        value = row.get(field_name, "")
        if value:
            track[field_name] = _parse_int_or_original(value)

    for issue_field in ("warnings", "blockers"):
        value = row.get(issue_field, "")
        if value:
            track[issue_field] = _parse_issue_cell(value)

    return track


def _read_m3u_job(path: Path, input_type: str) -> dict[str, Any]:
    text = _read_m3u_text(path, input_type)
    tracks: list[dict[str, Any]] = []
    pending_extinf: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.upper().startswith("#EXTINF:"):
            pending_extinf = _parse_extinf(line)
            continue
        if line.startswith("#"):
            continue

        track = _track_from_path(line, path.parent, len(tracks) + 1)
        if pending_extinf:
            track.update(pending_extinf)
        tracks.append(track)
        pending_extinf = None

    return _canonical_job(path, input_type, tracks)


def _read_m3u_text(path: Path, input_type: str) -> str:
    if input_type == "m3u8":
        return path.read_text(encoding="utf-8-sig")

    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        fallback_encoding = locale.getpreferredencoding(False) or "utf-8"
        return data.decode(fallback_encoding)


def _parse_extinf(line: str) -> dict[str, Any]:
    match = _EXTINF_RE.match(line)
    if not match:
        return {}

    metadata: dict[str, Any] = {}
    duration = match.group("duration").strip()
    if duration:
        metadata["duration_sec"] = _parse_float_or_original(duration)

    label = match.group("label").strip()
    if not label:
        return metadata

    if " - " in label:
        artist, title = label.split(" - ", 1)
        if artist.strip() and title.strip():
            metadata["artist"] = artist.strip()
            metadata["title"] = title.strip()
            return metadata

    metadata["title"] = label
    return metadata


def _track_from_path(source_path: str, base_dir: Path, position: int) -> dict[str, Any]:
    track: dict[str, Any] = {"position": position}
    if source_path:
        track["source_path"] = _resolve_source_path(source_path, base_dir)
    track["producer_meta"] = {"original_source_path": source_path}
    return track


def _resolve_source_path(source_path: str, base_dir: Path) -> str:
    if _is_absolute_path_string(source_path):
        return source_path
    return str((base_dir / source_path).resolve(strict=False))


def _is_absolute_path_string(value: str) -> bool:
    return (
        Path(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or PurePosixPath(value).is_absolute()
    )


def _canonical_job(path: Path, input_type: str, tracks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "format": SUPPORTED_FORMAT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "playlist": {
            "name": path.stem,
            "description": f"Imported from {input_type.upper()} input.",
            "track_count": len(tracks),
        },
        "settings": _default_settings(),
        "tracks": tracks,
        "summary": {
            "status": "ready",
            "can_run": True,
            "track_count": len(tracks),
        },
        "producer_meta": {
            "source": "ppb.input_readers",
            "source_input_type": input_type,
            "source_input_path": str(path),
            "will_write_files": False,
        },
    }


def _default_settings() -> dict[str, Any]:
    return {
        "output_format": "source",
        "copy_mode": "copy_if_compatible",
        "normalize_loudness": False,
        "target_lufs": -14.0,
        "true_peak_db": -1.0,
        "write_tags": False,
        "generate_m3u8": True,
        "filename_template": DEFAULT_FILENAME_TEMPLATE,
    }


def _parse_issue_cell(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [part.strip() for part in value.split("|") if part.strip()]

    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    if isinstance(parsed, dict):
        message = parsed.get("message")
        return [str(message)] if message else [json.dumps(parsed, ensure_ascii=False)]
    return [str(parsed)]


def _parse_int(value: str) -> int | None:
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _parse_int_or_original(value: str) -> int | str:
    parsed = _parse_int(value)
    return parsed if parsed is not None else value


def _parse_float_or_original(value: str) -> float | str:
    try:
        return float(value)
    except ValueError:
        return value
