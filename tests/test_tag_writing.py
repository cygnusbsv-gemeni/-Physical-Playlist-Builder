from __future__ import annotations

import hashlib
import json
import logging
import math
import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ppb.cli import main, run_tag_writing_stage
from ppb.contract import PlaylistJob, PlaylistSettings, SUPPORTED_FORMAT, TrackEntry
from ppb.copier import (
    STATUS_CONVERTED,
    STATUS_COPIED,
    STATUS_FAILED,
    STATUS_FFMPEG_MISSING,
    STATUS_SKIPPED,
    STATUS_SOURCE_MISSING,
    CopyStageResult,
    CopyTrackResult,
)
from ppb.ffmpeg_tools import resolve_ffmpeg
from ppb.tags import (
    ID3_VERSION_V23,
    STATUS_FAILED as TAG_HELPER_STATUS_FAILED,
    STATUS_OUTSIDE_OUTPUT_DIR,
    STATUS_UNSUPPORTED_FORMAT,
    STATUS_WRITTEN,
    TagWriteResult,
    write_tags_to_exported_file,
)


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
    write_tags: bool = True,
    normalize_loudness: bool = False,
    playlist_name: str = "Tag Tests",
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
    path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    return path


def read_report(output_dir: Path) -> dict:
    return json.loads((output_dir / "export_report.json").read_text(encoding="utf-8"))


def ffmpeg_or_skip() -> None:
    if not resolve_ffmpeg().ok:
        pytest.skip("ffmpeg is not available in this environment")


def m4a_or_skip(tmp_path: Path) -> None:
    resolution = resolve_ffmpeg()
    if not resolution.ok or not resolution.executable:
        pytest.skip("ffmpeg is not available in this environment")

    probe_root = tmp_path / "m4a_probe"
    source = probe_root / "probe.wav"
    destination = probe_root / "probe.m4a"
    write_sine_wav(source)
    completed = subprocess.run(
        [resolution.executable, "-y", "-i", str(source), str(destination)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0 or not destination.is_file():
        pytest.skip("ffmpeg in this environment cannot create M4A fixtures")


def assert_mp3_requested_fields_absent(path: Path) -> None:
    from mutagen.id3 import ID3, ID3NoHeaderError

    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        return

    for frame_id in ("TIT2", "TPE1", "TALB", "TPE2", "TRCK", "TDRC", "TCON"):
        assert frame_id not in tags


def make_logger() -> logging.Logger:
    logger = logging.getLogger(f"ppb-tag-tests-{id(object())}")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger


def test_tag_helper_refuses_target_outside_final_output_dir(tmp_path):
    output_dir = tmp_path / "export"
    output_dir.mkdir()
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"not an exported file")

    result = write_tags_to_exported_file(
        file_path=outside,
        final_output_dir=output_dir,
        metadata={"title": "Ignored"},
    )

    assert result.status == STATUS_OUTSIDE_OUTPUT_DIR
    assert result.success is False
    assert "outside final output directory" in (result.error or "")


def test_tag_helper_reports_unsupported_wav_without_crashing(tmp_path):
    output_dir = tmp_path / "export"
    output_dir.mkdir()
    exported = output_dir / "tone.wav"
    write_sine_wav(exported)

    result = write_tags_to_exported_file(
        file_path=exported,
        final_output_dir=output_dir,
        metadata={"title": "Tone"},
    )

    assert result.status == STATUS_UNSUPPORTED_FORMAT
    assert result.success is False
    assert "Unsupported tag-writing file type" in (result.error or "")


def test_cli_mp3_tags_v24_write_cyrillic_and_preserve_source_with_reports_logs_and_m3u(tmp_path):
    pytest.importorskip("mutagen")
    ffmpeg_or_skip()
    from mutagen.id3 import ID3

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
                "output_filename": "cyrillic.mp3",
                "title": "Песня",
                "artist": "Исполнитель",
                "album": "Альбом",
                "albumartist": "Разные артисты",
                "tracknumber": "7",
                "genre": "Рок",
                "date": "2024-05-03",
            }
        ],
        output_format="mp3",
        write_tags=True,
    )

    main(
        [
            "--input",
            str(job),
            "--out",
            str(output_dir),
            "--no-create-subfolder",
            "--id3-version",
            "v24",
        ]
    )

    destination = output_dir / "cyrillic.mp3"
    assert destination.is_file()
    assert sha256(source) == source_hash_before

    tags = ID3(str(destination))
    assert tags.version[1] == 4
    assert tags["TIT2"].text[0] == "Песня"
    assert tags["TPE1"].text[0] == "Исполнитель"
    assert tags["TALB"].text[0] == "Альбом"
    assert tags["TPE2"].text[0] == "Разные артисты"
    assert tags["TRCK"].text[0] == "7"
    assert tags["TCON"].text[0] == "Рок"

    report = read_report(output_dir)
    track = report["tracks"][0]
    assert track["status"] == STATUS_CONVERTED
    assert track["tag_status"] == "written"
    assert track["tag_format"] == "id3v2.4"
    assert track["tag_written_fields"] == [
        "title",
        "artist",
        "album",
        "albumartist",
        "tracknumber",
        "date",
        "genre",
    ]
    assert report["tags_status"] == "written"
    assert report["tag_totals"]["written"] == 1

    report_text = (output_dir / "export_report.txt").read_text(encoding="utf-8")
    assert "Tag Writing" in report_text
    assert "Status: written" in report_text
    assert "Written: 1" in report_text

    log_text = (output_dir / "export.log").read_text(encoding="utf-8")
    assert "tag writing started" in log_text
    assert "track 1 tag written" in log_text
    assert "tag writing completed" in log_text

    playlist_text = (output_dir / "playlist.m3u8").read_text(encoding="utf-8")
    assert "cyrillic.mp3" in playlist_text
    assert str(destination) not in playlist_text


