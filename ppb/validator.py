"""Validation and normalization for ``physical_playlist_job.v1`` input."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ppb.contract import (
    DEFAULT_FILENAME_TEMPLATE,
    SUPPORTED_FORMAT,
    PlaylistJob,
    PlaylistSettings,
    TrackEntry,
)


SUPPORTED_SCHEMA = SUPPORTED_FORMAT
LEGACY_WARNING = (
    "Legacy playlist_job format detected. Please regenerate the job file using "
    "the canonical physical_playlist_job.v1 contract."
)

_TRACK_OPT_STR = (
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
    "source_status",
    "availability",
)

_TRACK_OPT_NUM = {
    "duration_sec": (int, float),
    "bitrate_kbps": (int,),
    "sample_rate_hz": (int,),
    "channels": (int,),
    "bit_depth": (int,),
}


@dataclass
class TrackIssue:
    """A validation issue attached to a track."""

    position: int
    source_path: str
    level: str
    message: str

    def __str__(self) -> str:
        tag = "BLOCKER" if self.level == "blocker" else "WARNING"
        loc = f"track {self.position}" if self.position else "track ?"
        path = f" [{self.source_path}]" if self.source_path else ""
        return f"  [{tag}] {loc}{path}: {self.message}"


@dataclass
class ValidationResult:
    """Result of validating a playlist job."""

    ok: bool = False
    job: PlaylistJob | None = None
    issues: list[TrackIssue] = field(default_factory=list)
    fatal_errors: list[str] = field(default_factory=list)
    global_warnings: list[str] = field(default_factory=list)
    blocked_count: int = 0
    strict: bool = False
    legacy_input: bool = False

    @property
    def has_blockers(self) -> bool:
        return self.blocked_count > 0

    @property
    def warning_count(self) -> int:
        track_warnings = sum(1 for issue in self.issues if issue.level == "warning")
        return len(self.global_warnings) + track_warnings

    @property
    def blocker_count(self) -> int:
        return sum(1 for issue in self.issues if issue.level == "blocker")

    def summary_lines(self) -> list[str]:
        lines: list[str] = []

        for warning in self.global_warnings:
            lines.append(f"  [WARNING] {warning}")

        if self.fatal_errors:
            for error in self.fatal_errors:
                lines.append(f"  [FATAL] {error}")
            return lines

        for issue in self.issues:
            lines.append(str(issue))

        if not lines:
            lines.append("  All tracks passed validation.")
            return lines

        lines.append("")
        lines.append(f"  Tracks total   : {len(self.job.tracks) if self.job else '?'}")
        lines.append(f"  Blocked tracks : {self.blocked_count}")
        lines.append(f"  Warnings       : {self.warning_count}")
        if self.strict and self.blocked_count:
            lines.append("  Strict mode    : FAIL - blocked tracks found")
        return lines


def validate_job(raw: dict[str, Any], strict: bool = False) -> ValidationResult:
    """Validate raw JSON content and return a neutral ``PlaylistJob``."""

    result = ValidationResult(strict=strict)

    if not isinstance(raw, dict):
        result.fatal_errors.append(
            f"Expected a JSON object at the top level, got {type(raw).__name__}."
        )
        return result

    if "schema" in raw and "format" not in raw:
        raw = _normalize_legacy_job(raw)
        result.legacy_input = True
        result.global_warnings.append(LEGACY_WARNING)

    job_format = raw.get("format")
    if job_format is None:
        result.fatal_errors.append(
            f'Missing required field "format". Expected "{SUPPORTED_FORMAT}".'
        )
        return result
    if job_format != SUPPORTED_FORMAT:
        result.fatal_errors.append(
            f'Unsupported format: "{job_format}". Only "{SUPPORTED_FORMAT}" is accepted.'
        )
        return result

    playlist = raw.get("playlist")
    if not isinstance(playlist, dict):
        result.fatal_errors.append('"playlist" is required and must be an object.')
        return result

    playlist_name = playlist.get("name")
    if not isinstance(playlist_name, str) or not playlist_name.strip():
        result.fatal_errors.append('"playlist.name" is required and must be a non-empty string.')
        return result

    playlist_description = playlist.get("description")
    if playlist_description is not None and not isinstance(playlist_description, str):
        result.global_warnings.append('"playlist.description" should be a string or null; ignored.')
        playlist_description = None

    playlist_track_count = playlist.get("track_count")
    if playlist_track_count is not None:
        if not isinstance(playlist_track_count, int) or isinstance(playlist_track_count, bool):
            result.global_warnings.append('"playlist.track_count" should be an integer; ignored.')
            playlist_track_count = None

    raw_tracks = raw.get("tracks")
    if raw_tracks is None:
        result.fatal_errors.append('"tracks" field is missing. It must be a list.')
        return result
    if not isinstance(raw_tracks, list):
        result.fatal_errors.append(f'"tracks" must be a list, got {type(raw_tracks).__name__}.')
        return result

    if playlist_track_count is not None and playlist_track_count != len(raw_tracks):
        result.global_warnings.append(
            '"playlist.track_count" does not match the actual number of tracks; '
            "using tracks[] length."
        )

    settings = _parse_settings(raw.get("settings"), result)
    if result.fatal_errors:
        return result

    entries = _parse_tracks(raw_tracks, result)
    result.blocked_count = sum(1 for entry in entries if entry.is_blocked)

    validation_warnings = list(result.global_warnings)
    validation_warnings.extend(issue.message for issue in result.issues if issue.level == "warning")
    validation_blockers = [issue.message for issue in result.issues if issue.level == "blocker"]

    result.job = PlaylistJob(
        format=job_format,
        playlist_name=playlist_name.strip(),
        playlist_description=playlist_description,
        playlist_track_count=playlist_track_count,
        settings=settings,
        tracks=entries,
        validation_warnings=validation_warnings,
        validation_blockers=validation_blockers,
        legacy_input=result.legacy_input,
    )

    result.ok = not (strict and result.blocked_count > 0)
    return result


def _normalize_legacy_job(raw: dict[str, Any]) -> dict[str, Any]:
    settings: dict[str, Any] = {}
    for old_key in ("output_format", "normalize_loudness", "write_tags"):
        if old_key in raw:
            settings[old_key] = raw[old_key]

    tracks = raw.get("tracks", [])
    normalized_tracks: list[Any]
    if isinstance(tracks, list):
        normalized_tracks = []
        for raw_track in tracks:
            if not isinstance(raw_track, dict):
                normalized_tracks.append(raw_track)
                continue
            track = dict(raw_track)
            if "display_title" in track and "title" not in track:
                track["title"] = track["display_title"]
            normalized_tracks.append(track)
    else:
        normalized_tracks = tracks

    playlist: dict[str, Any] = {"name": raw.get("playlist_name", "")}
    if "playlist_description" in raw:
        playlist["description"] = raw["playlist_description"]
    if "track_count" in raw:
        playlist["track_count"] = raw["track_count"]

    canonical = {
        "format": raw.get("schema"),
        "playlist": playlist,
        "settings": settings,
        "tracks": normalized_tracks,
    }
    if "summary" in raw:
        canonical["summary"] = raw["summary"]
    if "producer_meta" in raw:
        canonical["producer_meta"] = raw["producer_meta"]
    return canonical


def _parse_settings(raw_settings: Any, result: ValidationResult) -> PlaylistSettings:
    if raw_settings is None:
        result.global_warnings.append('"settings" is missing; using safe defaults.')
        return PlaylistSettings()

    if not isinstance(raw_settings, dict):
        result.fatal_errors.append(f'"settings" must be an object, got {type(raw_settings).__name__}.')
        return PlaylistSettings()

    settings = PlaylistSettings()
    settings.output_format = _read_optional_string(
        raw_settings, "output_format", settings.output_format, result, "settings"
    )
    settings.copy_mode = _read_string(
        raw_settings, "copy_mode", settings.copy_mode, result, "settings"
    )
    settings.normalize_loudness = _read_bool(
        raw_settings, "normalize_loudness", settings.normalize_loudness, result, "settings"
    )
    settings.target_lufs = _read_optional_number(
        raw_settings, "target_lufs", settings.target_lufs, result, "settings"
    )
    settings.true_peak_db = _read_optional_number(
        raw_settings, "true_peak_db", settings.true_peak_db, result, "settings"
    )
    settings.write_tags = _read_bool(
        raw_settings, "write_tags", settings.write_tags, result, "settings"
    )
    settings.generate_m3u8 = _read_bool(
        raw_settings, "generate_m3u8", settings.generate_m3u8, result, "settings"
    )
    settings.filename_template = _read_optional_string(
        raw_settings, "filename_template", DEFAULT_FILENAME_TEMPLATE, result, "settings"
    )
    return settings


def _parse_tracks(raw_tracks: list[Any], result: ValidationResult) -> list[TrackEntry]:
    entries: list[TrackEntry] = []
    seen_positions: dict[int, int] = {}

    for raw_idx, raw_track in enumerate(raw_tracks):
        implicit_position = raw_idx + 1

        if not isinstance(raw_track, dict):
            message = f"Track entry must be a JSON object, got {type(raw_track).__name__}."
            entry = TrackEntry(source_path="", position=implicit_position)
            entry.blockers.append(message)
            entries.append(entry)
            result.issues.append(TrackIssue(implicit_position, "", "blocker", message))
            continue

        position = _read_track_position(raw_track, implicit_position, result)
        if position in seen_positions:
            message = f'Duplicate "position" {position}; keeping tracks[] order.'
            result.issues.append(
                TrackIssue(position, str(raw_track.get("source_path", "")), "warning", message)
            )
        seen_positions[position] = raw_idx

        source_path = raw_track.get("source_path")
        if not isinstance(source_path, str) or not source_path.strip():
            message = (
                '"source_path" is required but missing.'
                if source_path is None
                else '"source_path" must be a non-empty string.'
            )
            entry = TrackEntry(source_path="", position=position)
            entry.blockers.append(message)
            entries.append(entry)
            result.issues.append(TrackIssue(position, "", "blocker", message))
            continue

        entry = TrackEntry(source_path=source_path, position=position)
        _copy_track_optional_fields(raw_track, entry, result)
        _copy_track_issues(raw_track, entry, result)
        entries.append(entry)

    return entries


def _read_track_position(
    raw_track: dict[str, Any], implicit_position: int, result: ValidationResult
) -> int:
    source_path = str(raw_track.get("source_path", ""))
    raw_position = raw_track.get("position")
    if raw_position is None:
        message = f'No "position" field; using implicit order {implicit_position}.'
        result.issues.append(TrackIssue(implicit_position, source_path, "warning", message))
        return implicit_position
    if not isinstance(raw_position, int) or isinstance(raw_position, bool) or raw_position < 1:
        message = f'Invalid "position"; using implicit order {implicit_position}.'
        result.issues.append(TrackIssue(implicit_position, source_path, "warning", message))
        return implicit_position
    return raw_position


def _copy_track_optional_fields(
    raw_track: dict[str, Any], entry: TrackEntry, result: ValidationResult
) -> None:
    for field_name in _TRACK_OPT_STR:
        value = raw_track.get(field_name)
        if value is None:
            continue
        if not isinstance(value, str):
            message = f'"{field_name}" should be a string; ignored.'
            entry.warnings.append(message)
            result.issues.append(TrackIssue(entry.position, entry.source_path, "warning", message))
            continue
        setattr(entry, field_name, value)

    for field_name, expected_types in _TRACK_OPT_NUM.items():
        value = raw_track.get(field_name)
        if value is None:
            continue
        if not isinstance(value, expected_types) or isinstance(value, bool):
            expected = "/".join(item.__name__ for item in expected_types)
            message = f'"{field_name}" should be {expected}; ignored.'
            entry.warnings.append(message)
            result.issues.append(TrackIssue(entry.position, entry.source_path, "warning", message))
            continue
        setattr(entry, field_name, value)

    producer_meta = raw_track.get("producer_meta")
    if producer_meta is None:
        return
    if isinstance(producer_meta, dict):
        entry.producer_meta = dict(producer_meta)
        return
    message = '"producer_meta" should be an object; ignored.'
    entry.warnings.append(message)
    result.issues.append(TrackIssue(entry.position, entry.source_path, "warning", message))


def _copy_track_issues(
    raw_track: dict[str, Any], entry: TrackEntry, result: ValidationResult
) -> None:
    for message in _coerce_issue_messages(raw_track.get("warnings")):
        entry.warnings.append(message)
        result.issues.append(TrackIssue(entry.position, entry.source_path, "warning", message))

    for message in _coerce_issue_messages(raw_track.get("blockers")):
        entry.blockers.append(message)
        result.issues.append(TrackIssue(entry.position, entry.source_path, "blocker", message))


def _coerce_issue_messages(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    messages: list[str] = []
    for item in items:
        if isinstance(item, str):
            messages.append(item)
        elif isinstance(item, dict):
            message = item.get("message")
            if isinstance(message, str) and message:
                messages.append(message)
            else:
                messages.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
        else:
            messages.append(str(item))
    return messages


def _read_string(
    source: dict[str, Any],
    key: str,
    default: str,
    result: ValidationResult,
    prefix: str,
) -> str:
    value = source.get(key, default)
    if isinstance(value, str):
        return value
    result.fatal_errors.append(f'"{prefix}.{key}" must be a string, got {type(value).__name__}.')
    return default


def _read_optional_string(
    source: dict[str, Any],
    key: str,
    default: str | None,
    result: ValidationResult,
    prefix: str,
) -> str | None:
    value = source.get(key, default)
    if value is None or isinstance(value, str):
        return value
    result.fatal_errors.append(
        f'"{prefix}.{key}" must be a string or null, got {type(value).__name__}.'
    )
    return default


def _read_bool(
    source: dict[str, Any],
    key: str,
    default: bool,
    result: ValidationResult,
    prefix: str,
) -> bool:
    value = source.get(key, default)
    if isinstance(value, bool):
        return value
    result.fatal_errors.append(f'"{prefix}.{key}" must be a boolean, got {type(value).__name__}.')
    return default


def _read_optional_number(
    source: dict[str, Any],
    key: str,
    default: float | None,
    result: ValidationResult,
    prefix: str,
) -> float | None:
    value = source.get(key, default)
    if value is None:
        return None
    if (isinstance(value, (int, float)) and not isinstance(value, bool)):
        return float(value)
    result.fatal_errors.append(
        f'"{prefix}.{key}" must be a number or null, got {type(value).__name__}.'
    )
    return default
