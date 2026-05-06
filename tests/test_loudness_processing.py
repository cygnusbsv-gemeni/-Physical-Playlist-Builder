from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import struct
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ppb.cli import main
from ppb.contract import SUPPORTED_FORMAT
from ppb.ffmpeg_tools import (
    FfmpegResolutionResult,
    normalize_loudness_second_pass,
    resolve_ffmpeg,
)
from ppb.report import (
    LOUDNESS_STATUS_FAILED,
    LOUDNESS_STATUS_FFMPEG_MISSING,
    LOUDNESS_STATUS_MEASURED,
    LOUDNESS_STATUS_NORMALIZED,
    LOUDNESS_STATUS_SKIPPED,
)


def write_sine_wav(path: Path, *, duration_sec: float = 0.5, sample_rate: int = 44100) -> None:
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
    normalize_loudness: bool = True,
    output_format: str = "source",
    playlist_name: str = "Loudness Tests",
    write_tags: bool = False,
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
            "normalize_loudness": normalize_loudness,
            "target_lufs": -14.0,
            "true_peak_db": -1.0,
            "write_tags": write_tags,
            "generate_m3u8": True,
            "filename_template": "{position:02d} - {artist} - {title}",
        },
        "tracks": tracks,
    }
    path = tmp_path / "playlist_job.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    return path


def read_report(output_dir: Path) -> dict:
    return json.loads((output_dir / "export_report.json").read_text(encoding="utf-8"))


def loudnorm_temp_files(output_dir: Path) -> list[Path]:
    return sorted(output_dir.rglob("*.ppb-loudnorm-*.tmp*"))


def ffmpeg_or_skip() -> None:
    if not resolve_ffmpeg().ok:
        pytest.skip("ffmpeg is not available in this environment")


def test_cli_normalize_loudness_success_reports_logs_m3u_and_preserves_source(tmp_path):
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
                "output_filename": "tone.wav",
                "artist": "Artist",
                "title": "Tone",
                "duration_sec": 1,
            }
        ],
    )

    main(["--input", str(job), "--out", str(output_dir), "--no-create-subfolder"])

    destination = output_dir / "tone.wav"
    assert destination.is_file()
    assert sha256(source) == source_hash_before
    assert not loudnorm_temp_files(output_dir)

    report = read_report(output_dir)
    track = report["tracks"][0]
    assert track["status"] == "copied"
    assert track["destination_path"] == str(destination.resolve(strict=False))
    assert track["loudness_status"] == LOUDNESS_STATUS_MEASURED
    assert track["loudness_normalization_status"] == LOUDNESS_STATUS_NORMALIZED
    assert track["post_loudness_status"] == LOUDNESS_STATUS_MEASURED
    assert isinstance(track["input_i"], float)
    assert isinstance(track["input_tp"], float)
    assert isinstance(track["input_lra"], float)
    assert isinstance(track["input_thresh"], float)
    assert isinstance(track["target_offset"], float)
    assert isinstance(track["post_input_i"], float)
    assert isinstance(track["post_input_tp"], float)
    assert isinstance(track["post_input_lra"], float)
    assert isinstance(track["post_input_thresh"], float)
    assert isinstance(track["post_target_offset"], float)
    assert track["normalized_output_path"] == str(destination.resolve(strict=False))
    assert track["size_after_export_before_loudness"] is not None
    assert track["size_after_loudness"] == destination.stat().st_size
    assert track["final_size"] == destination.stat().st_size
    assert report["loudness_totals"][LOUDNESS_STATUS_MEASURED] == 1
    assert report["loudness_totals"][LOUDNESS_STATUS_NORMALIZED] == 1
    assert report["loudness_verification_totals"]["verified"] == 1

    report_text = (output_dir / "export_report.txt").read_text(encoding="utf-8")
    assert "Measured: 1" in report_text
    assert "Normalized: 1" in report_text
    assert "Post-normalization verified: 1" in report_text

    log_text = (output_dir / "export.log").read_text(encoding="utf-8")
    assert "loudness measured" in log_text
    assert "loudness normalized" in log_text
    assert "post-normalization loudness verified" in log_text

    playlist_text = (output_dir / "playlist.m3u8").read_text(encoding="utf-8")
    assert "#EXTM3U" in playlist_text
    assert "tone.wav" in playlist_text
    assert str(destination) not in playlist_text