def test_cli_mp3_tags_v23_write_id3v23_when_requested(tmp_path):
    pytest.importorskip("mutagen")
    ffmpeg_or_skip()
    from mutagen.id3 import ID3

    source = tmp_path / "sources" / "tone.wav"
    write_sine_wav(source)
    output_dir = tmp_path / "export"
    job = write_job(
        tmp_path,
        [
            {
                "position": 1,
                "source_path": str(source),
                "output_filename": "legacy.mp3",
                "title": "Старый тег",
                "artist": "Артист",
                "album": "Архив",
                "tracknumber": "3",
            }
        ],
        output_format="mp3",
        write_tags=True,
    )

    main(
        [
            "--input",
            str(job),
            "--out",
            str(output_dir),
            "--no-create-subfolder",
            "--id3-version",
            ID3_VERSION_V23,
        ]
    )

    destination = output_dir / "legacy.mp3"
    tags = ID3(str(destination))
    assert tags.version[1] == 3
    assert tags["TIT2"].text[0] == "Старый тег"
    assert tags["TPE1"].text[0] == "Артист"
    assert tags["TALB"].text[0] == "Архив"
    assert tags["TRCK"].text[0] == "3"

    report = read_report(output_dir)
    assert report["tracks"][0]["tag_format"] == "id3v2.3"
    assert report["tags"]["id3_version"] == ID3_VERSION_V23


def test_cli_flac_writes_vorbiscomment_tags_when_supported(tmp_path):
    pytest.importorskip("mutagen")
    ffmpeg_or_skip()
    from mutagen.flac import FLAC

    source = tmp_path / "sources" / "tone.wav"
    write_sine_wav(source)
    output_dir = tmp_path / "export"
    job = write_job(
        tmp_path,
        [
            {
                "position": 1,
                "source_path": str(source),
                "output_filename": "album.flac",
                "title": "Трек FLAC",
                "artist": "FLAC Artist",
                "album": "FLAC Album",
                "albumartist": "FLAC Album Artist",
                "tracknumber": "2",
                "genre": "Ambient",
                "year": "2004",
            }
        ],
        output_format="flac",
        write_tags=True,
    )

    main(["--input", str(job), "--out", str(output_dir), "--no-create-subfolder"])

    destination = output_dir / "album.flac"
    tags = FLAC(str(destination))
    assert tags["TITLE"] == ["Трек FLAC"]
    assert tags["ARTIST"] == ["FLAC Artist"]
    assert tags["ALBUM"] == ["FLAC Album"]
    assert tags["ALBUMARTIST"] == ["FLAC Album Artist"]
    assert tags["TRACKNUMBER"] == ["2"]
    assert tags["GENRE"] == ["Ambient"]
    assert tags["DATE"] == ["2004"]

    report = read_report(output_dir)
    assert report["tracks"][0]["tag_status"] == "written"
    assert report["tracks"][0]["tag_format"] == "vorbiscomment"


def test_cli_m4a_writes_mp4_tags_when_supported(tmp_path):
    pytest.importorskip("mutagen")
    m4a_or_skip(tmp_path)
    from mutagen.mp4 import MP4

    source = tmp_path / "sources" / "tone.wav"
    write_sine_wav(source)
    output_dir = tmp_path / "export"
    job = write_job(
        tmp_path,
        [
            {
                "position": 1,
                "source_path": str(source),
                "output_filename": "portable.m4a",
                "title": "M4A Title",
                "artist": "M4A Artist",
                "album": "M4A Album",
                "albumartist": "M4A Album Artist",
                "tracknumber": "4/12",
                "genre": "Pop",
                "date": "2025",
            }
        ],
        output_format="m4a",
        write_tags=True,
    )

    main(["--input", str(job), "--out", str(output_dir), "--no-create-subfolder"])

    destination = output_dir / "portable.m4a"
    tags = MP4(str(destination)).tags
    assert tags is not None
    assert tags["\xa9nam"] == ["M4A Title"]
    assert tags["\xa9ART"] == ["M4A Artist"]
    assert tags["\xa9alb"] == ["M4A Album"]
    assert tags["aART"] == ["M4A Album Artist"]
    assert tags["trkn"] == [(4, 12)]
    assert tags["\xa9gen"] == ["Pop"]
    assert tags["\xa9day"] == ["2025"]

    report = read_report(output_dir)
    assert report["tracks"][0]["tag_status"] == "written"
    assert report["tracks"][0]["tag_format"] == "mp4"


