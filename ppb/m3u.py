"""M3U8 playlist generation for successfully exported audio files."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from ppb.contract import PlaylistJob, TrackEntry
from ppb.copier import CopyStageResult, STATUS_CONVERTED, STATUS_COPIED, STATUS_RESUMED


DEFAULT_M3U_FILENAME = "playlist.m3u8"

M3U_STATUS_GENERATED = "generated"
M3U_STATUS_SKIPPED = "skipped"
M3U_STATUS_FAILED = "failed"

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
class M3UGenerationResult:
    """Outcome of the post-copy M3U8 generation stage."""

    status: str
    m3u_path: str | None
    track_count: int
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def validate_m3u_filename(filename: str) -> str:
    """Validate that the requested M3U8 filename is a safe leaf filename."""

    candidate = filename.strip()
    if not candidate:
        raise ValueError("M3U8 filename must not be empty.")
    if Path(candidate).is_absolute() or PureWindowsPath(candidate).is_absolute():
        raise ValueError(f"M3U8 filename must not be an absolute path: {filename}")
    if Path(candidate).name != candidate or "\\" in candidate or "/" in candidate:
        raise ValueError(f"M3U8 filename must be a leaf filename, not a path: {filename}")
    if ".." in Path(candidate).parts:
        raise ValueError(f"M3U8 filename must not contain parent traversal: {filename}")
    if _INVALID_FILENAME_CHARS_RE.search(candidate):
        raise ValueError(f"M3U8 filename contains invalid filesystem characters: {filename}")
    if candidate in {".", ".."} or not candidate.strip(" ."):
        raise ValueError(f"M3U8 filename is not usable: {filename}")
    if _is_reserved_windows_name(candidate):
        raise ValueError(f"M3U8 filename uses a reserved Windows device name: {filename}")
    return candidate


def generate_m3u8_playlist(
    *,
    job: PlaylistJob,
    copy_result: CopyStageResult,
    final_output_dir: Path | str,
    m3u_name: str = DEFAULT_M3U_FILENAME,
) -> M3UGenerationResult:
    """Generate ``playlist.m3u8`` from successfully exported or resumed files."""

    safe_m3u_name = validate_m3u_filename(m3u_name)
    if not job.settings.generate_m3u8:
        return M3UGenerationResult(
            status=M3U_STATUS_SKIPPED,
            m3u_path=None,
            track_count=0,
            warnings=["M3U8 generation skipped because settings.generate_m3u8 is false."],
        )

    output_dir = Path(final_output_dir).resolve(strict=False)
    playlist_path = (output_dir / safe_m3u_name).resolve(strict=False)
    warnings: list[str] = []
    lines = ["#EXTM3U"]
    track_count = 0

    for track, result in zip(job.tracks, copy_result.results):
        if (
            result.status not in {STATUS_COPIED, STATUS_CONVERTED, STATUS_RESUMED}
            or not result.destination_path
        ):
            continue

        destination_path = Path(result.destination_path).resolve(strict=False)
        if not destination_path.is_file():
            warnings.append(
                "Exported track is missing on disk during M3U8 generation: "
                f"{destination_path}"
            )
            continue

        extinf_line = _build_extinf_line(track)
        if extinf_line is not None:
            lines.append(extinf_line)
        lines.append(_relative_playlist_path(playlist_path.parent, destination_path))
        track_count += 1

    try:
        with playlist_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as exc:
        return M3UGenerationResult(
            status=M3U_STATUS_FAILED,
            m3u_path=str(playlist_path),
            track_count=track_count,
            warnings=warnings,
            errors=[str(exc)],
        )

    return M3UGenerationResult(
        status=M3U_STATUS_GENERATED,
        m3u_path=str(playlist_path),
        track_count=track_count,
        warnings=warnings,
    )


def _build_extinf_line(track: TrackEntry) -> str | None:
    display_text = _build_display_text(track)
    if display_text is None:
        return None
    duration = _format_duration(track.duration_sec)
    return f"#EXTINF:{duration},{display_text}"


def _build_display_text(track: TrackEntry) -> str | None:
    artist = _sanitize_m3u_text(track.artist)
    title = _sanitize_m3u_text(track.title)

    if artist and title:
        return f"{artist} - {title}"
    if title:
        return title
    if artist:
        return artist
    return None


def _format_duration(duration_sec: float | None) -> int:
    if duration_sec is None:
        return -1
    if duration_sec < 0:
        return -1
    return int(round(duration_sec))


def _sanitize_m3u_text(value: str | None) -> str:
    if not value:
        return ""

    text = value.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    cleaned = "".join(
        character
        for character in text
        if not unicodedata.category(character).startswith("C")
    )
    return " ".join(cleaned.split())


def _relative_playlist_path(base_dir: Path, destination_path: Path) -> str:
    relative_path = destination_path.relative_to(base_dir)
    return relative_path.as_posix()


def _is_reserved_windows_name(filename: str) -> bool:
    stem = filename.split(".", 1)[0].upper()
    return stem in _RESERVED_WINDOWS_NAMES
