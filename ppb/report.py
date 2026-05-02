"""Reporting helpers for dry-run plans, export sessions, and copy results."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ppb.contract import PlaylistJob
from ppb.copier import (
    STATUS_DESTINATION_EXISTS,
    STATUS_FAILED,
    STATUS_NOT_IMPLEMENTED,
    STATUS_SOURCE_MISSING,
    CopyStageResult,
    CopyTrackResult,
)
from ppb.m3u import M3UGenerationResult
from ppb.planner import DryRunPlan


EXPORT_REPORT_TEXT_FILENAME = "export_report.txt"


def dry_run_plan_to_dict(plan: DryRunPlan) -> dict[str, Any]:
    """Return a stable JSON-serializable representation of a dry-run plan."""

    data = asdict(plan)
    data["summary"] = {
        "operation_count": len(plan.operations),
        "blocked_count": len(plan.blocked_tracks),
        "safe_operation_count": len(plan.safe_operations),
        "error_count": plan.error_count,
        "warning_count": plan.warning_count,
        "has_errors": plan.has_errors,
    }
    return data


def write_dry_run_report(plan: DryRunPlan, report_path: Path | str) -> Path:
    """Write a dry-run JSON report without touching music/output files."""

    path = Path(report_path)
    path.write_text(
        json.dumps(dry_run_plan_to_dict(plan), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def export_session_to_dict(
    *,
    job: PlaylistJob,
    plan: DryRunPlan,
    requested_out: Path | str,
    create_subfolder: bool,
    overwrite: bool,
    input_path: Path | str | None = None,
    input_type: str | None = None,
) -> dict[str, Any]:
    """Return the JSON payload handed off to later copy/export stages."""

    return {
        "format": "physical_playlist_export_session.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": {
            "path": str(input_path) if input_path is not None else None,
            "type": input_type,
        },
        "playlist": {
            "name": job.playlist_name,
            "track_count": len(job.tracks),
        },
        "output": {
            "requested_path": str(requested_out),
            "final_path": plan.output_dir,
            "create_subfolder": create_subfolder,
            "overwrite": overwrite,
        },
        "handoff": {
            "final_output_dir": plan.output_dir,
            "safe_operation_count": len(plan.safe_operations),
            "audio_files_copied": False,
        },
        "job": asdict(job),
        "dry_run_plan": dry_run_plan_to_dict(plan),
    }


def write_export_session(
    *,
    job: PlaylistJob,
    plan: DryRunPlan,
    session_path: Path | str,
    requested_out: Path | str,
    create_subfolder: bool,
    overwrite: bool,
    input_path: Path | str | None = None,
    input_type: str | None = None,
) -> Path:
    """Write ``export_session.json`` into the already-created output folder."""

    path = Path(session_path)
    path.write_text(
        json.dumps(
            export_session_to_dict(
                job=job,
                plan=plan,
                requested_out=requested_out,
                create_subfolder=create_subfolder,
                overwrite=overwrite,
                input_path=input_path,
                input_type=input_type,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def export_report_to_dict(
    copy_result: CopyStageResult,
    m3u_result: M3UGenerationResult | None = None,
    *,
    started_at: str | None = None,
    finished_at: str | None = None,
    input_path: Path | str | None = None,
    final_output_dir: Path | str | None = None,
    playlist_name: str | None = None,
    report_txt_path: Path | str | None = None,
    log_path: Path | str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Return the JSON payload for the real copy-stage report."""

    report_finished_at = finished_at or datetime.now(timezone.utc).isoformat()
    output_dir = str(final_output_dir) if final_output_dir is not None else copy_result.output_dir
    warnings, errors = _collect_copy_messages(copy_result)
    if m3u_result is not None:
        warnings.extend(m3u_result.warnings)
        errors.extend(m3u_result.errors)

    report = {
        "format": "physical_playlist_export_report.v1",
        "created_at": report_finished_at,
        "session_id": session_id,
        "started_at": started_at,
        "finished_at": report_finished_at,
        "input_path": str(input_path) if input_path is not None else None,
        "final_output_dir": output_dir,
        "playlist_name": playlist_name,
        "output": {
            "final_path": output_dir,
            "overwrite": copy_result.overwrite,
        },
        "totals": copy_result.summary,
        "summary": copy_result.summary,
        "warnings": warnings,
        "errors": errors,
        "tracks": [asdict(result) for result in copy_result.results],
        "report_txt_path": str(report_txt_path) if report_txt_path is not None else None,
        "log_path": str(log_path) if log_path is not None else None,
    }
    report.update(_m3u_metadata_to_dict(m3u_result))
    return report