def test_cli_write_tags_false_skips_tagging_and_leaves_exported_mp3_without_requested_fields(tmp_path):
    pytest.importorskip("mutagen")
    ffmpeg_or_skip()

    source = tmp_path / "sources" / "tone.wav"
    write_sine_wav(source)
    output_dir = tmp_path / "export"
    job = write_job(
        tmp_path,
        [
            {
                "position": 1,
                "source_path": str(source),
                "output_filename": "untagged.mp3",
                "title": "Should Not Be Written",
                "artist": "No Writer",
            }
        ],
        output_format="mp3",
        write_tags=False,
    )

    main(["--input", str(job), "--out", str(output_dir), "--no-create-subfolder"])

    destination = output_dir / "untagged.mp3"
    assert destination.is_file()
    assert_mp3_requested_fields_absent(destination)

    report = read_report(output_dir)
    assert report["tags_status"] == "skipped"
    assert "settings.write_tags is false" in (report["tags_reason"] or "")
    assert report["tracks"][0]["tag_status"] == "skipped"


def test_cli_skip_tags_overrides_job_setting_and_records_skip_in_report_and_log(tmp_path):
    pytest.importorskip("mutagen")
    ffmpeg_or_skip()

    source = tmp_path / "sources" / "tone.wav"
    write_sine_wav(source)
    output_dir = tmp_path / "export"
    job = write_job(
        tmp_path,
        [
            {
                "position": 1,
                "source_path": str(source),
                "output_filename": "skip-me.mp3",
                "title": "Should Not Be Written",
                "artist": "Skipped Artist",
            }
        ],
        output_format="mp3",
        write_tags=True,
    )

    main(
        [
            "--input",
            str(job),
            "--out",
            str(output_dir),
            "--no-create-subfolder",
            "--skip-tags",
        ]
    )

    destination = output_dir / "skip-me.mp3"
    assert destination.is_file()
    assert_mp3_requested_fields_absent(destination)

    report = read_report(output_dir)
    assert report["tags_status"] == "skipped"
    assert report["tags"]["skip_tags"] is True
    assert "--skip-tags was passed." in (report["tags_reason"] or "")
    assert report["tracks"][0]["tag_status"] == "skipped"
    assert "--skip-tags was passed." in report["tracks"][0]["tag_warnings"]

    log_text = (output_dir / "export.log").read_text(encoding="utf-8")
    assert "tag skipped" in log_text
    assert "--skip-tags was passed." in log_text


def test_cli_wav_export_stays_successful_and_reports_unsupported_format(tmp_path):
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
                "title": "WAV Title",
                "artist": "WAV Artist",
            }
        ],
        output_format="source",
        write_tags=True,
    )

    main(["--input", str(job), "--out", str(output_dir), "--no-create-subfolder"])

    destination = output_dir / "tone.wav"
    assert destination.is_file()

    report = read_report(output_dir)
    assert report["tracks"][0]["status"] == STATUS_COPIED
    assert report["tracks"][0]["tag_status"] == "unsupported_format"
    assert report["tags_status"] == "unsupported_format"

    report_text = (output_dir / "export_report.txt").read_text(encoding="utf-8")
    assert "Unsupported format: 1" in report_text

    log_text = (output_dir / "export.log").read_text(encoding="utf-8")
    assert "unsupported_format" in log_text

    playlist_text = (output_dir / "playlist.m3u8").read_text(encoding="utf-8")
    assert "tone.wav" in playlist_text


