from __future__ import annotations

import hashlib
import json
import math
import struct
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ppb.cli import main
from ppb.contract import SUPPORTED_FORMAT
from ppb.copier import (
    STATUS_CONVERTED,
    STATUS_DESTINATION_EXISTS,
    STATUS_FAILED,
    STATUS_FFMPEG_MISSING,
    run_copy_stage,
)
from ppb.ffmpeg_tools import resolve_ffmpeg
from ppb.input_readers import read_playlist_input
from ppb.planner import build_dry_run_plan


def write_sine_wav(path: Path, *, duration_sec: float = 0.2, sample_rate: int = 44100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = int(duration_sec * sample_rate)
    amplitude = 12000
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        frames = bytearray()
        for index in range(frame_count):
            sample = int(amplitude * math.sin(2 * math.pi * 440 * index / sample_rate))
            frames.extend(struct.pack("<h", sample))
        handle.writeframes(bytes(frames))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_job(
    tmp_path: Path,
    tracks: list[dict],
    *,
    output_format: str = "mp3",
    playlist_name: str = "Conversion Tests",
) -> Path:
    job = {
        "format": SUPPORTED_FORMAT,
        "playlist": {
            "name": playlist_name,
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


def ffmpeg_or_skip() -> None:
    if not resolve_ffmpeg().ok:
        pytest.skip("ffmpeg is not available in this environment")


def read_report(output_dir: Path) -> dict:
    return json.loads((output_dir / "export_report.json").read_text(encoding="utf-8"))


def test_cli_converts_wav_to_mp3_and_writes_reports_log_and_m3u(tmp_path):
    ffmpeg_or_skip()
    source = tmp_path / "sources" / "tone.wav"
    write_sine_wav(source)
    source_hash_before = sha256(source)
    output_dir = tmp_path / "export"
    job = write_job(
        tmp_path,
        [
            {
                "position": 1,
                "source_path": str(source),
                "output_filename": "converted.mp3",
                "artist": "Artist",
                "title": "Tone",
                "duration_sec": 1,
            }
        ],
    )

    main(["--input", str(job), "--out", str(output_dir), "--no-create-subfolder"])

    destination = output_dir / "converted.mp3"
    assert destination.is_file()
    assert destination.parent.resolve(strict=False) == output_dir.resolve(strict=False)
    assert sorted(tmp_path.rglob("converted.mp3")) == [destination]
    assert sha256(source) == source_hash_before

    report = read_report(output_dir)
    assert report["tracks"][0]["status"] == STATUS_CONVERTED
    assert report["tracks"][0]["destination_path"] == str(destination.resolve(strict=False))
    assert report["totals"][STATUS_CONVERTED] == 1

    report_text = (output_dir / "export_report.txt").read_text(encoding="utf-8")
    assert "Converted: 1" in report_text

    log_text = (output_dir / "export.log").read_text(encoding="utf-8")
    assert "converted to mp3" in log_text

    playlist_text = (output_dir / "playlist.m3u8").read_text(encoding="utf-8")
    assert "#EXTM3U" in playlist_text
    assert "converted.mp3" in playlist_text
    assert str(destination) not in playlist_text


def test_cli_invalid_explicit_ffmpeg_keeps_copy_and_excludes_failed_convert_from_m3u(tmp_path):
    copy_source = tmp_path / "sources" / "already.mp3"
    copy_source.parent.mkdir(parents=True)
    copy_source.write_bytes(b"copy fixture bytes")
    convert_source = tmp_path / "sources" / "needs-convert.wav"
    write_sine_wav(convert_source)
    output_dir = tmp_path / "export"
    missing_ffmpeg = tmp_path / "tools" / "missing-ffmpeg.exe"
    job = write_job(
        tmp_path,
        [
            {
                "position": 1,
                "source_path": str(copy_source),
                "output_filename": "copied.mp3",
                "artist": "Artist",
                "title": "Copied",
            },
            {
                "position": 2,
                "source_path": str(convert_source),
                "output_filename": "converted.mp3",
                "artist": "Artist",
                "title": "Converted",
            },
        ],
    )

    main(
        [
            "--input",
            str(job),
            "--out",
            str(output_dir),
            "--no-create-subfolder",
            "--ffmpeg",
            str(missing_ffmpeg),
        ]
    )

    assert (output_dir / "copied.mp3").read_bytes() == b"copy fixture bytes"
    assert not (output_dir / "converted.mp3").exists()

    report = read_report(output_dir)
    statuses = {track["position"]: track["status"] for track in report["tracks"]}
    assert statuses[1] == "copied"
    assert statuses[2] == STATUS_FFMPEG_MISSING
    assert report["totals"][STATUS_FFMPEG_MISSING] == 1

    playlist_text = (output_dir / "playlist.m3u8").read_text(encoding="utf-8")
    assert "copied.mp3" in playlist_text
    assert "converted.mp3" not in playlist_text

    log_text = (output_dir / "export.log").read_text(encoding="utf-8")
    assert "ffmpeg missing" in log_text


def test_cli_bad_audio_conversion_failure_records_stderr_and_excludes_from_m3u(tmp_path):
    ffmpeg_or_skip()
    bad_source = tmp_path / "sources" / "bad.wav"
    bad_source.parent.mkdir(parents=True)
    bad_source.write_bytes(b"this is not a valid wav file")
    source_hash_before = sha256(bad_source)
    output_dir = tmp_path / "export"
    job = write_job(
        tmp_path,
        [
            {
                "position": 1,
                "source_path": str(bad_source),
                "output_filename": "bad.mp3",
                "artist": "Artist",
                "title": "Bad",
            }
        ],
    )

    main(["--input", str(job), "--out", str(output_dir), "--no-create-subfolder"])

    assert sha256(bad_source) == source_hash_before
    report = read_report(output_dir)
    track = report["tracks"][0]
    assert track["status"] == STATUS_FAILED
    assert track["ffmpeg_stderr_summary"]
    assert report["totals"][STATUS_FAILED] == 1

    report_text = (output_dir / "export_report.txt").read_text(encoding="utf-8")
    assert "ffmpeg stderr summary:" in report_text

    log_text = (output_dir / "export.log").read_text(encoding="utf-8")
    assert "ffmpeg stderr summary" in log_text

    playlist_text = (output_dir / "playlist.m3u8").read_text(encoding="utf-8")
    assert "bad.mp3" not in playlist_text
    assert not (output_dir / "bad.mp3").exists()


def test_convert_destination_conflict_is_not_overwritten_without_overwrite(tmp_path):
    source = tmp_path / "sources" / "tone.wav"
    write_sine_wav(source)
    source_hash_before = sha256(source)
    output_dir = tmp_path / "export"
    output_dir.mkdir()
    destination = output_dir / "tone.mp3"
    destination.write_bytes(b"existing destination bytes")
    destination_before = destination.read_bytes()
    job = write_job(
        tmp_path,
        [
            {
                "position": 1,
                "source_path": str(source),
                "output_filename": destination.name,
                "artist": "Artist",
                "title": "Tone",
            }
        ],
    )
    plan = build_plan_from_job(job, output_dir)

    result = run_copy_stage(plan=plan, final_output_dir=output_dir, overwrite=False)

    assert result.results[0].status == STATUS_DESTINATION_EXISTS
    assert destination.read_bytes() == destination_before
    assert sha256(source) == source_hash_before


def test_convert_overwrite_replaces_existing_destination_when_ffmpeg_available(tmp_path):
    ffmpeg_or_skip()
    source = tmp_path / "sources" / "tone.wav"
    write_sine_wav(source)
    source_hash_before = sha256(source)
    output_dir = tmp_path / "export"
    output_dir.mkdir()
    destination = output_dir / "tone.mp3"
    destination.write_bytes(b"existing destination bytes")
    job = write_job(
        tmp_path,
        [
            {
                "position": 1,
                "source_path": str(source),
                "output_filename": destination.name,
                "artist": "Artist",
                "title": "Tone",
            }
        ],
    )
    plan = build_plan_from_job(job, output_dir)

    result = run_copy_stage(plan=plan, final_output_dir=output_dir, overwrite=True)

    assert result.results[0].status == STATUS_CONVERTED
    assert destination.is_file()
    assert destination.read_bytes() != b"existing destination bytes"
    assert sha256(source) == source_hash_before