def write_export_report(
    copy_result: CopyStageResult,
    report_path: Path | str,
    m3u_result: M3UGenerationResult | None = None,
    *,
    started_at: str | None = None,
    finished_at: str | None = None,
    input_path: Path | str | None = None,
    final_output_dir: Path | str | None = None,
    playlist_name: str | None = None,
    report_txt_path: Path | str | None = None,
    log_path: Path | str | None = None,
    session_id: str | None = None,
) -> Path:
    """Write ``export_report.json`` with per-track copy-stage results."""

    path = Path(report_path)
    path.write_text(
        json.dumps(
            export_report_to_dict(
                copy_result,
                m3u_result=m3u_result,
                started_at=started_at,
                finished_at=finished_at,
                input_path=input_path,
                final_output_dir=final_output_dir,
                playlist_name=playlist_name,
                report_txt_path=report_txt_path,
                log_path=log_path,
                session_id=session_id,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def write_export_report_text(
    copy_result: CopyStageResult,
    report_path: Path | str,
    *,
    m3u_result: M3UGenerationResult | None = None,
    input_path: Path | str | None = None,
    final_output_dir: Path | str | None = None,
    playlist_name: str | None = None,
    report_json_path: Path | str | None = None,
    log_path: Path | str | None = None,
) -> Path:
    """Write a human-readable export report for a completed real run."""

    path = Path(report_path)
    output_dir = str(final_output_dir) if final_output_dir is not None else copy_result.output_dir
    summary = copy_result.summary
    lines: list[str] = [
        "Physical Playlist Builder Export Report",
        "=" * 40,
        f"Playlist name: {playlist_name or '(unknown)'}",
        f"Input path: {input_path if input_path is not None else '(unknown)'}",
        f"Final output folder: {output_dir}",
        "",
        "Totals",
        "-" * 40,
        f"Copied: {summary.get('copied', 0)}",
        f"Skipped: {summary.get('skipped', 0)}",
        f"Failed: {summary.get('failed', 0)}",
        f"Source missing: {summary.get('source_missing', 0)}",
        f"Destination exists: {summary.get('destination_exists', 0)}",
        f"Not implemented: {summary.get('not_implemented', 0)}",
        "",
        "M3U8",
        "-" * 40,
        f"Status: {m3u_result.status if m3u_result is not None else 'not_evaluated'}",
        f"Path: {m3u_result.m3u_path if m3u_result and m3u_result.m3u_path else '(none)'}",
        f"Track count: {m3u_result.track_count if m3u_result is not None else 0}",
        "",
    ]

    _append_track_section(
        lines,
        "Failed or Missing Tracks",
        copy_result.results,
        {STATUS_FAILED, STATUS_SOURCE_MISSING},
    )
    _append_track_section(
        lines,
        "Destination Conflicts",
        copy_result.results,
        {STATUS_DESTINATION_EXISTS},
    )
    _append_track_section(
        lines,
        "Not Implemented Convert Tracks",
        copy_result.results,
        {STATUS_NOT_IMPLEMENTED},
    )

    lines.extend(
        [
            "Generated Files",
            "-" * 40,
            f"export_report.json: {report_json_path if report_json_path is not None else '(none)'}",
            f"export_report.txt: {path}",
            f"export.log: {log_path if log_path is not None else '(none)'}",
            f"playlist.m3u8: {m3u_result.m3u_path if m3u_result and m3u_result.m3u_path else '(none)'}",
            "",
        ]
    )

    warnings, errors = _collect_copy_messages(copy_result)
    if m3u_result is not None:
        warnings.extend(m3u_result.warnings)
        errors.extend(m3u_result.errors)
    _append_message_section(lines, "Warnings", warnings)
    _append_message_section(lines, "Errors", errors)

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def update_export_session_copy_summary(
    *,
    session_path: Path | str,
    copy_result: CopyStageResult,
    report_path: Path | str,
) -> Path:
    """Update ``export_session.json`` with the completed copy-stage handoff."""

    path = Path(session_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    handoff = data.setdefault("handoff", {})
    handoff["audio_files_copied"] = copy_result.summary["copied"] > 0
    handoff["copy_report_path"] = str(report_path)
    handoff["copy_summary"] = copy_result.summary
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _m3u_metadata_to_dict(m3u_result: M3UGenerationResult | None) -> dict[str, Any]:
    if m3u_result is None:
        return {
            "m3u_path": None,
            "m3u_track_count": 0,
            "m3u_status": "not_evaluated",
        }

    data: dict[str, Any] = {
        "m3u_path": m3u_result.m3u_path,
        "m3u_track_count": m3u_result.track_count,
        "m3u_status": m3u_result.status,
    }
    if m3u_result.warnings:
        data["m3u_warnings"] = list(m3u_result.warnings)
    if m3u_result.errors:
        data["m3u_errors"] = list(m3u_result.errors)
    return data


def _collect_copy_messages(copy_result: CopyStageResult) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    for result in copy_result.results:
        warnings.extend(
            f"track {result.position}: {message}" for message in result.warnings if message
        )
        errors.extend(f"track {result.position}: {message}" for message in result.errors if message)
    return warnings, errors


def _append_track_section(
    lines: list[str],
    title: str,
    results: list[CopyTrackResult],
    statuses: set[str],
) -> None:
    lines.extend([title, "-" * 40])
    matching = [result for result in results if result.status in statuses]
    if not matching:
        lines.extend(["None", ""])
        return
    for result in matching:
        lines.append(_format_track_result(result))
        for error in result.errors:
            lines.append(f"  error: {error}")
        for warning in result.warnings:
            lines.append(f"  warning: {warning}")
    lines.append("")


def _format_track_result(result: CopyTrackResult) -> str:
    filename = result.expected_output_filename or "(no output filename)"
    destination = result.destination_path or "(no destination)"
    return (
        f"track {result.position}: {result.status} | {filename} | "
        f"source={result.source_path} | destination={destination}"
    )


def _append_message_section(lines: list[str], title: str, messages: list[str]) -> None:
    lines.extend([title, "-" * 40])
    if not messages:
        lines.extend(["None", ""])
        return
    for message in messages:
        lines.append(message)
    lines.append("")
