"""JSON reporting helpers for dry-run plans."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

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
