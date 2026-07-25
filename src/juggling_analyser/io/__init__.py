"""Ingestion and persistence. Depends on ``core``; nothing in ``core`` depends here.

``qtm`` is the pipeline's only ingestion path. ``tsv`` is a validation oracle used
by the tests and never by the pipeline (DESIGN.md §4).
"""

from .qtm import QtmScan, TrajectoryObject, read_qtm, scan_qtm

__all__ = ["QtmScan", "TrajectoryObject", "read_qtm", "scan_qtm"]
