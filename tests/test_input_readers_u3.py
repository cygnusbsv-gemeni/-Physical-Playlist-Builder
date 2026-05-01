from __future__ import annotations

import ast
import json
import shutil
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ppb.contract import SUPPORTED_FORMAT, PlaylistJob
from ppb.input_readers import InputReadError, read_playlist_input
from ppb.validator import LEGACY_WARNING


RUNTIME_ROOT = Path(__file__).resolve().parent.parent / "test_runtime"


def make_workspace() -> Path:
    path = RUNTIME_ROOT / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


def cleanup_workspace(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def canonical_job() -> dict:
    return {
        "format": SUPPORTED_FORMAT,
        "playlist": {"name": "Canonical", "track_count": 1},
        "settings": {
            "output_format": "source",
            "copy_mode": "copy_if_compatible",
            "normalize_loudness": False,
            "target_lufs": -14.0,
            "true_peak_db": -1.0,
            "write_tags": False,
            "generate_m3u8": True,
            "filename_template": "{position:02d} - {artist} - {title}",
        },
        "tracks": [{"position": 1, "source_path": "/music/one.flac"}],
    }


def test_canonical_json_input_still_works():
    workspace = make_workspace()
    try:
        path = workspace / "playlist_job.json"
        path.write_text(json.dumps(canonical_job()), encoding="utf-8")
        result = read_playlist_input(path)
        assert result.input_type == "json"
        assert not result.converted
        assert result.validation.ok
        assert result.validation.job.playlist_name == "Canonical"
    finally:
        cleanup_workspace(workspace)


def test_legacy_json_input_still_works_with_warning():
    workspace = make_workspace()
    try:
        path = workspace / "legacy.json"
        path.write_text(
            json.dumps(
                {
                    "schema": SUPPORTED_FORMAT,
                    "playlist_name": "Legacy",
                    "tracks": [{"position": 1, "source_path": "/music/legacy.flac"}],
                }
            ),
            encoding="utf-8",
        )
        result = read_playlist_input(path)
        assert result.validation.ok
        assert result.validation.legacy_input
        assert LEGACY_WARNING in result.validation.global_warnings
    finally:
        cleanup_workspace(workspace)


def test_txt_input_loads_paths_and_ignores_blank_and_comment_lines():
    workspace = make_workspace()
    try:
        path = workspace / "tracks.txt"
        path.write_text("# comment\n\none.flac\n  two.flac  \n", encoding="utf-8")
        result = read_playlist_input(path)
        tracks = result.validation.job.tracks
        assert result.input_type == "txt"
        assert result.converted
        assert [track.position for track in tracks] == [1, 2]
        assert [track.producer_meta["original_source_path"] for track in tracks] == [
            "one.flac",
            "two.flac",
        ]
    finally:
        cleanup_workspace(workspace)


def test_txt_relative_paths_are_resolved_against_txt_folder():
    workspace = make_workspace()
    try:
        path = workspace / "lists" / "tracks.txt"
        path.parent.mkdir()
        path.write_text("music/one.flac\n", encoding="utf-8")
        result = read_playlist_input(path)
        expected = str((path.parent / "music/one.flac").resolve(strict=False))
        assert result.validation.job.tracks[0].source_path == expected
    finally:
        cleanup_workspace(workspace)


def test_csv_comma_delimited_input_loads():
    workspace = make_workspace()
    try:
        path = workspace / "tracks.csv"
        path.write_text("source_path,title\none.flac,One\n", encoding="utf-8")
        result = read_playlist_input(path)
        track = result.validation.job.tracks[0]
        assert result.input_type == "csv"
        assert track.title == "One"
    finally:
        cleanup_workspace(workspace)


def test_csv_semicolon_delimited_input_loads():
    workspace = make_workspace()
    try:
        path = workspace / "tracks.csv"
        path.write_text("source_path;artist\none.flac;Artist\n", encoding="utf-8-sig")
        result = read_playlist_input(path)
        assert result.validation.job.tracks[0].artist == "Artist"
    finally:
        cleanup_workspace(workspace)


def test_csv_requires_source_path_column():
    workspace = make_workspace()
    try:
        path = workspace / "tracks.csv"
        path.write_text("title\nOne\n", encoding="utf-8")
        with pytest.raises(InputReadError) as exc_info:
            read_playlist_input(path)
        assert "source_path" in str(exc_info.value)
    finally:
        cleanup_workspace(workspace)


def test_csv_optional_metadata_fields_are_mapped():
    workspace = make_workspace()
    try:
        path = workspace / "tracks.csv"
        path.write_text(
            (
                "position,source_path,output_filename,filename_hint,title,artist,album,"
                "albumartist,tracknumber,date,year,genre,duration_sec,codec,bitrate_kbps,"
                "sample_rate_hz,channels,bit_depth,tag_format,warnings,blockers\n"
                "7,one.flac,out.flac,hint,Title,Artist,Album,Album Artist,1,2026-01-01,"
                "2026,Rock,123.5,flac,900,44100,2,16,Vorbis,careful,\n"
            ),
            encoding="utf-8",
        )
        result = read_playlist_input(path)
        track = result.validation.job.tracks[0]
        assert track.position == 7
        assert track.output_filename == "out.flac"
        assert track.filename_hint == "hint"
        assert track.title == "Title"
        assert track.artist == "Artist"
        assert track.album == "Album"
        assert track.albumartist == "Album Artist"
        assert track.tracknumber == "1"
        assert track.date == "2026-01-01"
        assert track.year == "2026"
        assert track.genre == "Rock"
        assert track.duration_sec == 123.5
        assert track.codec == "flac"
        assert track.bitrate_kbps == 900
        assert track.sample_rate_hz == 44100
        assert track.channels == 2
        assert track.bit_depth == 16
        assert track.tag_format == "Vorbis"
        assert track.warnings == ["careful"]
    finally:
        cleanup_workspace(workspace)


def test_m3u8_input_loads():
    workspace = make_workspace()
    try:
        path = workspace / "playlist.m3u8"
        path.write_text("#EXTM3U\none.flac\n", encoding="utf-8")
        result = read_playlist_input(path)
        assert result.input_type == "m3u8"
        assert result.validation.ok
        assert len(result.validation.job.tracks) == 1
    finally:
        cleanup_workspace(workspace)


def test_m3u8_relative_paths_are_resolved_against_playlist_folder():
    workspace = make_workspace()
    try:
        path = workspace / "lists" / "playlist.m3u8"
        path.parent.mkdir()
        path.write_text("music/one.flac\n", encoding="utf-8")
        result = read_playlist_input(path)
        expected = str((path.parent / "music/one.flac").resolve(strict=False))
        assert result.validation.job.tracks[0].source_path == expected
    finally:
        cleanup_workspace(workspace)


def test_extinf_duration_title_artist_are_parsed():
    workspace = make_workspace()
    try:
        path = workspace / "playlist.m3u8"
        path.write_text("#EXTM3U\n#EXTINF:245,Artist - Title\none.flac\n", encoding="utf-8")
        result = read_playlist_input(path)
        track = result.validation.job.tracks[0]
        assert track.duration_sec == 245.0
        assert track.artist == "Artist"
        assert track.title == "Title"
    finally:
        cleanup_workspace(workspace)


def test_m3u_input_loads():
    workspace = make_workspace()
    try:
        path = workspace / "playlist.m3u"
        path.write_text("#EXTM3U\none.flac\n", encoding="utf-8")
        result = read_playlist_input(path)
        assert result.input_type == "m3u"
        assert result.validation.ok
    finally:
        cleanup_workspace(workspace)


@pytest.mark.parametrize("filename,content", [
    ("job.json", None),
    ("tracks.txt", "one.flac\n"),
    ("tracks.csv", "source_path\none.flac\n"),
    ("playlist.m3u", "one.flac\n"),
    ("playlist.m3u8", "one.flac\n"),
])
def test_all_input_types_produce_playlist_job_shape(filename: str, content: str | None):
    workspace = make_workspace()
    try:
        path = workspace / filename
        if content is None:
            path.write_text(json.dumps(canonical_job()), encoding="utf-8")
        else:
            path.write_text(content, encoding="utf-8")
        result = read_playlist_input(path)
        assert isinstance(result.validation.job, PlaylistJob)
        assert result.validation.job.format == SUPPORTED_FORMAT
        assert isinstance(result.validation.job.tracks, list)
    finally:
        cleanup_workspace(workspace)


def test_input_readers_do_not_import_producer_specific_modules():
    source = (Path(__file__).resolve().parent.parent / "ppb" / "input_readers.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    forbidden = ("flask", "sqlite3", "backend", "MusicLib", "musiclib")
    assert not any(name.startswith(forbidden) for name in imported)


def test_reader_does_not_write_or_create_output_music_files():
    workspace = make_workspace()
    try:
        path = workspace / "tracks.txt"
        path.write_text("source.flac\n", encoding="utf-8")
        before = {item.relative_to(workspace) for item in workspace.rglob("*")}
        result = read_playlist_input(path)
        after = {item.relative_to(workspace) for item in workspace.rglob("*")}
        assert result.validation.ok
        assert before == after
    finally:
        cleanup_workspace(workspace)
