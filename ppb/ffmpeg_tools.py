"""Small, isolated ffmpeg helpers for future conversion stages.

This module is intentionally not wired into the main CLI export workflow yet.
It only provides executable discovery and a single-file conversion helper that
writes inside an explicitly supplied output folder.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


STATUS_CONVERTED = "converted"
STATUS_DESTINATION_EXISTS = "destination_exists"
STATUS_FAILED = "failed"
STATUS_FFMPEG_UNAVAILABLE = "ffmpeg_unavailable"
STATUS_SOURCE_MISSING = "source_missing"
STATUS_UNSUPPORTED_FORMAT = "unsupported_format"

SUPPORTED_TARGET_FORMATS = {"mp3", "flac", "wav", "m4a", "aac"}
DEFAULT_MP3_QUALITY = 2

_BITRATE_RE = re.compile(r"^[1-9][0-9]*[kKmM]?$")


@dataclass(frozen=True)
class FfmpegResolutionResult:
    """Structured result for ffmpeg executable discovery."""

    ok: bool
    executable: str | None
    source: str
    explicit: bool
    returncode: int | None = None
    version_line: str | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


@dataclass(frozen=True)
class FfmpegConversionResult:
    """Structured result for a single-file ffmpeg conversion attempt."""

    ok: bool
    status: str
    source_path: str
    destination_path: str
    output_folder: str
    target_format: str
    ffmpeg: FfmpegResolutionResult | None = None
    command: list[str] = field(default_factory=list)
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    stderr_summary: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def resolve_ffmpeg(
    ffmpeg_path: Path | str | None = None,
    *,
    timeout_sec: float = 10.0,
) -> FfmpegResolutionResult:
    """Resolve and validate an ffmpeg executable.

    When ``ffmpeg_path`` is omitted, discovery uses ``ffmpeg`` from ``PATH``.
    Explicit values may be either a command name available on ``PATH`` or a
    concrete executable path. The candidate is validated by running
    ``ffmpeg -version``.
    """

    explicit = ffmpeg_path is not None and str(ffmpeg_path).strip() != ""
    source = "explicit" if explicit else "PATH"

    candidate_result = _resolve_candidate(ffmpeg_path)
    if candidate_result.error:
        return FfmpegResolutionResult(
            ok=False,
            executable=None,
            source=source,
            explicit=explicit,
            error=candidate_result.error,
        )

    executable = candidate_result.executable
    try:
        completed = subprocess.run(
            [executable, "-version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
    except OSError as exc:
        return FfmpegResolutionResult(
            ok=False,
            executable=executable,
            source=source,
            explicit=explicit,
            error=f"ffmpeg executable is not runnable: {exc}",
        )
    except subprocess.TimeoutExpired as exc:
        return FfmpegResolutionResult(
            ok=False,
            executable=executable,
            source=source,
            explicit=explicit,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            error=f"ffmpeg -version timed out after {timeout_sec:g} seconds.",
        )

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if completed.returncode != 0:
        return FfmpegResolutionResult(
            ok=False,
            executable=executable,
            source=source,
            explicit=explicit,
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            error=f"ffmpeg -version failed with exit code {completed.returncode}.",
        )

    return FfmpegResolutionResult(
        ok=True,
        executable=executable,
        source=source,
        explicit=explicit,
        returncode=completed.returncode,
        version_line=_first_nonempty_line(stdout) or _first_nonempty_line(stderr),
        stdout=stdout,
        stderr=stderr,
    )


def convert_audio_file(
    *,
    source_path: Path | str,
    destination_path: Path | str,
    output_folder: Path | str,
    target_format: str,
    ffmpeg_path: Path | str | None = None,
    mp3_quality: int = DEFAULT_MP3_QUALITY,
    audio_bitrate: int | str | None = None,
    overwrite: bool = False,
    timeout_sec: float | None = None,
) -> FfmpegConversionResult:
    """Convert one source audio file into one destination file.

    The source is only ever passed to ffmpeg as an input. The destination must
    resolve inside ``output_folder``. Missing destination parent directories are
    created only after that boundary check succeeds.
    """

    normalized_format = _normalize_target_format(target_format)
    source = Path(source_path).expanduser()
    output = Path(output_folder).expanduser().resolve(strict=False)
    destination = _resolve_destination_path(destination_path, output)

    validation_errors = _validate_conversion_request(
        source=source,
        destination=destination,
        output=output,
        target_format=normalized_format,
        mp3_quality=mp3_quality,
        audio_bitrate=audio_bitrate,
        overwrite=overwrite,
    )
    if validation_errors:
        return FfmpegConversionResult(
            ok=False,
            status=_status_for_validation_errors(validation_errors, normalized_format),
            source_path=str(source),
            destination_path=str(destination),
            output_folder=str(output),
            target_format=normalized_format,
            errors=validation_errors,
        )

    resolution = resolve_ffmpeg(ffmpeg_path)
    if not resolution.ok:
        return FfmpegConversionResult(
            ok=False,
            status=STATUS_FFMPEG_UNAVAILABLE,
            source_path=str(source),
            destination_path=str(destination),
            output_folder=str(output),
            target_format=normalized_format,
            ffmpeg=resolution,
            errors=[resolution.error or "ffmpeg executable could not be resolved."],
        )

    bitrate = _normalize_audio_bitrate(audio_bitrate)
    if bitrate.error:
        return FfmpegConversionResult(
            ok=False,
            status=STATUS_FAILED,
            source_path=str(source),
            destination_path=str(destination),
            output_folder=str(output),
            target_format=normalized_format,
            ffmpeg=resolution,
            errors=[bitrate.error],
        )

    command = _build_ffmpeg_command(
        executable=resolution.executable or "ffmpeg",
        source=source,
        destination=destination,
        target_format=normalized_format,
        mp3_quality=mp3_quality,
        audio_bitrate=bitrate.value,
        overwrite=overwrite,
    )

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return FfmpegConversionResult(
            ok=False,
            status=STATUS_FAILED,
            source_path=str(source),
            destination_path=str(destination),
            output_folder=str(output),
            target_format=normalized_format,
            ffmpeg=resolution,
            command=command,
            errors=[f"Could not create destination folder inside output folder: {exc}"],
        )

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
    except OSError as exc:
        return FfmpegConversionResult(
            ok=False,
            status=STATUS_FAILED,
            source_path=str(source),
            destination_path=str(destination),
            output_folder=str(output),
            target_format=normalized_format,
            ffmpeg=resolution,
            command=command,
            errors=[f"ffmpeg execution failed: {exc}"],
        )
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr or ""
        return FfmpegConversionResult(
            ok=False,
            status=STATUS_FAILED,
            source_path=str(source),
            destination_path=str(destination),
            output_folder=str(output),
            target_format=normalized_format,
            ffmpeg=resolution,
            command=command,
            stdout=exc.stdout or "",
            stderr=stderr,
            stderr_summary=_summarize_stderr(stderr),
            errors=[f"ffmpeg conversion timed out after {timeout_sec:g} seconds."],
        )

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if completed.returncode != 0:
        return FfmpegConversionResult(
            ok=False,
            status=STATUS_FAILED,
            source_path=str(source),
            destination_path=str(destination),
            output_folder=str(output),
            target_format=normalized_format,
            ffmpeg=resolution,
            command=command,
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            stderr_summary=_summarize_stderr(stderr),
            errors=[f"ffmpeg conversion failed with exit code {completed.returncode}."],
        )

    if not destination.is_file():
        return FfmpegConversionResult(
            ok=False,
            status=STATUS_FAILED,
            source_path=str(source),
            destination_path=str(destination),
            output_folder=str(output),
            target_format=normalized_format,
            ffmpeg=resolution,
            command=command,
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            stderr_summary=_summarize_stderr(stderr),
            errors=["ffmpeg reported success but destination file was not created."],
        )

    return FfmpegConversionResult(
        ok=True,
        status=STATUS_CONVERTED,
        source_path=str(source),
        destination_path=str(destination),
        output_folder=str(output),
        target_format=normalized_format,
        ffmpeg=resolution,
        command=command,
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        stderr_summary=_summarize_stderr(stderr),
    )


@dataclass(frozen=True)
class _CandidateResult:
    executable: str
    error: str | None = None


@dataclass(frozen=True)
class _BitrateResult:
    value: str | None
    error: str | None = None


def _resolve_candidate(ffmpeg_path: Path | str | None) -> _CandidateResult:
    raw_value = _strip_surrounding_quotes(str(ffmpeg_path).strip()) if ffmpeg_path is not None else ""

    if not raw_value:
        path_from_env = shutil.which("ffmpeg")
        if path_from_env:
            return _CandidateResult(executable=path_from_env)
        return _CandidateResult(
            executable="",
            error="ffmpeg was not found on PATH. Pass --ffmpeg or install ffmpeg.",
        )

    if _looks_like_command_name(raw_value):
        path_from_env = shutil.which(raw_value)
        if path_from_env:
            return _CandidateResult(executable=path_from_env)
        return _CandidateResult(
            executable="",
            error=f'Explicit ffmpeg command was not found on PATH: "{raw_value}"',
        )

    path = Path(raw_value).expanduser().resolve(strict=False)
    if not path.exists():
        return _CandidateResult(
            executable=str(path),
            error=f"Explicit ffmpeg path does not exist: {path}",
        )
    if not path.is_file():
        return _CandidateResult(
            executable=str(path),
            error=f"Explicit ffmpeg path is not a file: {path}",
        )
    return _CandidateResult(executable=str(path))


def _validate_conversion_request(
    *,
    source: Path,
    destination: Path,
    output: Path,
    target_format: str,
    mp3_quality: int,
    audio_bitrate: int | str | None,
    overwrite: bool,
) -> list[str]:
    errors: list[str] = []

    if target_format not in SUPPORTED_TARGET_FORMATS:
        errors.append(
            "Unsupported target format: "
            f"{target_format or '(empty)'}. Supported formats: "
            f"{', '.join(sorted(SUPPORTED_TARGET_FORMATS))}."
        )
    if not 0 <= mp3_quality <= 9:
        errors.append("mp3_quality must be an integer from 0 to 9.")
    bitrate = _normalize_audio_bitrate(audio_bitrate)
    if bitrate.error:
        errors.append(bitrate.error)

    try:
        source_resolved = source.resolve(strict=True)
    except OSError:
        errors.append(f"Source file does not exist on disk: {source}")
        source_resolved = source.resolve(strict=False)
    if source.exists() and not source.is_file():
        errors.append(f"Source path is not a file: {source}")

    if not _is_relative_to(destination, output) or destination == output:
        errors.append(f"Destination path escapes the output folder: {destination}")
    if destination.parent.exists() and not destination.parent.is_dir():
        errors.append(f"Destination parent exists but is not a folder: {destination.parent}")
    if destination.exists() and not destination.is_file():
        errors.append(f"Destination path exists but is not a file: {destination}")
    if destination.exists() and not overwrite:
        errors.append(f"Destination file already exists: {destination}")
    if destination.resolve(strict=False) == source_resolved:
        errors.append("Destination path must not be the same file as the source path.")

    return errors


def _build_ffmpeg_command(
    *,
    executable: str,
    source: Path,
    destination: Path,
    target_format: str,
    mp3_quality: int,
    audio_bitrate: str | None,
    overwrite: bool,
) -> list[str]:
    command = [
        executable,
        "-hide_banner",
        "-nostdin",
        "-y" if overwrite else "-n",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-map_metadata",
        "-1",
    ]
    command.extend(_codec_args(target_format, mp3_quality=mp3_quality, audio_bitrate=audio_bitrate))
    command.extend(["-f", _muxer_for_format(target_format), str(destination)])
    return command


def _codec_args(
    target_format: str,
    *,
    mp3_quality: int,
    audio_bitrate: str | None,
) -> list[str]:
    if target_format == "mp3":
        args = ["-c:a", "libmp3lame"]
        if audio_bitrate:
            args.extend(["-b:a", audio_bitrate])
        else:
            args.extend(["-q:a", str(mp3_quality)])
        return args
    if target_format == "flac":
        return ["-c:a", "flac"]
    if target_format == "wav":
        return ["-c:a", "pcm_s16le"]
    if target_format in {"m4a", "aac"}:
        args = ["-c:a", "aac"]
        if audio_bitrate:
            args.extend(["-b:a", audio_bitrate])
        return args
    raise ValueError(f"Unsupported target format: {target_format}")


def _muxer_for_format(target_format: str) -> str:
    if target_format == "m4a":
        return "ipod"
    if target_format == "aac":
        return "adts"
    return target_format


def _resolve_destination_path(destination_path: Path | str, output: Path) -> Path:
    destination = Path(destination_path).expanduser()
    if not destination.is_absolute():
        destination = output / destination
    return destination.resolve(strict=False)


def _normalize_target_format(target_format: str) -> str:
    return (target_format or "").strip().lower().lstrip(".")


def _normalize_audio_bitrate(audio_bitrate: int | str | None) -> _BitrateResult:
    if audio_bitrate is None:
        return _BitrateResult(value=None)
    if isinstance(audio_bitrate, int):
        if audio_bitrate <= 0:
            return _BitrateResult(value=None, error="audio_bitrate must be positive.")
        return _BitrateResult(value=f"{audio_bitrate}k")

    value = str(audio_bitrate).strip()
    if not value:
        return _BitrateResult(value=None)
    if not _BITRATE_RE.fullmatch(value):
        return _BitrateResult(
            value=None,
            error="audio_bitrate must be a positive value like 192k, 256k, or 1411k.",
        )
    return _BitrateResult(value=value)


def _status_for_validation_errors(errors: list[str], target_format: str) -> str:
    if target_format not in SUPPORTED_TARGET_FORMATS:
        return STATUS_UNSUPPORTED_FORMAT
    if any("Source file does not exist" in error or "Source path is not a file" in error for error in errors):
        return STATUS_SOURCE_MISSING
    if any("Destination file already exists" in error for error in errors):
        return STATUS_DESTINATION_EXISTS
    return STATUS_FAILED


def _summarize_stderr(stderr: str, *, max_lines: int = 20) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[-max_lines:])


def _first_nonempty_line(value: str) -> str | None:
    for line in value.splitlines():
        line = line.strip()
        if line:
            return line
    return None


def _strip_surrounding_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _looks_like_command_name(value: str) -> bool:
    return (
        Path(value).name == value
        and "\\" not in value
        and "/" not in value
        and os.pathsep not in value
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