def test_cli_loudness_processing_uses_exported_copy_paths_not_sources(tmp_path, monkeypatch):
    source = tmp_path / "sources" / "tone.wav"
    write_sine_wav(source)
    source_hash_before = sha256(source)
    output_dir = tmp_path / "export"
    destination = output_dir / "tone.wav"
    observed_measurement_paths: list[Path] = []
    observed_normalization_paths: list[Path] = []
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

    def fake_measure_loudness_first_pass(**kwargs):
        measured = Path(kwargs["source_path"]).resolve(strict=False)
        observed_measurement_paths.append(measured)
        return SimpleNamespace(
            success=True,
            status="measured",
            input_i=-20.0,
            input_tp=-2.0,
            input_lra=3.0,
            input_thresh=-30.0,
            target_offset=0.5,
            return_code=0,
            stderr_summary="",
            errors=[],
            ffmpeg=kwargs.get("ffmpeg"),
        )

    def fake_normalize_loudness_second_pass(**kwargs):
        exported = Path(kwargs["exported_path"]).resolve(strict=False)
        observed_normalization_paths.append(exported)
        return SimpleNamespace(
            success=True,
            status="normalized",
            output_path=str(exported),
            return_code=0,
            stderr_summary="",
            errors=[],
        )

    monkeypatch.setattr("ppb.cli.measure_loudness_first_pass", fake_measure_loudness_first_pass)
    monkeypatch.setattr("ppb.cli.normalize_loudness_second_pass", fake_normalize_loudness_second_pass)

    main(["--input", str(job), "--out", str(output_dir), "--no-create-subfolder"])

    assert destination.is_file()
    assert sha256(source) == source_hash_before
    assert observed_measurement_paths == [
        destination.resolve(strict=False),
        destination.resolve(strict=False),
    ]
    assert observed_normalization_paths == [destination.resolve(strict=False)]
    assert source.resolve(strict=False) not in observed_measurement_paths
    assert source.resolve(strict=False) not in observed_normalization_paths


def test_cli_skip_loudness_verification_avoids_post_measurement_and_reports_progress(
    tmp_path,
    monkeypatch,
    capsys,
):
    source = tmp_path / "sources" / "tone.wav"
    write_sine_wav(source)
    source_hash_before = sha256(source)
    output_dir = tmp_path / "export"
    destination = output_dir / "tone.wav"
    observed_measurement_paths: list[Path] = []
    observed_normalization_paths: list[Path] = []
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

    def fake_measure_loudness_first_pass(**kwargs):
        measured = Path(kwargs["source_path"]).resolve(strict=False)
        observed_measurement_paths.append(measured)
        return SimpleNamespace(
            success=True,
            status="measured",
            input_i=-20.0,
            input_tp=-2.0,
            input_lra=3.0,
            input_thresh=-30.0,
            target_offset=0.5,
            return_code=0,
            stderr_summary="",
            errors=[],
            ffmpeg=kwargs.get("ffmpeg"),
        )

    def fake_normalize_loudness_second_pass(**kwargs):
        exported = Path(kwargs["exported_path"]).resolve(strict=False)
        observed_normalization_paths.append(exported)
        return SimpleNamespace(
            success=True,
            status="normalized",
            output_path=str(exported),
            return_code=0,
            stderr_summary="",
            errors=[],
            ffmpeg=kwargs.get("ffmpeg"),
        )

    monkeypatch.setattr("ppb.cli.measure_loudness_first_pass", fake_measure_loudness_first_pass)
    monkeypatch.setattr("ppb.cli.normalize_loudness_second_pass", fake_normalize_loudness_second_pass)

    main(
        [
            "--input",
            str(job),
            "--out",
            str(output_dir),
            "--no-create-subfolder",
            "--skip-loudness-verification",
        ]
    )

    out = capsys.readouterr().out
    assert "[loudness] Starting:" in out
    assert "verification=skipped" in out
    assert "[loudness] 1/1 measuring:" in out
    assert "[loudness] 1/1 normalizing:" in out
    assert "[loudness] 1/1 verifying:" not in out

    assert destination.is_file()
    assert sha256(source) == source_hash_before
    assert observed_measurement_paths == [destination.resolve(strict=False)]
    assert observed_normalization_paths == [destination.resolve(strict=False)]

    report = read_report(output_dir)
    track = report["tracks"][0]
    assert track["loudness_status"] == LOUDNESS_STATUS_MEASURED
    assert track["loudness_normalization_status"] == LOUDNESS_STATUS_NORMALIZED
    assert track["post_loudness_status"] == LOUDNESS_STATUS_SKIPPED
    assert "--skip-loudness-verification" in track["post_loudness_skip_reason"]
    assert report["loudness"]["skip_loudness_verification"] is True
    assert report["loudness_verification_totals"]["verified"] == 0
    assert report["loudness_verification_totals"][LOUDNESS_STATUS_SKIPPED] == 1

    report_text = (output_dir / "export_report.txt").read_text(encoding="utf-8")
    assert "Post-normalization verification skipped: 1" in report_text


