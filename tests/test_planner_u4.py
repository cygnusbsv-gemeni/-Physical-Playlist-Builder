from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ppb.contract import SUPPORTED_FORMAT
from ppb.planner import (
    ACTION_CONVERT,
    ACTION_COPY,
    ACTION_ERROR,
    ACTION_SKIP_BLOCKED,
    build_dry_run_plan,
)
from ppb.report import dry_run_plan_to_dict
from ppb.validator import validate_job


RUNTIME_ROOT = Path(__file__).resolve().parent.parent / "test_runtime"


def make_workspace() -> Path:
    path = RUNTIME_ROOT / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


def cleanup_workspace(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def canonical_job(tracks: list[dict], output_format: str | None = "source") -> dict:
    return {
        "format": SUPPORTED_FORMAT,
        "playlist": {"name": "Dry Run", "track_count": len(tracks)},
        "settings": {
            "output_format": output_format,
            "copy_mode": "copy_if_compatible",
            "normalize_loudness": False,
            "target_lufs": -14.0,
            "true_peak_db": -1.0,
            "write_tags": False,
            "generate_m3u8": True,
            "filename_template": "{position:02d} - {artist} - {title}",
        },
        "tracks": tracks,
    }


def build_plan(raw_job: dict, out_dir: Path):
    result = validate_job(raw_job)
    assert result.job is not None
    return build_dry_run_plan(result.job, out_dir)


def test_planner_computes_copy_operation_and_destination():
    workspace = make_workspace()
    try:
        source = workspace / "music" / "one.flac"
        source.parent.mkdir()
        source.write_text("fixture", encoding="utf-8")

        plan = build_plan(
            canonical_job(
                [
                    {
                        "position": 1,
                        "source_path": str(source),
                        "artist": "Artist",
                        "title": "Song",
                    }
                ]
            ),
            workspace / "out",
        )

        operation = plan.operations[0]
        assert operation.planned_action == ACTION_COPY
        assert operation.source_path == str(source)
        assert operation.source_exists is True
        assert operation.expected_output_filename == "01 - Artist - Song.flac"
        assert operation.destination_path == str((workspace / "out" / "01 - Artist - Song.flac").resolve(strict=False))
        assert plan.safe_operations == [operation]
    finally:
        cleanup_workspace(workspace)


def test_planner_marks_conversion_when_target_format_differs():
    workspace = make_workspace()
    try:
        source = workspace / "music" / "one.flac"
        source.parent.mkdir()
        source.write_text("fixture", encoding="utf-8")

        plan = build_plan(
            canonical_job(
                [{"position": 1, "source_path": str(source), "artist": "A", "title": "T"}],
                output_format="mp3",
            ),
            workspace / "out",
        )

        operation = plan.operations[0]
        assert operation.planned_action == ACTION_CONVERT
        assert operation.expected_output_filename == "01 - A - T.mp3"
    finally:
        cleanup_workspace(workspace)


def test_planner_reports_missing_source_as_track_error():
    workspace = make_workspace()
    try:
        missing = workspace / "music" / "missing.flac"
        plan = build_plan(
            canonical_job([{"position": 1, "source_path": str(missing)}]),
            workspace / "out",
        )

        operation = plan.operations[0]
        assert operation.planned_action == ACTION_ERROR
        assert operation.source_exists is False
        assert any("Source file does not exist" in error for error in operation.errors)
        assert not plan.safe_operations
    finally:
        cleanup_workspace(workspace)


def test_planner_detects_duplicate_output_filenames():
    workspace = make_workspace()
    try:
        source_a = workspace / "a.flac"
        source_b = workspace / "b.flac"
        source_a.write_text("fixture", encoding="utf-8")
        source_b.write_text("fixture", encoding="utf-8")

        plan = build_plan(
            canonical_job(
                [
                    {"position": 1, "source_path": str(source_a), "output_filename": "same.flac"},
                    {"position": 2, "source_path": str(source_b), "output_filename": "same.flac"},
                ]
            ),
            workspace / "out",
        )

        assert plan.duplicate_output_filenames == ["same.flac"]
        assert all(operation.destination_filename_conflict for operation in plan.operations)
        assert all(operation.planned_action == ACTION_ERROR for operation in plan.operations)
    finally:
        cleanup_workspace(workspace)


def test_planner_lists_blocked_tracks_separately():
    workspace = make_workspace()
    try:
        plan = build_plan(
            canonical_job(
                [
                    {
                        "position": 1,
                        "source_path": str(workspace / "blocked.flac"),
                        "blockers": ["Unavailable in source catalog"],
                    }
                ]
            ),
            workspace / "out",
        )

        assert len(plan.blocked_tracks) == 1
        assert plan.blocked_tracks[0].planned_action == ACTION_SKIP_BLOCKED
        assert "Unavailable in source catalog" in plan.blocked_tracks[0].errors
    finally:
        cleanup_workspace(workspace)


def test_planner_rejects_output_directory_equal_to_source_directory():
    workspace = make_workspace()
    try:
        source = workspace / "music" / "one.flac"
        source.parent.mkdir()
        source.write_text("fixture", encoding="utf-8")

        plan = build_plan(
            canonical_job([{"position": 1, "source_path": str(source)}]),
            source.parent,
        )

        assert not plan.output_dir_valid
        assert plan.output_dir_overwrites_source_dir
        assert plan.operations[0].planned_action == ACTION_ERROR
        assert not plan.safe_operations
    finally:
        cleanup_workspace(workspace)


def test_planner_rejects_output_directory_inside_source_directory():
    workspace = make_workspace()
    try:
        source = workspace / "music" / "one.flac"
        source.parent.mkdir()
        source.write_text("fixture", encoding="utf-8")

        plan = build_plan(
            canonical_job([{"position": 1, "source_path": str(source)}]),
            source.parent / "Physical Export",
        )

        assert not plan.output_dir_valid
        assert plan.output_dir_inside_source_dir
        assert any("inside a source track directory" in error for error in plan.errors)
        assert plan.operations[0].planned_action == ACTION_ERROR
    finally:
        cleanup_workspace(workspace)


def test_planner_rejects_dangerous_explicit_output_filename():
    workspace = make_workspace()
    try:
        source = workspace / "music" / "one.flac"
        source.parent.mkdir()
        source.write_text("fixture", encoding="utf-8")

        plan = build_plan(
            canonical_job(
                [{"position": 1, "source_path": str(source), "output_filename": "..\\evil.flac"}]
            ),
            workspace / "out",
        )

        operation = plan.operations[0]
        assert operation.planned_action == ACTION_ERROR
        assert operation.destination_path is None
        assert any("Dangerous output filename" in error for error in operation.errors)
    finally:
        cleanup_workspace(workspace)


def test_planner_rejects_invalid_output_directory_path():
    workspace = make_workspace()
    try:
        source = workspace / "music" / "one.flac"
        source.parent.mkdir()
        source.write_text("fixture", encoding="utf-8")

        plan = build_plan(
            canonical_job([{"position": 1, "source_path": str(source)}]),
            workspace / "bad<dir>",
        )

        assert not plan.output_dir_valid
        assert any("invalid filesystem characters" in error for error in plan.errors)
        assert str(workspace / "bad<dir>") in plan.dangerous_output_paths
        assert plan.operations[0].planned_action == ACTION_ERROR
    finally:
        cleanup_workspace(workspace)


def test_dry_run_report_dict_contains_summary():
    workspace = make_workspace()
    try:
        source = workspace / "music" / "one.flac"
        source.parent.mkdir()
        source.write_text("fixture", encoding="utf-8")
        plan = build_plan(
            canonical_job([{"position": 1, "source_path": str(source)}]),
            workspace / "out",
        )
        data = dry_run_plan_to_dict(plan)
        json.dumps(data)
        assert data["summary"]["operation_count"] == 1
        assert data["summary"]["safe_operation_count"] == 1
    finally:
        cleanup_workspace(workspace)
