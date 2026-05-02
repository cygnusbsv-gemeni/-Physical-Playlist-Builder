"""Logging helpers for real export runs."""

from __future__ import annotations

import logging
from pathlib import Path


EXPORT_LOG_FILENAME = "export.log"
LOGGER_NAME = "ppb.export"


def setup_export_logger(final_output_dir: Path | str) -> tuple[logging.Logger, Path]:
    """Create a per-export log file inside the final output folder."""

    output_dir = Path(final_output_dir).resolve(strict=False)
    log_path = (output_dir / EXPORT_LOG_FILENAME).resolve(strict=False)
    if not _is_relative_to(log_path, output_dir):
        raise OSError(f"Log path escapes the output directory: {log_path}")

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    close_export_logger(logger)

    handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S%z")
    )
    logger.addHandler(handler)
    return logger, log_path


def close_export_logger(logger: logging.Logger | None = None) -> None:
    """Flush and close handlers installed on the export logger."""

    target = logger if logger is not None else logging.getLogger(LOGGER_NAME)
    for handler in list(target.handlers):
        handler.flush()
        handler.close()
        target.removeHandler(handler)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
