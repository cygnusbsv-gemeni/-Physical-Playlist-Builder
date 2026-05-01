from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ppb.cli import main
from ppb.contract import SUPPORTED_FORMAT


RUNTIME_ROOT = Path(__file__).resolve().parent.parent / "test_runtime"


def make_workspace() -> Path:
    path = RUNTIME_ROOT / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


def cleanup_workspace(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def write_job(workspace: Path, data: dict) -> Path:
    path = workspace / "playlist_job.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def canonical_job(tracks=None) -> dict:
    return {
        "format": SUPPORTED_FORMAT,
        "playlist": {"name": "Road Trip", "track_count": len(tracks or [])},
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
        "tracks": tracks or [],
    }


def find_export_session(root: Path) -> Path:
    sessions = list(root.rglob("export_session.json"))
    assert len(sessions) == 1
    return sessions[0]


def test_cli_prints_canonical_summary(capsys):
    workspace = make_workspace()
    try:
        job = write_job(
            workspace,
            canonical_job(
                [
                    {"source_path": "/music/01.flac", "position": 1},
                    {"source_path": "/music/02.flac", "position": 2},
                ]
            ),
        )
        main(["--input", str(job), "--out", str(workspace / "out"), "--dry-run"])
        out = capsys.readouterr().out
        assert f"Input path: {job}" in out
        assert "Detected input type: json" in out
        assert "Format: physical_playlist_job.v1" in out
        assert "Playlist name: Road Trip" in out
        assert "Track count: 2" in out
        assert "Dry-run mode: YES" in out
    finally:
        cleanup_workspace(workspace)


def test_cli_non_strict_reports_blocked_without_failing(capsys):
    workspace = make_workspace()
    try:
        job = write_job(workspace, canonical_job([{"position": 1}]))
        main(["--input", str(job), "--out", str(workspace / "out"), "--dry-run"])
        out = capsys.readouterr().out
        assert "Blocked track count: 1" in out
        assert "will be skipped later" in out
    finally:
        cleanup_workspace(workspace)


def test_cli_strict_exits_3_on_blocked_tracks():
    workspace = make_workspace()
    try:
        job = write_job(workspace, canonical_job([{"position": 1}]))
        with pytest.raises(SystemExit) as exc_info:
            main(["--input", str(job), "--out", str(workspace / "out"), "--strict"])
        assert exc_info.value.code == 3
    finally:
        cleanup_workspace(workspace)


def test_missing_input_exits_with_error():
    workspace = make_workspace()
    try:
        with pytest.raises(SystemExit) as exc_info:
            main(["--input", str(workspace / "missing.json"), "--out", str(workspace / "out")])
        assert exc_info.value.code != 0
    finally:
        cleanup_workspace(workspace)


def test_malformed_json_exits_with_error():
    workspace = make_workspace()
    try:
        bad = workspace / "bad.json"
        bad.write_text("{ not valid json }", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            main(["--input", str(bad), "--out", str(workspace / "out")])
        assert exc_info.value.code == 2
    finally:
        cleanup_workspace(workspace)


def test_cli_creates_timestamped_output_folder_and_session_without_copying_audio():
    workspace = make_workspace()
    try:
        source = workspace / "music" / "song.flac"
        source.parent.mkdir()
        source.write_text("fixture", encoding="utf-8")
        out_dir = workspace / "out"
        job = write_job(
            workspace,
            canonical_job(
                [
                    {
                        "source_path": str(source),
                        "position": 1,
                        "artist": "Artist",
                        "title": "Song",
                    }
                ]
            ),
        )
        main(["--input", str(job), "--out", str(out_dir)])
        session_path = find_export_session(out_dir)
        output_dir = session_path.parent
        assert output_dir.name.startswith("Road Trip_")
        assert sorted(path.name for path in output_dir.iterdir()) == ["export_session.json"]
        data = json.loads(session_path.read_text(encoding="utf-8"))
        assert data["output"]["final_path"] == str(output_dir)
        assert data["handoff"]["final_output_dir"] == str(output_dir)
        assert data["handoff"]["audio_files_copied"] is False
    finally:
        cleanup_workspace(workspace)


def test_cli_dry_run_report_writes_json_without_output_folder(capsys):
    workspace = make_workspace()
    try:
        source = workspace / "music" / "song.flac"
        source.parent.mkdir()
        source.write_text("fixture", encoding="utf-8")
        out_dir = workspace / "out"
        report = workspace / "dry_run_report.json"
        job = write_job(
            workspace,
            canonical_job(
                [
                    {
                        "source_path": str(source),
                        "position": 1,
                        "artist": "Artist",
                        "title": "Song",
                    }
                ]
            ),
        )
        main(
            [
                "--input",
                str(job),
                "--out",
                str(out_dir),
                "--dry-run",
                "--report",
                str(report),
            ]
        )
        out = capsys.readouterr().out
        assert "Dry-Run Operation Plan" in out
        assert "Safe for next output-folder stage: 1" in out
        assert report.exists()
        data = json.loads(report.read_text(encoding="utf-8"))
        assert data["summary"]["safe_operation_count"] == 1
        assert not out_dir.exists()
    finally:
        cleanup_workspace(workspace)


def test_cli_no_create_subfolder_uses_exact_output_folder():
    workspace = make_workspace()
    try:
        out_dir = workspace / "exact"
        job = write_job(workspace, canonical_job())
        main(["--input", str(job), "--out", str(out_dir), "--no-create-subfolder"])
        session_path = out_dir / "export_session.json"
        assert session_path.exists()
        data = json.loads(session_path.read_text(encoding="utf-8"))
        assert data["output"]["create_subfolder"] is False
        assert data["output"]["final_path"] == str(out_dir.resolve(strict=False))
    finally:
        cleanup_workspace(workspace)


def test_cli_protects_existing_non_empty_output_without_overwrite():
    workspace = make_workspace()
    try:
        out_dir = workspace / "exact"
        out_dir.mkdir()
        marker = out_dir / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        job = write_job(workspace, canonical_job())
        with pytest.raises(SystemExit) as exc_info:
            main(["--input", str(job), "--out", str(out_dir), "--no-create-subfolder"])
        assert exc_info.value.code == 4
        assert marker.read_text(encoding="utf-8") == "keep"
        assert not (out_dir / "export_session.json").exists()
    finally:
        cleanup_workspace(workspace)


def test_cli_overwrite_allows_existing_non_empty_output():
    workspace = make_workspace()
    try:
        out_dir = workspace / "exact"
        out_dir.mkdir()
        (out_dir / "keep.txt").write_text("keep", encoding="utf-8")
        job = write_job(workspace, canonical_job())
        main(
            [
                "--input",
                str(job),
                "--out",
                str(out_dir),
                "--no-create-subfolder",
                "--overwrite",
            ]
        )
        assert (out_dir / "keep.txt").exists()
        assert (out_dir / "export_session.json").exists()
    finally:
        cleanup_workspace(workspace)


def test_cli_sanitizes_playlist_name_for_created_subfolder():
    workspace = make_workspace()
    try:
        raw_job = canonical_job()
        raw_job["playlist"]["name"] = "Bad:Name?*"
        job = write_job(workspace, raw_job)
        out_dir = workspace / "out"
        main(["--input", str(job), "--out", str(out_dir)])
        session_path = find_export_session(out_dir)
        assert session_path.parent.name.startswith("Bad_Name_")
    finally:
        cleanup_workspace(workspace)


def test_cli_reads_txt_input_and_reports_normalization(capsys):
    workspace = make_workspace()
    try:
        txt = workspace / "tracks.txt"
        txt.write_text("one.flac\n", encoding="utf-8")
        main(["--input", str(txt), "--out", str(workspace / "out"), "--dry-run"])
        out = capsys.readouterr().out
        assert "Detected input type: txt" in out
        assert "Input normalized: converted into PlaylistJob structure" in out
        assert "Playlist name: tracks" in out
        assert "Track count: 1" in out
    finally:
        cleanup_workspace(workspace)


def test_cli_allows_explicit_input_type(capsys):
    workspace = make_workspace()
    try:
        txt = workspace / "tracks.data"
        txt.write_text("one.flac\n", encoding="utf-8")
        main(
            [
                "--input",
                str(txt),
                "--input-type",
                "txt",
                "--out",
                str(workspace / "out"),
                "--dry-run",
            ]
        )
        out = capsys.readouterr().out
        assert "Detected input type: txt" in out
    finally:
        cleanup_workspace(workspace)
