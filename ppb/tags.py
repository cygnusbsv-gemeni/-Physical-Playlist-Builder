"""Safe isolated tag-writing helpers for exported audio files."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


STATUS_WRITTEN = "written"
STATUS_NO_SUPPORTED_FIELDS = "no_supported_fields"
STATUS_UNSUPPORTED_FORMAT = "unsupported_format"
STATUS_OUTSIDE_OUTPUT_DIR = "outside_output_dir"
STATUS_MISSING_FILE = "missing_file"
STATUS_INVALID_OUTPUT_DIR = "invalid_output_dir"
STATUS_INVALID_ID3_VERSION = "invalid_id3_version"
STATUS_DEPENDENCY_MISSING = "dependency_missing"
STATUS_FAILED = "failed"

ID3_VERSION_V23 = "v23"
ID3_VERSION_V24 = "v24"

_ORDERED_FIELDS = (
    "title",
    "artist",
    "album",
    "albumartist",
    "tracknumber",
    "date",
    "year",
    "genre",
)
_MP4_TRACKNUMBER_RE = re.compile(r"^\s*([0-9]+)(?:\s*/\s*([0-9]+))?\s*$")


@dataclass(frozen=True)
class TagWriteResult:
    """Structured result for one exported-file tag writing attempt."""

    success: bool
    status: str
    file_path: str
    tag_format: str | None
    written_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class _NormalizedMetadata:
    values: dict[str, str]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _WritePayload:
    written_fields: list[str]
    warnings: list[str] = field(default_factory=list)


def write_tags_to_exported_file(
    *,
    file_path: Path | str,
    final_output_dir: Path | str,
    metadata: Mapping[str, Any],
    id3_version: str = ID3_VERSION_V24,
) -> TagWriteResult:
    """Write normalized tags to exactly one exported file.

    The target file must already exist inside ``final_output_dir``. The helper
    uses only the provided metadata mapping and never reads source audio files
    or source paths.
    """

    output_dir = Path(final_output_dir).expanduser().resolve(strict=False)
    target = Path(file_path).expanduser().resolve(strict=False)

    if not output_dir.is_dir():
        return TagWriteResult(
            success=False,
            status=STATUS_INVALID_OUTPUT_DIR,
            file_path=str(target),
            tag_format=None,
            error=f"Final output directory does not exist or is not a directory: {output_dir}",
        )

    if not _is_relative_to(target, output_dir) or target == output_dir:
        return TagWriteResult(
            success=False,
            status=STATUS_OUTSIDE_OUTPUT_DIR,
            file_path=str(target),
            tag_format=_tag_format_for_path(target, id3_version=id3_version),
            error=f"Refused to write tags outside final output directory: {target}",
        )

    container_format = _container_format_for_path(target)
    tag_format = _tag_format_for_path(target, id3_version=id3_version)

    if not target.is_file():
        return TagWriteResult(
            success=False,
            status=STATUS_MISSING_FILE,
            file_path=str(target),
            tag_format=tag_format,
            error=f"Exported file does not exist on disk: {target}",
        )

    if container_format is None:
        return TagWriteResult(
            success=False,
            status=STATUS_UNSUPPORTED_FORMAT,
            file_path=str(target),
            tag_format=tag_format,
            error=f"Unsupported tag-writing file type: {target.suffix or '(no extension)'}",
        )

    normalized_id3_version = _normalize_id3_version(id3_version)
    if container_format == "mp3" and normalized_id3_version is None:
        return TagWriteResult(
            success=False,
            status=STATUS_INVALID_ID3_VERSION,
            file_path=str(target),
            tag_format=tag_format,
            error="id3_version must be v23 or v24 for MP3 tag writing.",
        )

    if not isinstance(metadata, Mapping):
        return TagWriteResult(
            success=False,
            status=STATUS_FAILED,
            file_path=str(target),
            tag_format=tag_format,
            error="metadata must be a dict-like mapping.",
        )

    normalized = _normalize_metadata(metadata)
    if not normalized.values:
        return TagWriteResult(
            success=False,
            status=STATUS_NO_SUPPORTED_FIELDS,
            file_path=str(target),
            tag_format=tag_format,
            warnings=normalized.warnings,
            error="No supported metadata fields were provided.",
        )

    try:
        if container_format == "mp3":
            payload = _write_mp3_tags(target, normalized.values, normalized_id3_version or ID3_VERSION_V24)
            tag_format = f"id3v2.{3 if normalized_id3_version == ID3_VERSION_V23 else 4}"
        elif container_format == "flac":
            payload = _write_flac_tags(target, normalized.values)
            tag_format = "vorbiscomment"
        elif container_format == "m4a":
            payload = _write_m4a_tags(target, normalized.values)
            tag_format = "mp4"
        else:
            return TagWriteResult(
                success=False,
                status=STATUS_UNSUPPORTED_FORMAT,
                file_path=str(target),
                tag_format=tag_format,
                error=f"Unsupported tag-writing file type: {target.suffix or '(no extension)'}",
            )
    except ImportError as exc:
        return TagWriteResult(
            success=False,
            status=STATUS_DEPENDENCY_MISSING,
            file_path=str(target),
            tag_format=tag_format,
            warnings=normalized.warnings,
            error=f"mutagen is required for tag writing: {exc}",
        )
    except Exception as exc:
        return TagWriteResult(
            success=False,
            status=STATUS_FAILED,
            file_path=str(target),
            tag_format=tag_format,
            warnings=normalized.warnings,
            error=f"Tag writing failed: {exc}",
        )

    return TagWriteResult(
        success=True,
        status=STATUS_WRITTEN,
        file_path=str(target),
        tag_format=tag_format,
        written_fields=payload.written_fields,
        warnings=normalized.warnings + payload.warnings,
    )


def _write_mp3_tags(
    path: Path,
    fields: dict[str, str],
    id3_version: str,
) -> _WritePayload:
    from mutagen.id3 import ID3, ID3NoHeaderError, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK

    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        tags = ID3()

    encoding = 1 if id3_version == ID3_VERSION_V23 else 3
    written: list[str] = []

    _replace_id3_text_frame(tags, "TIT2", TIT2, fields.get("title"), encoding, written, "title")
    _replace_id3_text_frame(tags, "TPE1", TPE1, fields.get("artist"), encoding, written, "artist")
    _replace_id3_text_frame(tags, "TALB", TALB, fields.get("album"), encoding, written, "album")
    _replace_id3_text_frame(
        tags,
        "TPE2",
        TPE2,
        fields.get("albumartist"),
        encoding,
        written,
        "albumartist",
    )
    _replace_id3_text_frame(
        tags,
        "TRCK",
        TRCK,
        fields.get("tracknumber"),
        encoding,
        written,
        "tracknumber",
    )

    date_field_name, date_value = _date_field(fields)
    if date_value:
        for frame_id in ("TDRC", "TYER", "TDAT", "TIME"):
            tags.delall(frame_id)
        tags.add(TDRC(encoding=encoding, text=[date_value]))
        written.append(date_field_name)

    _replace_id3_text_frame(tags, "TCON", TCON, fields.get("genre"), encoding, written, "genre")
    tags.save(str(path), v2_version=3 if id3_version == ID3_VERSION_V23 else 4)

    return _WritePayload(written_fields=_ordered_written_fields(written))


def _write_flac_tags(path: Path, fields: dict[str, str]) -> _WritePayload:
    from mutagen.flac import FLAC

    audio = FLAC(str(path))
    written: list[str] = []
    vorbis_keys = {
        "title": "TITLE",
        "artist": "ARTIST",
        "album": "ALBUM",
        "albumartist": "ALBUMARTIST",
        "tracknumber": "TRACKNUMBER",
        "genre": "GENRE",
    }

    for field_name, vorbis_key in vorbis_keys.items():
        value = fields.get(field_name)
        if value:
            audio[vorbis_key] = [value]
            written.append(field_name)

    date_field_name, date_value = _date_field(fields)
    if date_value:
        audio["DATE"] = [date_value]
        written.append(date_field_name)

    audio.save()
    return _WritePayload(written_fields=_ordered_written_fields(written))


def _write_m4a_tags(path: Path, fields: dict[str, str]) -> _WritePayload:
    from mutagen.mp4 import MP4

    audio = MP4(str(path))
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    if tags is None:
        raise ValueError("Could not create M4A tag container.")
    written: list[str] = []
    warnings: list[str] = []
    mp4_keys = {
        "title": "\xa9nam",
        "artist": "\xa9ART",
        "album": "\xa9alb",
        "albumartist": "aART",
        "genre": "\xa9gen",
    }

    for field_name, mp4_key in mp4_keys.items():
        value = fields.get(field_name)
        if value:
            tags[mp4_key] = [value]
            written.append(field_name)

    tracknumber = fields.get("tracknumber")
    if tracknumber:
        parsed_tracknumber = _parse_mp4_tracknumber(tracknumber)
        if parsed_tracknumber is None:
            warnings.append(
                "M4A tracknumber was ignored because it is not numeric: "
                f"{tracknumber}"
            )
        else:
            tags["trkn"] = [parsed_tracknumber]
            written.append("tracknumber")

    date_field_name, date_value = _date_field(fields)
    if date_value:
        tags["\xa9day"] = [date_value]
        written.append(date_field_name)

    audio.save()
    return _WritePayload(written_fields=_ordered_written_fields(written), warnings=warnings)


def _replace_id3_text_frame(
    tags: Any,
    frame_id: str,
    frame_class: Any,
    value: str | None,
    encoding: int,
    written: list[str],
    field_name: str,
) -> None:
    if not value:
        return
    tags.delall(frame_id)
    tags.add(frame_class(encoding=encoding, text=[value]))
    written.append(field_name)


def _normalize_metadata(metadata: Mapping[str, Any]) -> _NormalizedMetadata:
    values: dict[str, str] = {}
    warnings: list[str] = []

    if "source_path" in metadata:
        warnings.append(
            "metadata field source_path was ignored; tag writer only uses provided normalized tag fields."
        )

    for field_name in ("title", "artist", "album", "albumartist", "tracknumber"):
        value = _metadata_text_value(metadata, field_name, warnings)
        if value:
            values[field_name] = value

    date_value = _metadata_text_value(metadata, "date", warnings)
    year_value = _metadata_text_value(metadata, "year", warnings)
    if date_value:
        values["date"] = date_value
        if year_value:
            warnings.append("metadata field year was ignored because date is present.")
    elif year_value:
        values["year"] = year_value

    genre_value = _metadata_text_value(metadata, "genre", warnings)
    if genre_value:
        values["genre"] = genre_value

    return _NormalizedMetadata(values=values, warnings=warnings)


def _metadata_text_value(
    metadata: Mapping[str, Any],
    field_name: str,
    warnings: list[str],
) -> str | None:
    raw_value = metadata.get(field_name)
    if raw_value is None:
        return None
    if isinstance(raw_value, Mapping):
        warnings.append(f"metadata field {field_name} was ignored because it is not scalar.")
        return None
    if isinstance(raw_value, (list, tuple, set)):
        value = "; ".join(
            str(item).strip()
            for item in raw_value
            if item is not None and str(item).strip()
        )
    else:
        value = str(raw_value).strip()
    return value or None


def _date_field(fields: dict[str, str]) -> tuple[str, str | None]:
    if fields.get("date"):
        return "date", fields["date"]
    if fields.get("year"):
        return "year", fields["year"]
    return "date", None


def _ordered_written_fields(written_fields: list[str]) -> list[str]:
    present = set(written_fields)
    return [field_name for field_name in _ORDERED_FIELDS if field_name in present]


def _parse_mp4_tracknumber(value: str) -> tuple[int, int] | None:
    match = _MP4_TRACKNUMBER_RE.fullmatch(value)
    if match is None:
        return None
    track = int(match.group(1))
    total = int(match.group(2)) if match.group(2) is not None else 0
    if track <= 0 or total < 0:
        return None
    return track, total


def _container_format_for_path(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        return "mp3"
    if suffix == ".flac":
        return "flac"
    if suffix in {".m4a", ".mp4"}:
        return "m4a"
    return None


def _tag_format_for_path(path: Path, *, id3_version: str) -> str | None:
    container_format = _container_format_for_path(path)
    if container_format == "mp3":
        normalized_version = _normalize_id3_version(id3_version)
        if normalized_version == ID3_VERSION_V23:
            return "id3v2.3"
        if normalized_version == ID3_VERSION_V24:
            return "id3v2.4"
        return "id3"
    if container_format == "flac":
        return "vorbiscomment"
    if container_format == "m4a":
        return "mp4"
    return None


def _normalize_id3_version(value: str) -> str | None:
    normalized = str(value or "").strip().lower().replace(".", "")
    if normalized in {"v23", "23", "id3v23"}:
        return ID3_VERSION_V23
    if normalized in {"v24", "24", "id3v24"}:
        return ID3_VERSION_V24
    return None


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
