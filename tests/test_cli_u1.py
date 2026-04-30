"""
tests/test_cli_u1.py — Stage U1 smoke tests.

Tests:
- CLI prints summary for a valid JSON input
- CLI exits with error for a missing input file
- CLI exits with error for malformed JSON
"""

from __future__ import annotations

import json
import sys
import pytest
from pathlib import Path

# Allow running tests from the project root without install
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ppb.cli import main


def write_job(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "playlist_job.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


class TestCliSummary:
    def test_prints_summary_for_valid_json(self, tmp_path, capsys):
        job = write_job(tmp_path, {
            "schema": "physical_playlist_job.v1",
            "playlist_name": "Road Trip",
            "tracks": [
                {"source_path": "/music/01.flac", "position": 1},
                {"source_path": "/music/02.flac", "position": 2},
            ],
        })
        main(["--input", str(job), "--out", str(tmp_path / "out")])
        out = capsys.readouterr().out
        assert "Road Trip" in out
        assert "2" in out  # track count

    def test_dry_run_flag_shown_in_summary(self, tmp_path, capsys):
        job = write_job(tmp_path, {
            "schema": "physical_playlist_job.v1",
            "playlist_name": "Mix",
            "tracks": [],
        })
        main(["--input", str(job), "--out", str(tmp_path / "out"), "--dry-run"])
        out = capsys.readouterr().out
        assert "YES" in out or "dry-run" in out.lower()

    def test_missing_input_exits_with_error(self, tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            main(["--input", str(tmp_path / "nonexistent.json"), "--out", str(tmp_path / "out")])
        assert exc_info.value.code != 0

    def test_malformed_json_exits_with_error(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{ not valid json }", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            main(["--input", str(bad), "--out", str(tmp_path / "out")])
        assert exc_info.value.code != 0

    def test_no_output_files_created(self, tmp_path, capsys):
        """Stage U1 must not create any files in the output folder."""
        out_dir = tmp_path / "out"
        job = write_job(tmp_path, {
            "schema": "physical_playlist_job.v1",
            "playlist_name": "Empty",
            "tracks": [],
        })
        main(["--input", str(job), "--out", str(out_dir)])
        assert not out_dir.exists(), "Output folder must NOT be created in Stage U1"