def test_resolved_integrated_loudness_warning_is_preserved_as_input_warning_only(tmp_path, monkeypatch):
    source = tmp_path / "sources" / "tone.wav"
    write_sine_wav(source)
    output_dir = tmp_path / "export"
    warning = "No integrated loudness value is available yet."
    job = write_job(
        tmp_path,
        [
            {
                "position": 1,
                "source_path": str(source),
                "output_filename": "tone.wav",
                "artist": "Artist",
                "title": "Tone",
                "warnings": [{"message": warning}],
            }
        ],
    )

    def fake_measure_loudness_first_pass(**kwargs):
        return SimpleNamespace(
            success=True,
            status="measured",
            input_i=-20.0,
            input_tp=-2.0,
            input_lra=3.0,
            input_thresh=-30.0,
            target_offset=0.5,
            return_code=0,
            stderr_summary="",
            errors=[],
            ffmpeg=kwargs.get("ffmpeg"),
        )

    def fake_normalize_loudness_second_pass(**kwargs):
        exported = Path(kwargs["exported_path"]).resolve(strict=False)
        return SimpleNamespace(
            success=True,
            status="normalized",
            output_path=str(exported),
            return_code=0,
            stderr_summary="",
            errors=[],
            ffmpeg=kwargs.get("ffmpeg"),
        )

    monkeypatch.setattr("ppb.cli.measure_loudness_first_pass", fake_measure_loudness_first_pass)
    monkeypatch.setattr("ppb.cli.normalize_loudness_second_pass", fake_normalize_loudness_second_pass)

    main(["--input", str(job), "--out", str(output_dir), "--no-create-subfolder"])

    report = read_report(output_dir)
    assert f"track 1: {warning}" in report["input_warnings"]
    assert f"track 1: {warning}" not in report["warnings"]
    assert report["tracks"][0]["input_warnings"] == [warning]

    report_text = (output_dir / "export_report.txt").read_text(encoding="utf-8")
    warnings_section = report_text.split("\nWarnings\n", 1)[1].split("\nErrors\n", 1)[0]
    assert warning not in warnings_section
    assert "None" in warnings_section


