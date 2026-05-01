"""Neutral in-memory objects for physical playlist jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SUPPORTED_FORMAT = "physical_playlist_job.v1"
DEFAULT_FILENAME_TEMPLATE = "{position:02d} - {artist} - {title}"


@dataclass
class PlaylistSettings:
    """Processing intentions from the canonical ``settings`` object."""

    output_format: str | None = "source"
    copy_mode: str = "copy_if_compatible"
    normalize_loudness: bool = False
    target_lufs: float | None = -14.0
    true_peak_db: float | None = -1.0
    write_tags: bool = False
    generate_m3u8: bool = True
    filename_template: str | None = DEFAULT_FILENAME_TEMPLATE


@dataclass
class TrackEntry:
    """A single neutral track entry after validation and normalization."""

    source_path: str
    position: int

    output_filename: str | None = None
    filename_hint: str | None = None

    title: str | None = None
    artist: str | None = None
    album: str | None = None
    albumartist: str | None = None
    tracknumber: str | None = None
    date: str | None = None
    year: str | None = None
    genre: str | None = None

    duration_sec: float | None = None
    codec: str | None = None
    bitrate_kbps: int | None = None
    sample_rate_hz: int | None = None
    channels: int | None = None
    bit_depth: int | None = None
    tag_format: str | None = None
    source_status: str | None = None
    availability: str | None = None

    producer_meta: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    @property
    def is_blocked(self) -> bool:
        return bool(self.blockers)


@dataclass
class PlaylistJob:
    """Internal representation independent of any producer application."""

    format: str = SUPPORTED_FORMAT
    playlist_name: str = ""
    playlist_description: str | None = None
    playlist_track_count: int | None = None
    settings: PlaylistSettings = field(default_factory=PlaylistSettings)
    tracks: list[TrackEntry] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)
    validation_blockers: list[str] = field(default_factory=list)
    legacy_input: bool = False

    @property
    def output_format(self) -> str | None:
        return self.settings.output_format

    @property
    def normalize_loudness(self) -> bool:
        return self.settings.normalize_loudness

    @property
    def write_tags(self) -> bool:
        return self.settings.write_tags
