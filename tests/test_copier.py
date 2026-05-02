from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ppb.contract import SUPPORTED_FORMAT
from ppb.copier import (
    EXPORT_REPORT_FILENAME,
    STATUS_COPIED,
    STATUS_DESTINATION_EXISTS,
    STATUS_FAILED,
    STATUS_FFMPEG_MISSING,
    STATUS_SKIPPED,
    STATUS_SOURCE_MISSING,
    run_copy_stage,
)
from ppb.input_readers import read_playlist_input
from ppb.planner import build_dry_run_plan
from ppb.report import write_export_report


def write_job(tmp_path: Path, tracks: list[dict], *, output_format: str = "source") -> Path:
    job = {
        "format": SUPPORTED_FORMAT,
        "playlist": {
            "name": "Copy Tests",
            "track_count": len(tracks),
        },
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
    path = tmp_path / "playlist_job.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    return path


def build_plan_from_job(job_path: Path, output_dir: Path):
    input_result = read_playlist_input(job_path)
    assert input_result.validation.ok
    return build_dry_run_plan(input_result.validation.job, output_dir)


def test_copy_stage_copies_track_leaves_source_unchanged_and_writes_report(tmp_path):
    source = tmp_path / "sources" / "song.flac"
    source.parent.mkdir()
    source_bytes = b"source audio bytes"
    source.write_bytes(source_bytes)
    output_dir = tmp_path / "export"
    output_dir.mkdir()
    job_path = write_job(
        tmp_path,
        [
            {
                "position": 1,
                "source_path": str(source),
                "output_filename": "copied.flac",
                "artist": "Artist",
                "title": "Song",
            }
        ],
    )
    plan = build_plan_from_job(job_path, output_dir)

    result = run_copy_stage(plan=plan, final_output_dir=output_dir)
    report_path = output_dir / EXPORT_REPORT_FILENAME
    write_export_report(result, report_path)

    destination = output_dir / "copied.flac"
    assert destination.read_bytes() == source_bytes
    assert source.read_bytes() == source_bytes
    assert result.summary[STATUS_COPIED] == 1
    assert result.results[0].status == STATUS_COPIED
    assert report_path.exists()

    report_data = json.loads(report_path.read_text(encoding="utf-8"))
    assert report_data["summary"][STATUS_COPIED] == 1
    assert report_data["tracks"][0]["status"] == STATUS_COPIED


def test_copy_stage_reports_missing_blocked_and_convert_without_outputs(tmp_path):
    blocked_source = tmp_path / "sources" / "blocked.mp3"
    convert_source = tmp_path / "sources" / "convert.flac"
    blocked_source.parent.mkdir()
    blocked_source.write_bytes(b"blocked")
    convert_source.write_bytes(b"convert")
    missing_source = tmp_path / "sources" / "missing.mp3"
    output_dir = tmp_path / "export"
    output_dir.mkdir()
    job_path = write_job(
        tmp_path,
        [
            {
                "position": 1,
                "source_path": str(missing_source),
                "output_filename": "missing.mp3",
                "artist": "Artist",
                "title": "Missing",
            },
            {
                "position": 2,
                "source_path": str(blocked_source),
                "output_filename": "blocked.mp3",
                "artist": "Artist",
                "title": "Blocked",
                "blockers": ["Blocked by fixture"],
            },
            {
                "position": 3,
                "source_path": str(convert_source),
                "output_filename": "convert.mp3",
                "artist": "Artist",
                "title": "Convert",
            },
        ],
        output_format="mp3",
    )
    plan = build_plan_from_job(job_path, output_dir)

    result = run_copy_stage(plan=plan, final_output_dir=output_dir)

    statuses = {track.position: track.status for track in result.results}
    assert statuses[1] == STATUS_SOURCE_MISSING
    assert statuses[2] == STATUS_SKIPPED
    assert statuses[3] in {STATUS_FAILED, STATUS_FFMPEG_MISSING}
    assert not (output_dir / "missing.mp3").exists()
    assert not (output_dir / "blocked.mp3").exists()
    assert not (output_dir / "convert.mp3").exists()


def test_copy_stage_does_not_overwrite_existing_destination_without_overwrite(tmp_path):
    source = tmp_path / "sources" / "song.flac"
    source.parent.mkdir()
    source.write_bytes(b"new source bytes")
    output_dir = tmp_path / "export"
    output_dir.mkdir()
    existing_destination = output_dir / "song.flac"
    existing_destination.write_bytes(b"existing destination bytes")
    job_path = write_job(
        tmp_path,
        [
            {
                "position": 1,
                "source_path": str(source),
                "output_filename": existing_destination.name,
                "artist": "Artist",
                "title": "Song",
            }
        ],
    )
    plan = build_plan_from_job(job_path, output_dir)

    result = run_copy_stage(plan=plan, final_output_dir=output_dir, overwrite=False)

    assert result.results[0].status == STATUS_DESTINATION_EXISTS
    assert existing_destination.read_bytes() == b"existing destination bytes"
    assert source.read_bytes() == b"new source bytes"