def test_successful_conversion_ffmpeg_stderr_is_not_logged_as_error(tmp_path, monkeypatch):
    source = tmp_path / "sources" / "tone.wav"
    write_sine_wav(source)
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
            }
        ],
        normalize_loudness=False,
        output_format="mp3",
    )

    def fake_convert_audio_file(**kwargs):
        Path(kwargs["destination_path"]).write_bytes(b"converted bytes")
        return SimpleNamespace(
            ok=True,
            status="converted",
            target_format="mp3",
            errors=[],
            warnings=[],
            returncode=0,
            stderr_summary="ffmpeg progress line on stderr",
        )

    monkeypatch.setattr("ppb.copier.convert_audio_file", fake_convert_audio_file)

    main(["--input", str(job), "--out", str(output_dir), "--no-create-subfolder"])

    report = read_report(output_dir)
    assert report["tracks"][0]["ffmpeg_stderr_summary"] == "ffmpeg progress line on stderr"

    log_text = (output_dir / "export.log").read_text(encoding="utf-8")
    stderr_lines = [line for line in log_text.splitlines() if "ffmpeg stderr summary" in line]
    assert stderr_lines
    assert all(" ERROR " not in line for line in stderr_lines)
    assert any(" INFO " in line for line in stderr_lines)


def test_write_tags_true_reports_unsupported_format_for_wav_without_crashing(tmp_path):
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
                "output_filename": "tone.wav",
                "artist": "Artist",
                "title": "Tone",
            }
        ],
        normalize_loudness=False,
        write_tags=True,
    )

    main(["--input", str(job), "--out", str(output_dir), "--no-create-subfolder"])

    assert sha256(source) == source_hash_before
    report = read_report(output_dir)
    assert report["tags_status"] == "unsupported_format"
    assert report["tracks"][0]["tags_status"] == "unsupported_format"
    assert report["tracks"][0]["tag_error"]
    assert "Unsupported tag-writing file type" in report["tracks"][0]["tag_error"]

    report_text = (output_dir / "export_report.txt").read_text(encoding="utf-8")
    assert "Status: unsupported_format" in report_text
    assert "Unsupported format: 1" in report_text


def test_cli_skip_loudness_records_skips_and_keeps_exported_audio(tmp_path):
    source = tmp_path / "sources" / "tone.wav"
    write_sine_wav(source)
    output_dir = tmp_path / "export"
    job = write_job(
        tmp_path,
        [
            {
                "position": 1,
                "source_path": str(source),
                "output_filename": "tone.wav",
                "artist": "Artist",
                "title": "Tone",
            }
        ],
    )

    main(
        [
            "--input",
            str(job),
            "--out",
            str(output_dir),
            "--no-create-subfolder",
            "--skip-loudness",
        ]
    )

    assert (output_dir / "tone.wav").is_file()
    report = read_report(output_dir)
    track = report["tracks"][0]
    assert track["loudness_status"] == LOUDNESS_STATUS_SKIPPED
    assert track["loudness_normalization_status"] == LOUDNESS_STATUS_SKIPPED
    assert "--skip-loudness" in track["loudness_skip_reason"]
    assert report["loudness"]["status"] == LOUDNESS_STATUS_SKIPPED
    assert report["loudness_totals"][LOUDNESS_STATUS_SKIPPED] == 1


def test_cli_normalize_loudness_false_records_clear_skip(tmp_path):
    source = tmp_path / "sources" / "tone.wav"
    write_sine_wav(source)
    output_dir = tmp_path / "export"
    job = write_job(
        tmp_path,
        [
            {
                "position": 1,
                "source_path": str(source),
                "output_filename": "tone.wav",
                "artist": "Artist",
                "title": "Tone",
            }
        ],
        normalize_loudness=False,
    )

    main(["--input", str(job), "--out", str(output_dir), "--no-create-subfolder"])

    assert (output_dir / "tone.wav").is_file()
    report = read_report(output_dir)
    track = report["tracks"][0]
    assert track["loudness_status"] == LOUDNESS_STATUS_SKIPPED
    assert track["loudness_normalization_status"] == LOUDNESS_STATUS_SKIPPED
    assert "settings.normalize_loudness is false" in track["loudness_skip_reason"]
    assert "settings.normalize_loudness is false" in report["loudness"]["reason"]

    report_text = (output_dir / "export_report.txt").read_text(encoding="utf-8")
    assert "Reason: settings.normalize_loudness is false." in report_text

    log_text = (output_dir / "export.log").read_text(encoding="utf-8")
    assert "settings.normalize_loudness is false" in log_text


