"""Small, isolated ffmpeg helpers for conversion and loudness processing.

Conversion, loudness measurement, and loudness normalization are used by the
main export workflow only for files inside the selected final output folder.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


STATUS_CONVERTED = "converted"
STATUS_DESTINATION_EXISTS = "destination_exists"
STATUS_FAILED = "failed"
STATUS_FFMPEG_UNAVAILABLE = "ffmpeg_unavailable"
STATUS_SOURCE_MISSING = "source_missing"
STATUS_UNSUPPORTED_FORMAT = "unsupported_format"
STATUS_LOUDNESS_MEASURED = "measured"
STATUS_LOUDNESS_NORMALIZED = "normalized"
STATUS_LOUDNESS_PARSE_FAILED = "loudnorm_parse_failed"

SUPPORTED_TARGET_FORMATS = {"mp3", "flac", "wav", "m4a", "aac"}
DEFAULT_MP3_QUALITY = 2
DEFAULT_TARGET_LUFS = -14.0
DEFAULT_TRUE_PEAK_DB = -1.0
DEFAULT_LOUDNESS_RANGE_LUFS = 11.0

_BITRATE_RE = re.compile(r"^[1-9][0-9]*[kKmM]?$")
_LOUDNORM_REQUIRED_KEYS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
_PERMISSION_RETRY_COUNT = 3
_PERMISSION_RETRY_DELAY_SEC = 0.2


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


@dataclass(frozen=True)
class FfmpegLoudnessMeasurementResult:
    """Structured result for an ffmpeg loudnorm first-pass measurement."""

    success: bool
    status: str
    source_path: str
    ffmpeg: FfmpegResolutionResult | None = None
    command: list[str] = field(default_factory=list)
    return_code: int | None = None
    input_i: float | None = None
    input_tp: float | None = None
    input_lra: float | None = None
    input_thresh: float | None = None
    target_offset: float | None = None
    raw_loudnorm_payload: dict[str, object] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    stderr_summary: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Compatibility alias for callers that use ``ok`` result fields."""

        return self.success


