"""JSON reporting helpers for dry-run plans, export sessions, and copy results."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ppb.contract import PlaylistJob
from ppb.copier import CopyStageResult
from ppb.m3u import M3UGenerationResult
from ppb.planner import DryRunPlan


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
) -> dict[str, Any]:
    """Return the JSON payload for the real copy-stage report."""

    report = {
        "format": "physical_playlist_export_report.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "output": {
            "final_path": copy_result.output_dir,
            "overwrite": copy_result.overwrite,
        },
        "summary": copy_result.summary,
        "tracks": [asdict(result) for result in copy_result.results],
    }
    report.update(_m3u_metadata_to_dict(m3u_result))
    return report


def write_export_report(
    copy_result: CopyStageResult,
    report_path: Path | str,
    m3u_result: M3UGenerationResult | None = None,
) -> Path:
    """Write ``export_report.json`` with per-track copy-stage results."""

    path = Path(report_path)
    path.write_text(
        json.dumps(
            export_report_to_dict(copy_result, m3u_result=m3u_result),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
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