def test_cli_loudness_ffmpeg_missing_keeps_copy_and_m3u_with_invalid_explicit_ffmpeg(tmp_path):
    source = tmp_path / "sources" / "tone.wav"
    write_sine_wav(source)
    source_hash_before = sha256(source)
    output_dir = tmp_path / "export"
    missing_ffmpeg = tmp_path / "tools" / "missing-ffmpeg.exe"
    job = write_job(
        tmp_path,
        [
            {
                "position": 1,
                "source_path": str(source),
                "output_filename": "tone.wav",
                "artist": "Artist",
                "title": "Tone",
            }
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

    destination = output_dir / "tone.wav"
    assert destination.is_file()
    assert sha256(source) == source_hash_before

    report = read_report(output_dir)
    track = report["tracks"][0]
    assert track["status"] == "copied"
    assert track["loudness_status"] == LOUDNESS_STATUS_FFMPEG_MISSING
    assert track["loudness_normalization_status"] == LOUDNESS_STATUS_FFMPEG_MISSING
    assert report["loudness_totals"][LOUDNESS_STATUS_FFMPEG_MISSING] == 1

    playlist_text = (output_dir / "playlist.m3u8").read_text(encoding="utf-8")
    assert "tone.wav" in playlist_text


def test_cli_bad_copied_audio_records_loudness_failure_and_removes_temp_files(tmp_path):
    ffmpeg_or_skip()
    source = tmp_path / "sources" / "bad.wav"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"not a valid wav file")
    source_bytes_before = source.read_bytes()
    output_dir = tmp_path / "export"
    job = write_job(
        tmp_path,
        [
            {
                "position": 1,
                "source_path": str(source),
                "output_filename": "bad.wav",
                "artist": "Artist",
                "title": "Bad",
            }
        ],
    )

    main(["--input", str(job), "--out", str(output_dir), "--no-create-subfolder"])

    destination = output_dir / "bad.wav"
    assert destination.read_bytes() == source_bytes_before
    assert source.read_bytes() == source_bytes_before
    assert not loudnorm_temp_files(output_dir)

    report = read_report(output_dir)
    track = report["tracks"][0]
    assert track["status"] == "copied"
    assert track["loudness_status"] == LOUDNESS_STATUS_FAILED
    assert track["loudness_normalization_status"] == LOUDNESS_STATUS_FAILED
    assert track["loudness_error"]


def test_failed_normalization_removes_own_temp_output_and_keeps_exported_copy(tmp_path):
    output_dir = tmp_path / "export"
    exported = output_dir / "tone.wav"
    write_sine_wav(exported)
    exported_bytes_before = exported.read_bytes()
    fake_ffmpeg = write_fake_ffmpeg_that_creates_partial_and_fails(tmp_path)
    resolution = FfmpegResolutionResult(
        ok=True,
        executable=str(fake_ffmpeg),
        source="test",
        explicit=True,
    )

    result = normalize_loudness_second_pass(
        exported_path=exported,
        output_folder=output_dir,
        measured_input_i=-20.0,
        measured_input_tp=-2.0,
        measured_input_lra=3.0,
        measured_input_thresh=-30.0,
        measured_target_offset=0.5,
        ffmpeg=resolution,
    )

    assert not result.success
    assert result.status == "failed"
    assert exported.read_bytes() == exported_bytes_before
    assert not loudnorm_temp_files(output_dir)


def write_fake_ffmpeg_that_creates_partial_and_fails(tmp_path: Path) -> Path:
    script_code = (
        "import pathlib\n"
        "import sys\n"
        "destination = pathlib.Path(sys.argv[-1])\n"
        "destination.write_bytes(b'partial normalized bytes')\n"
        "print('forced normalization failure', file=sys.stderr)\n"
        "raise SystemExit(1)\n"
    )
    script = tmp_path / "fake_ffmpeg.py"
    script.write_text(script_code, encoding="utf-8")

    if os.name == "nt":
        launcher = tmp_path / "fake_ffmpeg.cmd"
        launcher.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n',
            encoding="utf-8",
        )
        return launcher

    launcher = tmp_path / "fake_ffmpeg"
    launcher.write_text(f"#!{sys.executable}\n{script_code}", encoding="utf-8")
    launcher.chmod(0o755)
    return launcher


