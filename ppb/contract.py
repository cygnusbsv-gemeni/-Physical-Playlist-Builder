"""
ppb/contract.py — Neutral input contract helpers.

Defines the canonical in-memory representation of a playlist job
that all input readers (JSON, TXT, CSV, M3U, folder) must produce.

Stage U1: stub only — structure documented, no validation logic yet.
Validation logic will be added in Stage U2/U3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TrackEntry:
    """
    A single track entry in a playlist job.

    Attributes
    ----------
    source_path : str
        Absolute or resolvable path to the source audio file.
    position : int
        1-based position in the playlist.
    display_title : Optional[str]
        Human-readable title override (optional).
    artist : Optional[str]
        Artist name override (optional).
    album : Optional[str]
        Album name override (optional).
    """

    source_path: str
    position: int
    display_title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None


@dataclass
class PlaylistJob:
    """
    Canonical in-memory representation of a playlist job.

    This is the neutral contract that all input readers must produce.
    The rest of the pipeline (validator, planner, copier, etc.) works
    exclusively against this dataclass — never against raw JSON or CSV.

    Attributes
    ----------
    schema : str
        Schema identifier, e.g. 'physical_playlist_job.v1'.
    playlist_name : str
        Human-readable playlist name.
    tracks : list[TrackEntry]
        Ordered list of track entries.
    output_format : Optional[str]
        Target audio format for conversion, e.g. 'mp3', 'flac'.
        None means copy as-is.
    normalize_loudness : bool
        Whether to apply loudness normalization to exported copies.
    write_tags : bool
        Whether to write metadata tags to exported copies.
    """

    schema: str = "physical_playlist_job.v1"
    playlist_name: str = ""
    tracks: list[TrackEntry] = field(default_factory=list)
    output_format: Optional[str] = None
    normalize_loudness: bool = False
    write_tags: bool = False
