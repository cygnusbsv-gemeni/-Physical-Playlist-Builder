from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ppb.contract import SUPPORTED_FORMAT
from ppb.validator import LEGACY_WARNING, validate_job


def canonical_job(**overrides) -> dict:
    job = {
        "format": SUPPORTED_FORMAT,
        "generated_at": "2026-05-01T00:00:00+00:00",
        "playlist": {
            "name": "Test Playlist",
            "description": "Test notes",
            "track_count": 1,
        },
        "settings": {
            "output_format": "mp3",
            "copy_mode": "convert_if_needed",
            "normalize_loudness": True,
            "target_lufs": -16.0,
            "true_peak_db": -1.5,
            "write_tags": True,
            "generate_m3u8": False,
            "filename_template": "{position:02d} - {title}",
        },
        "tracks": [
            {
                "position": 1,
                "source_path": "/music/01.flac",
                "title": "Track One",
                "artist": "Artist",
            }
        ],
    }
    job.update(overrides)
    return job


def test_canonical_job_is_accepted():
    result = validate_job(canonical_job())
    assert result.ok
    assert result.job.format == SUPPORTED_FORMAT


def test_top_level_format_required_for_canonical_input():
    data = canonical_job()
    data.pop("format")
    result = validate_job(data)
    assert not result.ok
    assert any('"format"' in error for error in result.fatal_errors)


def test_unsupported_format_rejected():
    result = validate_job(canonical_job(format="other.v1"))
    assert not result.ok
    assert any("Unsupported format" in error for error in result.fatal_errors)


def test_top_level_schema_is_not_required():
    data = canonical_job()
    assert "schema" not in data
    assert validate_job(data).ok


def test_playlist_name_read_from_playlist_object():
    result = validate_job(canonical_job(playlist={"name": "Nested Name"}))
    assert result.ok
    assert result.job.playlist_name == "Nested Name"


def test_settings_are_read_from_settings_object():
    result = validate_job(canonical_job())
    settings = result.job.settings
    assert settings.output_format == "mp3"
    assert settings.copy_mode == "convert_if_needed"
    assert settings.normalize_loudness is True
    assert settings.target_lufs == -16.0
    assert settings.true_peak_db == -1.5
    assert settings.write_tags is True
    assert settings.generate_m3u8 is False
    assert settings.filename_template == "{position:02d} - {title}"


def test_tracks_are_read_from_tracks_array_order():
    tracks = [
        {"position": 2, "source_path": "/music/second.flac"},
        {"position": 1, "source_path": "/music/first.flac"},
    ]
    result = validate_job(canonical_job(tracks=tracks))
    assert [track.source_path for track in result.job.tracks] == [
        "/music/second.flac",
        "/music/first.flac",
    ]


def test_source_path_is_required():
    result = validate_job(canonical_job(tracks=[{"position": 1}]))
    assert result.ok
    assert result.blocked_count == 1
    assert any('"source_path" is required' in issue.message for issue in result.issues)


def test_missing_source_path_reports_explicit_position():
    result = validate_job(canonical_job(tracks=[{"position": 5}]))
    assert any(issue.level == "blocker" and issue.position == 5 for issue in result.issues)


def test_missing_source_path_reports_implicit_index():
    result = validate_job(canonical_job(tracks=[{}]))
    assert any(issue.level == "blocker" and issue.position == 1 for issue in result.issues)


def test_missing_position_warns_and_uses_implicit_order():
    result = validate_job(canonical_job(tracks=[{"source_path": "/music/no-pos.flac"}]))
    assert result.ok
    assert result.warning_count == 1
    assert result.job.tracks[0].position == 1


def test_blockers_are_reported_and_block_track():
    data = canonical_job(
        tracks=[
            {
                "position": 1,
                "source_path": "/music/missing.flac",
                "blockers": [{"message": "Source unavailable"}],
            }
        ]
    )
    result = validate_job(data)
    assert result.ok
    assert result.blocked_count == 1
    assert any("Source unavailable" in issue.message for issue in result.issues)


def test_strict_fails_on_blocked_tracks():
    result = validate_job(canonical_job(tracks=[{"position": 1}]), strict=True)
    assert not result.ok
    assert result.blocked_count == 1


def test_non_strict_allows_blocked_tracks():
    result = validate_job(canonical_job(tracks=[{"position": 1}]), strict=False)
    assert result.ok
    assert result.blocked_count == 1


def test_summary_is_optional():
    data = canonical_job()
    data.pop("summary", None)
    assert validate_job(data).ok


def test_producer_meta_is_optional_and_ignored_safely():
    data = canonical_job()
    data["producer_meta"] = {"unknown": {"nested": [1, 2, 3]}}
    result = validate_job(data)
    assert result.ok
    assert result.job.playlist_name == "Test Playlist"


def test_unknown_optional_fields_are_ignored_safely():
    data = canonical_job(
        extra_top_level={"ignored": True},
        tracks=[
            {
                "position": 1,
                "source_path": "/music/01.flac",
                "unexpected_field": {"any": "shape"},
            }
        ],
    )
    assert validate_job(data).ok


def test_input_warnings_are_preserved():
    result = validate_job(
        canonical_job(
            tracks=[
                {
                    "position": 1,
                    "source_path": "/music/01.flac",
                    "warnings": [{"message": "Input warning"}],
                }
            ]
        )
    )
    assert result.job.tracks[0].warnings == ["Input warning"]
    assert result.warning_count == 1


def test_legacy_job_is_accepted_with_warning():
    legacy = {
        "schema": SUPPORTED_FORMAT,
        "playlist_name": "Legacy Playlist",
        "output_format": "flac",
        "normalize_loudness": False,
        "write_tags": True,
        "tracks": [{"position": 1, "source_path": "/music/legacy.flac"}],
    }
    result = validate_job(legacy)
    assert result.ok
    assert result.legacy_input
    assert LEGACY_WARNING in result.global_warnings
    assert result.job.playlist_name == "Legacy Playlist"
    assert result.job.settings.output_format == "flac"
    assert result.job.settings.write_tags is True


def test_legacy_display_title_is_mapped_to_title():
    legacy = {
        "schema": SUPPORTED_FORMAT,
        "playlist_name": "Legacy Playlist",
        "tracks": [
            {
                "position": 1,
                "source_path": "/music/legacy.flac",
                "display_title": "Legacy Title",
            }
        ],
    }
    result = validate_job(legacy)
    assert result.ok
    assert result.job.tracks[0].title == "Legacy Title"