def test_loudness_normalization_replaces_read_only_exported_copy(tmp_path):
    output_dir = tmp_path / "export"
    output_dir.mkdir()
    exported = output_dir / "tone.wav"
    exported.write_bytes(b"original exported bytes")
    exported.chmod(exported.stat().st_mode & ~stat.S_IWRITE)
    fake_ffmpeg = write_fake_ffmpeg_that_creates_output_and_succeeds(tmp_path)
    resolution = FfmpegResolutionResult(
        ok=True,
        executable=str(fake_ffmpeg),
        source="test",
        explicit=True,
    )

    try:
        result = normalize_loudness_second_pass(
            exported_path=exported,
            output_folder=output_dir,
            measured_input_i=-20.0,
            measured_input_tp=-2.0,
            measured_input_lra=3.0,
            measured_input_thresh=-30.0,
            measured_target_offset=0.5,
            ffmpeg=resolution,
        )
    finally:
        if exported.exists():
            exported.chmod(exported.stat().st_mode | stat.S_IWRITE)

    assert result.success
    assert result.status == "normalized"
    assert exported.read_bytes() == b"normalized bytes"
    assert not loudnorm_temp_files(output_dir)


def test_loudness_replace_permission_failure_keeps_exported_copy_and_removes_temp(
    tmp_path,
    monkeypatch,
):
    output_dir = tmp_path / "export"
    output_dir.mkdir()
    exported = output_dir / "tone.wav"
    exported.write_bytes(b"original exported bytes")
    fake_ffmpeg = write_fake_ffmpeg_that_creates_output_and_succeeds(tmp_path)
    resolution = FfmpegResolutionResult(
        ok=True,
        executable=str(fake_ffmpeg),
        source="test",
        explicit=True,
    )
    original_replace = Path.replace

    def fake_replace(self, target):
        if ".ppb-loudnorm-" in self.name:
            raise PermissionError(13, "simulated permission denied")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fake_replace)
    monkeypatch.setattr("ppb.ffmpeg_tools._PERMISSION_RETRY_COUNT", 1)
    monkeypatch.setattr("ppb.ffmpeg_tools._PERMISSION_RETRY_DELAY_SEC", 0)

    result = normalize_loudness_second_pass(
        exported_path=exported,
        output_folder=output_dir,
        measured_input_i=-20.0,
        measured_input_tp=-2.0,
        measured_input_lra=3.0,
        measured_input_thresh=-30.0,
        measured_target_offset=0.5,
        ffmpeg=resolution,
    )

    assert not result.success
    assert result.status == "failed"
    assert "permission denied" in "; ".join(result.errors).lower()
    assert exported.read_bytes() == b"original exported bytes"
    assert not loudnorm_temp_files(output_dir)


def write_fake_ffmpeg_that_creates_output_and_succeeds(tmp_path: Path) -> Path:
    script_code = (
        "import pathlib\n"
        "import sys\n"
        "destination = pathlib.Path(sys.argv[-1])\n"
        "destination.write_bytes(b'normalized bytes')\n"
        "print('forced normalization success', file=sys.stderr)\n"
    )
    script = tmp_path / "fake_ffmpeg_success.py"
    script.write_text(script_code, encoding="utf-8")

    if os.name == "nt":
        launcher = tmp_path / "fake_ffmpeg_success.cmd"
        launcher.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n',
            encoding="utf-8",
        )
        return launcher

    launcher = tmp_path / "fake_ffmpeg_success"
    launcher.write_text(f"#!{sys.executable}\n{script_code}", encoding="utf-8")
    launcher.chmod(0o755)
    return launcher