@dataclass(frozen=True)
class FfmpegLoudnessNormalizationResult:
    """Structured result for an ffmpeg loudnorm second-pass normalization."""

    success: bool
    status: str
    source_path: str
    output_path: str | None
    output_folder: str
    target_format: str
    temporary_path: str | None = None
    ffmpeg: FfmpegResolutionResult | None = None
    command: list[str] = field(default_factory=list)
    return_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    stderr_summary: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Compatibility alias for callers that use ``ok`` result fields."""

        return self.success


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

    destination_existed_before = destination.exists()
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
        warnings = _remove_partial_destination(
            destination,
            destination_existed_before=destination_existed_before,
        )
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
            warnings=warnings,
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


def measure_loudness_first_pass(
    *,
    source_path: Path | str,
    ffmpeg: FfmpegResolutionResult | Path | str | None = None,
    target_lufs: float | int | str | None = DEFAULT_TARGET_LUFS,
    true_peak_db: float | int | str | None = DEFAULT_TRUE_PEAK_DB,
    loudness_range_lufs: float | int | str | None = DEFAULT_LOUDNESS_RANGE_LUFS,
    timeout_sec: float | None = None,
) -> FfmpegLoudnessMeasurementResult:
    """Measure loudness with ffmpeg's loudnorm filter first pass.

    The source file is only passed to ffmpeg as an input. The command writes to
    the ``null`` muxer and never creates, rewrites, renames, deletes, or replaces
    audio files.
    """

    source = Path(source_path).expanduser()
    source_resolved = source.resolve(strict=False)

    target = _normalize_loudnorm_target_value(
        target_lufs,
        default=DEFAULT_TARGET_LUFS,
        name="target_lufs",
    )
    true_peak = _normalize_loudnorm_target_value(
        true_peak_db,
        default=DEFAULT_TRUE_PEAK_DB,
        name="true_peak_db",
    )
    lra_target = _normalize_loudnorm_target_value(
        loudness_range_lufs,
        default=DEFAULT_LOUDNESS_RANGE_LUFS,
        name="loudness_range_lufs",
    )
    target_errors = [
        result.error
        for result in (target, true_peak, lra_target)
        if result.error is not None
    ]
    if target_errors:
        return FfmpegLoudnessMeasurementResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(source_resolved),
            errors=target_errors,
        )

    if not source.is_file():
        return FfmpegLoudnessMeasurementResult(
            success=False,
            status=STATUS_SOURCE_MISSING,
            source_path=str(source_resolved),
            errors=[f"Source file does not exist on disk: {source}"],
        )

    resolution = _ensure_ffmpeg_resolution(ffmpeg)
    if not resolution.ok:
        return FfmpegLoudnessMeasurementResult(
            success=False,
            status=STATUS_FFMPEG_UNAVAILABLE,
            source_path=str(source_resolved),
            ffmpeg=resolution,
            errors=[resolution.error or "ffmpeg executable could not be resolved."],
        )

    command = _build_loudness_measurement_command(
        executable=resolution.executable or "ffmpeg",
        source=source,
        target_lufs=target.value,
        true_peak_db=true_peak.value,
        loudness_range_lufs=lra_target.value,
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
        return FfmpegLoudnessMeasurementResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(source_resolved),
            ffmpeg=resolution,
            command=command,
            errors=[f"ffmpeg loudness measurement failed: {exc}"],
        )
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr or ""
        return FfmpegLoudnessMeasurementResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(source_resolved),
            ffmpeg=resolution,
            command=command,
            stdout=exc.stdout or "",
            stderr=stderr,
            stderr_summary=_summarize_stderr(stderr),
            errors=[f"ffmpeg loudness measurement timed out after {timeout_sec:g} seconds."],
        )

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    stderr_summary = _summarize_stderr(stderr)
    parse_result = _parse_loudnorm_payload(stdout=stdout, stderr=stderr)
    if parse_result.error is not None or parse_result.payload is None:
        errors = []
        if completed.returncode != 0:
            errors.append(f"ffmpeg loudness measurement failed with exit code {completed.returncode}.")
        errors.append(parse_result.error or "Could not parse ffmpeg loudnorm JSON output.")
        return FfmpegLoudnessMeasurementResult(
            success=False,
            status=STATUS_LOUDNESS_PARSE_FAILED if completed.returncode == 0 else STATUS_FAILED,
            source_path=str(source_resolved),
            ffmpeg=resolution,
            command=command,
            return_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            stderr_summary=stderr_summary,
            errors=errors,
        )

    payload = parse_result.payload
    values = _extract_loudnorm_values(payload)
    if values.error is not None:
        return FfmpegLoudnessMeasurementResult(
            success=False,
            status=STATUS_LOUDNESS_PARSE_FAILED,
            source_path=str(source_resolved),
            ffmpeg=resolution,
            command=command,
            return_code=completed.returncode,
            raw_loudnorm_payload=payload,
            stdout=stdout,
            stderr=stderr,
            stderr_summary=stderr_summary,
            errors=[values.error],
        )

    if completed.returncode != 0:
        return FfmpegLoudnessMeasurementResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(source_resolved),
            ffmpeg=resolution,
            command=command,
            return_code=completed.returncode,
            input_i=values.input_i,
            input_tp=values.input_tp,
            input_lra=values.input_lra,
            input_thresh=values.input_thresh,
            target_offset=values.target_offset,
            raw_loudnorm_payload=payload,
            stdout=stdout,
            stderr=stderr,
            stderr_summary=stderr_summary,
            errors=[f"ffmpeg loudness measurement failed with exit code {completed.returncode}."],
        )

    return FfmpegLoudnessMeasurementResult(
        success=True,
        status=STATUS_LOUDNESS_MEASURED,
        source_path=str(source_resolved),
        ffmpeg=resolution,
        command=command,
        return_code=completed.returncode,
        input_i=values.input_i,
        input_tp=values.input_tp,
        input_lra=values.input_lra,
        input_thresh=values.input_thresh,
        target_offset=values.target_offset,
        raw_loudnorm_payload=payload,
        stdout=stdout,
        stderr=stderr,
        stderr_summary=stderr_summary,
    )


def normalize_loudness_second_pass(
    *,
    exported_path: Path | str,
    output_folder: Path | str,
    measured_input_i: float | int | str | None,
    measured_input_tp: float | int | str | None,
    measured_input_lra: float | int | str | None,
    measured_input_thresh: float | int | str | None,
    measured_target_offset: float | int | str | None,
    ffmpeg: FfmpegResolutionResult | Path | str | None = None,
    target_lufs: float | int | str | None = DEFAULT_TARGET_LUFS,
    true_peak_db: float | int | str | None = DEFAULT_TRUE_PEAK_DB,
    loudness_range_lufs: float | int | str | None = DEFAULT_LOUDNESS_RANGE_LUFS,
    mp3_quality: int = DEFAULT_MP3_QUALITY,
    audio_bitrate: int | str | None = None,
    timeout_sec: float | None = None,
) -> FfmpegLoudnessNormalizationResult:
    """Normalize an already exported audio file using loudnorm second pass.

    The input must resolve inside ``output_folder``. The command writes to a
    unique temporary output next to the exported file and replaces the exported
    copy only after ffmpeg succeeds. Source audio files outside the final output
    folder are not accepted by this helper.
    """

    output = Path(output_folder).expanduser().resolve(strict=False)
    exported = Path(exported_path).expanduser().resolve(strict=False)
    target_format = _normalize_target_format(exported.suffix)

    target = _normalize_loudnorm_target_value(
        target_lufs,
        default=DEFAULT_TARGET_LUFS,
        name="target_lufs",
    )
    true_peak = _normalize_loudnorm_target_value(
        true_peak_db,
        default=DEFAULT_TRUE_PEAK_DB,
        name="true_peak_db",
    )
    lra_target = _normalize_loudnorm_target_value(
        loudness_range_lufs,
        default=DEFAULT_LOUDNESS_RANGE_LUFS,
        name="loudness_range_lufs",
    )
    measured_values = {
        "measured_input_i": _normalize_required_loudnorm_value(
            measured_input_i,
            name="measured_input_i",
        ),
        "measured_input_tp": _normalize_required_loudnorm_value(
            measured_input_tp,
            name="measured_input_tp",
        ),
        "measured_input_lra": _normalize_required_loudnorm_value(
            measured_input_lra,
            name="measured_input_lra",
        ),
        "measured_input_thresh": _normalize_required_loudnorm_value(
            measured_input_thresh,
            name="measured_input_thresh",
        ),
        "measured_target_offset": _normalize_required_loudnorm_value(
            measured_target_offset,
            name="measured_target_offset",
        ),
    }

    errors = [
        result.error
        for result in (target, true_peak, lra_target, *measured_values.values())
        if result.error is not None
    ]
    errors.extend(
        _validate_loudness_normalization_request(
            exported=exported,
            output=output,
            target_format=target_format,
            mp3_quality=mp3_quality,
            audio_bitrate=audio_bitrate,
        )
    )
    if errors:
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(exported),
            output_path=str(exported),
            output_folder=str(output),
            target_format=target_format,
            errors=errors,
        )

    resolution = _ensure_ffmpeg_resolution(ffmpeg)
    if not resolution.ok:
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FFMPEG_UNAVAILABLE,
            source_path=str(exported),
            output_path=str(exported),
            output_folder=str(output),
            target_format=target_format,
            ffmpeg=resolution,
            errors=[resolution.error or "ffmpeg executable could not be resolved."],
        )

    bitrate = _normalize_audio_bitrate(audio_bitrate)
    if bitrate.error:
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(exported),
            output_path=str(exported),
            output_folder=str(output),
            target_format=target_format,
            ffmpeg=resolution,
            errors=[bitrate.error],
        )

    temp_result = _unique_loudness_temp_path(exported=exported, output=output)
    if temp_result.error is not None or temp_result.path is None:
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(exported),
            output_path=str(exported),
            output_folder=str(output),
            target_format=target_format,
            ffmpeg=resolution,
            errors=[temp_result.error or "Could not create a safe temporary loudness output path."],
        )

    temporary = temp_result.path
    command = _build_loudness_normalization_command(
        executable=resolution.executable or "ffmpeg",
        source=exported,
        destination=temporary,
        target_format=target_format,
        target_lufs=target.value,
        true_peak_db=true_peak.value,
        loudness_range_lufs=lra_target.value,
        measured_input_i=measured_values["measured_input_i"].value,
        measured_input_tp=measured_values["measured_input_tp"].value,
        measured_input_lra=measured_values["measured_input_lra"].value,
        measured_input_thresh=measured_values["measured_input_thresh"].value,
        measured_target_offset=measured_values["measured_target_offset"].value,
        mp3_quality=mp3_quality,
        audio_bitrate=bitrate.value,
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
        warnings = _remove_partial_destination(
            temporary,
            destination_existed_before=False,
        )
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(exported),
            output_path=str(exported),
            output_folder=str(output),
            target_format=target_format,
            temporary_path=str(temporary),
            ffmpeg=resolution,
            command=command,
            warnings=warnings,
            errors=[f"ffmpeg loudness normalization failed: {exc}"],
        )
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr or ""
        warnings = _remove_partial_destination(
            temporary,
            destination_existed_before=False,
        )
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(exported),
            output_path=str(exported),
            output_folder=str(output),
            target_format=target_format,
            temporary_path=str(temporary),
            ffmpeg=resolution,
            command=command,
            stdout=exc.stdout or "",
            stderr=stderr,
            stderr_summary=_summarize_stderr(stderr),
            warnings=warnings,
            errors=[f"ffmpeg loudness normalization timed out after {timeout_sec:g} seconds."],
        )

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    stderr_summary = _summarize_stderr(stderr)
    if completed.returncode != 0:
        warnings = _remove_partial_destination(
            temporary,
            destination_existed_before=False,
        )
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(exported),
            output_path=str(exported),
            output_folder=str(output),
            target_format=target_format,
            temporary_path=str(temporary),
            ffmpeg=resolution,
            command=command,
            return_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            stderr_summary=stderr_summary,
            warnings=warnings,
            errors=[f"ffmpeg loudness normalization failed with exit code {completed.returncode}."],
        )

    if not temporary.is_file():
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(exported),
            output_path=str(exported),
            output_folder=str(output),
            target_format=target_format,
            temporary_path=str(temporary),
            ffmpeg=resolution,
            command=command,
            return_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            stderr_summary=stderr_summary,
            errors=["ffmpeg reported success but temporary normalized file was not created."],
        )

    replace_warnings, replace_error = _replace_exported_file_with_retry(
        temporary=temporary,
        exported=exported,
    )
    if replace_error is not None:
        cleanup_warnings = _remove_partial_destination(
            temporary,
            destination_existed_before=False,
        )
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(exported),
            output_path=str(exported),
            output_folder=str(output),
            target_format=target_format,
            temporary_path=str(temporary),
            ffmpeg=resolution,
            command=command,
            return_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            stderr_summary=stderr_summary,
            warnings=replace_warnings + cleanup_warnings,
            errors=[replace_error],
        )

    if not exported.is_file():
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(exported),
            output_path=str(exported),
            output_folder=str(output),
            target_format=target_format,
            temporary_path=str(temporary),
            ffmpeg=resolution,
            command=command,
            return_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            stderr_summary=stderr_summary,
            errors=["Normalized replacement completed but exported file is missing."],
        )

    return FfmpegLoudnessNormalizationResult(
        success=True,
        status=STATUS_LOUDNESS_NORMALIZED,
        source_path=str(exported),
        output_path=str(exported),
        output_folder=str(output),
        target_format=target_format,
        temporary_path=str(temporary),
        ffmpeg=resolution,
        command=command,
        return_code=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        stderr_summary=stderr_summary,
        warnings=replace_warnings,
    )


def normalize_loudness_and_encode_mp3_from_source(
    *,
    source_path: Path | str,
    final_mp3_path: Path | str,
    final_output_dir: Path | str,
    input_i: float | int | str | None,
    input_tp: float | int | str | None,
    input_lra: float | int | str | None,
    input_thresh: float | int | str | None,
    target_offset: float | int | str | None,
    ffmpeg: FfmpegResolutionResult | Path | str | None = None,
    target_lufs: float | int | str | None = DEFAULT_TARGET_LUFS,
    true_peak_db: float | int | str | None = DEFAULT_TRUE_PEAK_DB,
    loudness_range_lufs: float | int | str | None = DEFAULT_LOUDNESS_RANGE_LUFS,
    mp3_quality: int = DEFAULT_MP3_QUALITY,
    audio_bitrate: int | str | None = None,
    overwrite: bool = False,
    timeout_sec: float | None = None,
) -> FfmpegLoudnessNormalizationResult:
    """Normalize source audio and encode directly to a final MP3 output.

    The source file is only passed to ffmpeg as a read-only input. The helper
    writes a unique temporary MP3 inside ``final_output_dir`` and atomically
    replaces ``final_mp3_path`` only after ffmpeg succeeds and produces a
    non-empty temporary file.
    """

    output = Path(final_output_dir).expanduser().resolve(strict=False)
    source = Path(source_path).expanduser()
    source_resolved = source.resolve(strict=False)
    final_mp3 = _resolve_destination_path(final_mp3_path, output)
    target_format = "mp3"

    target = _normalize_loudnorm_target_value(
        target_lufs,
        default=DEFAULT_TARGET_LUFS,
        name="target_lufs",
    )
    true_peak = _normalize_loudnorm_target_value(
        true_peak_db,
        default=DEFAULT_TRUE_PEAK_DB,
        name="true_peak_db",
    )
    lra_target = _normalize_loudnorm_target_value(
        loudness_range_lufs,
        default=DEFAULT_LOUDNESS_RANGE_LUFS,
        name="loudness_range_lufs",
    )
    measured_values = {
        "measured_input_i": _normalize_required_loudnorm_value(
            input_i,
            name="input_i",
        ),
        "measured_input_tp": _normalize_required_loudnorm_value(
            input_tp,
            name="input_tp",
        ),
        "measured_input_lra": _normalize_required_loudnorm_value(
            input_lra,
            name="input_lra",
        ),
        "measured_input_thresh": _normalize_required_loudnorm_value(
            input_thresh,
            name="input_thresh",
        ),
        "measured_target_offset": _normalize_required_loudnorm_value(
            target_offset,
            name="target_offset",
        ),
    }

    errors = [
        result.error
        for result in (target, true_peak, lra_target, *measured_values.values())
        if result.error is not None
    ]
    errors.extend(
        _validate_fused_mp3_loudness_encode_request(
            source=source,
            source_resolved=source_resolved,
            destination=final_mp3,
            output=output,
            mp3_quality=mp3_quality,
            audio_bitrate=audio_bitrate,
            overwrite=overwrite,
        )
    )
    if errors:
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=_status_for_fused_mp3_validation_errors(errors),
            source_path=str(source_resolved),
            output_path=str(final_mp3),
            output_folder=str(output),
            target_format=target_format,
            errors=errors,
        )

    resolution = _ensure_ffmpeg_resolution(ffmpeg)
    if not resolution.ok:
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FFMPEG_UNAVAILABLE,
            source_path=str(source_resolved),
            output_path=str(final_mp3),
            output_folder=str(output),
            target_format=target_format,
            ffmpeg=resolution,
            errors=[resolution.error or "ffmpeg executable could not be resolved."],
        )

    bitrate = _normalize_audio_bitrate(audio_bitrate)
    if bitrate.error:
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(source_resolved),
            output_path=str(final_mp3),
            output_folder=str(output),
            target_format=target_format,
            ffmpeg=resolution,
            errors=[bitrate.error],
        )

    temp_result = _unique_loudness_temp_path(exported=final_mp3, output=output)
    if temp_result.error is not None or temp_result.path is None:
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(source_resolved),
            output_path=str(final_mp3),
            output_folder=str(output),
            target_format=target_format,
            ffmpeg=resolution,
            errors=[temp_result.error or "Could not create a safe temporary MP3 output path."],
        )

    temporary = temp_result.path
    command = _build_loudness_normalization_command(
        executable=resolution.executable or "ffmpeg",
        source=source,
        destination=temporary,
        target_format=target_format,
        target_lufs=target.value,
        true_peak_db=true_peak.value,
        loudness_range_lufs=lra_target.value,
        measured_input_i=measured_values["measured_input_i"].value,
        measured_input_tp=measured_values["measured_input_tp"].value,
        measured_input_lra=measured_values["measured_input_lra"].value,
        measured_input_thresh=measured_values["measured_input_thresh"].value,
        measured_target_offset=measured_values["measured_target_offset"].value,
        mp3_quality=mp3_quality,
        audio_bitrate=bitrate.value,
    )

    try:
        temporary.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(source_resolved),
            output_path=str(final_mp3),
            output_folder=str(output),
            target_format=target_format,
            temporary_path=str(temporary),
            ffmpeg=resolution,
            command=command,
            errors=[f"Could not create temporary MP3 folder inside final output folder: {exc}"],
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
        warnings = _remove_partial_destination(
            temporary,
            destination_existed_before=False,
        )
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(source_resolved),
            output_path=str(final_mp3),
            output_folder=str(output),
            target_format=target_format,
            temporary_path=str(temporary),
            ffmpeg=resolution,
            command=command,
            warnings=warnings,
            errors=[f"ffmpeg fused MP3 loudness encode failed: {exc}"],
        )
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr or ""
        warnings = _remove_partial_destination(
            temporary,
            destination_existed_before=False,
        )
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(source_resolved),
            output_path=str(final_mp3),
            output_folder=str(output),
            target_format=target_format,
            temporary_path=str(temporary),
            ffmpeg=resolution,
            command=command,
            stdout=exc.stdout or "",
            stderr=stderr,
            stderr_summary=_summarize_stderr(stderr),
            warnings=warnings,
            errors=[f"ffmpeg fused MP3 loudness encode timed out after {timeout_sec:g} seconds."],
        )

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    stderr_summary = _summarize_stderr(stderr)
    if completed.returncode != 0:
        warnings = _remove_partial_destination(
            temporary,
            destination_existed_before=False,
        )
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(source_resolved),
            output_path=str(final_mp3),
            output_folder=str(output),
            target_format=target_format,
            temporary_path=str(temporary),
            ffmpeg=resolution,
            command=command,
            return_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            stderr_summary=stderr_summary,
            warnings=warnings,
            errors=[f"ffmpeg fused MP3 loudness encode failed with exit code {completed.returncode}."],
        )

    if not temporary.is_file() or temporary.stat().st_size <= 0:
        warnings = _remove_partial_destination(
            temporary,
            destination_existed_before=False,
        )
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(source_resolved),
            output_path=str(final_mp3),
            output_folder=str(output),
            target_format=target_format,
            temporary_path=str(temporary),
            ffmpeg=resolution,
            command=command,
            return_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            stderr_summary=stderr_summary,
            warnings=warnings,
            errors=["ffmpeg reported success but temporary MP3 file was missing or empty."],
        )

    replace_warnings, replace_error = _replace_exported_file_with_retry(
        temporary=temporary,
        exported=final_mp3,
    )
    if replace_error is not None:
        cleanup_warnings = _remove_partial_destination(
            temporary,
            destination_existed_before=False,
        )
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(source_resolved),
            output_path=str(final_mp3),
            output_folder=str(output),
            target_format=target_format,
            temporary_path=str(temporary),
            ffmpeg=resolution,
            command=command,
            return_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            stderr_summary=stderr_summary,
            warnings=replace_warnings + cleanup_warnings,
            errors=[replace_error],
        )

    if not final_mp3.is_file() or final_mp3.stat().st_size <= 0:
        return FfmpegLoudnessNormalizationResult(
            success=False,
            status=STATUS_FAILED,
            source_path=str(source_resolved),
            output_path=str(final_mp3),
            output_folder=str(output),
            target_format=target_format,
            temporary_path=str(temporary),
            ffmpeg=resolution,
            command=command,
            return_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            stderr_summary=stderr_summary,
            errors=["Fused MP3 replacement completed but final MP3 is missing or empty."],
        )

    return FfmpegLoudnessNormalizationResult(
        success=True,
        status=STATUS_LOUDNESS_NORMALIZED,
        source_path=str(source_resolved),
        output_path=str(final_mp3),
        output_folder=str(output),
        target_format=target_format,
        temporary_path=str(temporary),
        ffmpeg=resolution,
        command=command,
        return_code=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        stderr_summary=stderr_summary,
        warnings=replace_warnings,
    )


@dataclass(frozen=True)
class _CandidateResult:
    executable: str
    error: str | None = None


@dataclass(frozen=True)
class _BitrateResult:
    value: str | None
    error: str | None = None


@dataclass(frozen=True)
class _FloatResult:
    value: float
    error: str | None = None


@dataclass(frozen=True)
class _TempPathResult:
    path: Path | None
    error: str | None = None


@dataclass(frozen=True)
class _LoudnormParseResult:
    payload: dict[str, object] | None
    error: str | None = None


@dataclass(frozen=True)
class _LoudnormValueResult:
    input_i: float | None = None
    input_tp: float | None = None
    input_lra: float | None = None
    input_thresh: float | None = None
    target_offset: float | None = None
    error: str | None = None


def _replace_exported_file_with_retry(
    *,
    temporary: Path,
    exported: Path,
) -> tuple[list[str], str | None]:
    """Replace exported copy with retry for Windows/cloud sync permission races."""

    warnings: list[str] = []
    last_error: OSError | None = None

    for attempt in range(_PERMISSION_RETRY_COUNT + 1):
        writable_warning = _make_exported_file_writable(exported)
        if writable_warning and writable_warning not in warnings:
            warnings.append(writable_warning)
        try:
            temporary.replace(exported)
            return warnings, None
        except OSError as exc:
            if not _is_permission_error(exc):
                return warnings, f"Could not replace exported file after loudness normalization: {exc}"
            last_error = exc
            if attempt < _PERMISSION_RETRY_COUNT:
                time.sleep(_PERMISSION_RETRY_DELAY_SEC)

    return warnings, (
        "Could not replace exported file after loudness normalization: "
        f"permission denied: {last_error}"
    )


def _make_exported_file_writable(path: Path) -> str | None:
    """Clear read-only mode on an exported copy inside the output workflow."""

    try:
        if not path.is_file():
            return None
        current_mode = path.stat().st_mode
        writable_mode = current_mode | stat.S_IWRITE | stat.S_IWUSR
        if writable_mode != current_mode:
            path.chmod(writable_mode)
    except OSError as exc:
        return f"Could not make exported file writable before loudness replacement: {exc}"
    return None


def _is_permission_error(exc: OSError) -> bool:
    return (
        isinstance(exc, PermissionError)
        or getattr(exc, "winerror", None) == 5
        or getattr(exc, "errno", None) in {13, 5}
    )


def _ensure_ffmpeg_resolution(
    ffmpeg: FfmpegResolutionResult | Path | str | None,
) -> FfmpegResolutionResult:
    if isinstance(ffmpeg, FfmpegResolutionResult):
        return ffmpeg
    return resolve_ffmpeg(ffmpeg)


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


def _validate_loudness_normalization_request(
    *,
    exported: Path,
    output: Path,
    target_format: str,
    mp3_quality: int,
    audio_bitrate: int | str | None,
) -> list[str]:
    errors: list[str] = []

    if not _is_relative_to(exported, output) or exported == output:
        errors.append(f"Refused loudness normalization outside final output folder: {exported}")
    if not exported.is_file():
        errors.append(f"Exported file does not exist on disk: {exported}")
    if target_format not in SUPPORTED_TARGET_FORMATS:
        errors.append(
            "Unsupported normalization target format: "
            f"{target_format or '(empty)'}. Supported formats: "
            f"{', '.join(sorted(SUPPORTED_TARGET_FORMATS))}."
        )
    if not 0 <= mp3_quality <= 9:
        errors.append("mp3_quality must be an integer from 0 to 9.")
    bitrate = _normalize_audio_bitrate(audio_bitrate)
    if bitrate.error:
        errors.append(bitrate.error)

    return errors


def _validate_fused_mp3_loudness_encode_request(
    *,
    source: Path,
    source_resolved: Path,
    destination: Path,
    output: Path,
    mp3_quality: int,
    audio_bitrate: int | str | None,
    overwrite: bool,
) -> list[str]:
    errors: list[str] = []

    if not source.is_file():
        errors.append(f"Source file does not exist on disk: {source}")
    if source.exists() and not source.is_file():
        errors.append(f"Source path is not a file: {source}")

    if not _is_relative_to(destination, output) or destination == output:
        errors.append(f"Final MP3 destination escapes final output folder: {destination}")
    if destination.suffix.lower() != ".mp3":
        errors.append(f"Final MP3 destination must use .mp3 suffix: {destination}")
    if destination.parent.exists() and not destination.parent.is_dir():
        errors.append(f"Final MP3 destination parent exists but is not a folder: {destination.parent}")
    if destination.exists() and not destination.is_file():
        errors.append(f"Final MP3 destination exists but is not a file: {destination}")
    if destination.exists() and not overwrite:
        errors.append(f"Destination file already exists: {destination}")
    if destination.resolve(strict=False) == source_resolved:
        errors.append("Final MP3 destination must not be the same file as the source path.")
    if destination.parent.resolve(strict=False) == source_resolved.parent.resolve(strict=False):
        errors.append("Final MP3 destination must not be written next to the source file.")

    if not 0 <= mp3_quality <= 9:
        errors.append("mp3_quality must be an integer from 0 to 9.")
    bitrate = _normalize_audio_bitrate(audio_bitrate)
    if bitrate.error:
        errors.append(bitrate.error)

    return errors


def _build_loudness_measurement_command(
    *,
    executable: str,
    source: Path,
    target_lufs: float,
    true_peak_db: float,
    loudness_range_lufs: float,
) -> list[str]:
    loudnorm_filter = (
        "loudnorm="
        f"I={_format_ffmpeg_number(target_lufs)}:"
        f"TP={_format_ffmpeg_number(true_peak_db)}:"
        f"LRA={_format_ffmpeg_number(loudness_range_lufs)}:"
        "print_format=json"
    )
    return [
        executable,
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-af",
        loudnorm_filter,
        "-f",
        "null",
        "-",
    ]


def _build_loudness_normalization_command(
    *,
    executable: str,
    source: Path,
    destination: Path,
    target_format: str,
    target_lufs: float,
    true_peak_db: float,
    loudness_range_lufs: float,
    measured_input_i: float,
    measured_input_tp: float,
    measured_input_lra: float,
    measured_input_thresh: float,
    measured_target_offset: float,
    mp3_quality: int,
    audio_bitrate: str | None,
) -> list[str]:
    loudnorm_filter = (
        "loudnorm="
        f"I={_format_ffmpeg_number(target_lufs)}:"
        f"TP={_format_ffmpeg_number(true_peak_db)}:"
        f"LRA={_format_ffmpeg_number(loudness_range_lufs)}:"
        f"measured_I={_format_ffmpeg_number(measured_input_i)}:"
        f"measured_TP={_format_ffmpeg_number(measured_input_tp)}:"
        f"measured_LRA={_format_ffmpeg_number(measured_input_lra)}:"
        f"measured_thresh={_format_ffmpeg_number(measured_input_thresh)}:"
        f"offset={_format_ffmpeg_number(measured_target_offset)}:"
        "linear=true:"
        "print_format=summary"
    )
    command = [
        executable,
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-n",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-map_metadata",
        "-1",
        "-af",
        loudnorm_filter,
    ]
    command.extend(_codec_args(target_format, mp3_quality=mp3_quality, audio_bitrate=audio_bitrate))
    command.extend(["-f", _muxer_for_format(target_format), str(destination)])
    return command


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


def _normalize_loudnorm_target_value(
    value: float | int | str | None,
    *,
    default: float,
    name: str,
) -> _FloatResult:
    raw_value = default if value is None else value
    try:
        normalized = float(raw_value)
    except (TypeError, ValueError):
        return _FloatResult(value=default, error=f"{name} must be a finite number.")
    if not math.isfinite(normalized):
        return _FloatResult(value=default, error=f"{name} must be a finite number.")
    return _FloatResult(value=normalized)


def _normalize_required_loudnorm_value(
    value: float | int | str | None,
    *,
    name: str,
) -> _FloatResult:
    if value is None:
        return _FloatResult(value=0.0, error=f"{name} is required for loudness normalization.")
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return _FloatResult(value=0.0, error=f"{name} must be a finite number.")
    if not math.isfinite(normalized):
        return _FloatResult(value=0.0, error=f"{name} must be a finite number.")
    return _FloatResult(value=normalized)


def _format_ffmpeg_number(value: float) -> str:
    return f"{value:g}"


def _parse_loudnorm_payload(*, stdout: str, stderr: str) -> _LoudnormParseResult:
    decode_errors: list[str] = []
    for stream_name, text in (("stderr", stderr), ("stdout", stdout)):
        candidate = _extract_loudnorm_json_candidate(text)
        if candidate is None:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            decode_errors.append(f"{stream_name}: {exc}")
            continue
        if not isinstance(payload, dict):
            decode_errors.append(f"{stream_name}: loudnorm JSON was not an object.")
            continue
        if not any(key in payload for key in _LOUDNORM_REQUIRED_KEYS):
            decode_errors.append(f"{stream_name}: JSON object did not look like loudnorm output.")
            continue
        return _LoudnormParseResult(payload=payload)

    if decode_errors:
        return _LoudnormParseResult(
            payload=None,
            error="Could not parse ffmpeg loudnorm JSON output: " + "; ".join(decode_errors),
        )
    return _LoudnormParseResult(
        payload=None,
        error="Could not find ffmpeg loudnorm JSON output in stderr or stdout.",
    )


def _extract_loudnorm_json_candidate(text: str) -> str | None:
    marker_index = text.rfind('"input_i"')
    if marker_index == -1:
        return None
    start_index = text.rfind("{", 0, marker_index)
    end_index = text.find("}", marker_index)
    if start_index == -1 or end_index == -1:
        return None
    return text[start_index : end_index + 1]


def _extract_loudnorm_values(payload: dict[str, object]) -> _LoudnormValueResult:
    values: dict[str, float] = {}
    for key in _LOUDNORM_REQUIRED_KEYS:
        if key not in payload:
            return _LoudnormValueResult(error=f"Missing loudnorm field: {key}")
        try:
            values[key] = float(str(payload[key]).strip())
        except (TypeError, ValueError):
            return _LoudnormValueResult(error=f"Loudnorm field is not numeric: {key}")
    return _LoudnormValueResult(
        input_i=values["input_i"],
        input_tp=values["input_tp"],
        input_lra=values["input_lra"],
        input_thresh=values["input_thresh"],
        target_offset=values["target_offset"],
    )


def _status_for_validation_errors(errors: list[str], target_format: str) -> str:
    if target_format not in SUPPORTED_TARGET_FORMATS:
        return STATUS_UNSUPPORTED_FORMAT
    if any("Source file does not exist" in error or "Source path is not a file" in error for error in errors):
        return STATUS_SOURCE_MISSING
    if any("Destination file already exists" in error for error in errors):
        return STATUS_DESTINATION_EXISTS
    return STATUS_FAILED


def _status_for_fused_mp3_validation_errors(errors: list[str]) -> str:
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


def _remove_partial_destination(destination: Path, *, destination_existed_before: bool) -> list[str]:
    if destination_existed_before or not destination.is_file():
        return []
    try:
        destination.unlink()
    except OSError as exc:
        return [f"Could not remove partial failed conversion output: {exc}"]
    return []


def _unique_loudness_temp_path(*, exported: Path, output: Path) -> _TempPathResult:
    parent = exported.parent.resolve(strict=False)
    if not _is_relative_to(parent, output):
        return _TempPathResult(
            path=None,
            error=f"Temporary loudness output parent escapes final output folder: {parent}",
        )

    suffix = exported.suffix or ".tmp"
    pid = os.getpid()
    for index in range(1, 1000):
        candidate = parent / f".{exported.stem}.ppb-loudnorm-{pid}-{index}.tmp{suffix}"
        candidate = candidate.resolve(strict=False)
        if candidate == exported or not _is_relative_to(candidate, output):
            continue
        if not candidate.exists():
            return _TempPathResult(path=candidate)

    return _TempPathResult(
        path=None,
        error=f"Could not find a non-existing temporary loudness output path near: {exported}",
    )


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