def test_run_tag_writing_stage_skips_non_exported_results_and_never_targets_source_paths(tmp_path, monkeypatch):
    output_dir = tmp_path / "export"
    output_dir.mkdir()
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    copied_source = source_dir / "copied.wav"
    copied_source.write_bytes(b"source bytes")
    copied_destination = output_dir / "copied.mp3"
    copied_destination.write_bytes(b"exported bytes")

    calls: list[dict[str, object]] = []

    def fake_write_tags_to_exported_file(**kwargs):
        calls.append(kwargs)
        return TagWriteResult(
            success=True,
            status=STATUS_WRITTEN,
            file_path=str(kwargs["file_path"]),
            tag_format="id3v2.4",
            written_fields=["title"],
        )

    monkeypatch.setattr("ppb.cli.write_tags_to_exported_file", fake_write_tags_to_exported_file)

    job = PlaylistJob(
        format=SUPPORTED_FORMAT,
        playlist_name="Tag Stage",
        settings=PlaylistSettings(write_tags=True),
        tracks=[
            TrackEntry(source_path=str(copied_source), position=1, title="Copied"),
            TrackEntry(source_path=str(source_dir / "failed.wav"), position=2, title="Failed"),
            TrackEntry(source_path=str(source_dir / "skipped.wav"), position=3, title="Skipped"),
            TrackEntry(source_path=str(source_dir / "missing.wav"), position=4, title="Missing"),
            TrackEntry(source_path=str(source_dir / "ffmpeg.wav"), position=5, title="FFmpeg"),
        ],
    )
    copy_result = CopyStageResult(
        output_dir=str(output_dir),
        overwrite=False,
        results=[
            CopyTrackResult(
                position=1,
                source_path=str(copied_source),
                destination_path=str(copied_destination),
                expected_output_filename="copied.mp3",
                planned_action="copy",
                status=STATUS_COPIED,
            ),
            CopyTrackResult(
                position=2,
                source_path=str(source_dir / "failed.wav"),
                destination_path=None,
                expected_output_filename="failed.mp3",
                planned_action="convert",
                status=STATUS_FAILED,
            ),
            CopyTrackResult(
                position=3,
                source_path=str(source_dir / "skipped.wav"),
                destination_path=None,
                expected_output_filename="skipped.mp3",
                planned_action="skip",
                status=STATUS_SKIPPED,
            ),
            CopyTrackResult(
                position=4,
                source_path=str(source_dir / "missing.wav"),
                destination_path=None,
                expected_output_filename="missing.mp3",
                planned_action="error",
                status=STATUS_SOURCE_MISSING,
            ),
            CopyTrackResult(
                position=5,
                source_path=str(source_dir / "ffmpeg.wav"),
                destination_path=None,
                expected_output_filename="ffmpeg.mp3",
                planned_action="convert",
                status=STATUS_FFMPEG_MISSING,
            ),
        ],
    )

    results, summary = run_tag_writing_stage(
        job=job,
        copy_result=copy_result,
        final_output_dir=output_dir,
        skip_tags=False,
        id3_version="v24",
        logger=make_logger(),
    )

    assert len(calls) == 1
    assert Path(str(calls[0]["file_path"])).resolve(strict=False) == copied_destination.resolve(
        strict=False
    )
    assert Path(str(calls[0]["file_path"])).resolve(strict=False) != copied_source.resolve(
        strict=False
    )
    assert calls[0]["final_output_dir"] == output_dir
    assert "source_path" not in calls[0]["metadata"]
    assert results[0]["tag_status"] == "written"
    assert [result["tag_status"] for result in results[1:]] == [
        "skipped",
        "skipped",
        "skipped",
        "skipped",
    ]
    assert summary["totals"]["written"] == 1
    assert summary["totals"]["skipped"] == 4


def test_cli_tag_failure_keeps_exported_audio_and_records_tag_error(tmp_path, monkeypatch):
    ffmpeg_or_skip()
    source = tmp_path / "sources" / "tone.wav"
    write_sine_wav(source)
    output_dir = tmp_path / "export"
    job = write_job(
        tmp_path,
        [
            {
                "position": 1,
                "source_path": str(source),
                "output_filename": "failure.mp3",
                "title": "Broken Tag",
                "artist": "Broken Artist",
            }
        ],
        output_format="mp3",
        write_tags=True,
    )

    def fake_write_tags_to_exported_file(**kwargs):
        return TagWriteResult(
            success=False,
            status=TAG_HELPER_STATUS_FAILED,
            file_path=str(kwargs["file_path"]),
            tag_format="id3v2.4",
            error="Simulated tag failure",
        )

    monkeypatch.setattr("ppb.cli.write_tags_to_exported_file", fake_write_tags_to_exported_file)

    main(["--input", str(job), "--out", str(output_dir), "--no-create-subfolder"])

    destination = output_dir / "failure.mp3"
    assert destination.is_file()

    report = read_report(output_dir)
    track = report["tracks"][0]
    assert track["status"] == STATUS_CONVERTED
    assert track["tag_status"] == "failed"
    assert track["tag_error"] == "Simulated tag failure"
    assert report["tags_status"] == "failed"

    log_text = (output_dir / "export.log").read_text(encoding="utf-8")
    assert "tag failed" in log_text

    playlist_text = (output_dir / "playlist.m3u8").read_text(encoding="utf-8")
    assert "failure.mp3" in playlist_text
