"""juggling-analyser — analyse ball-juggling from motion-capture recordings."""

from juggling_analyser.core import (
    BALL_DIAMETER,
    BALL_MASS,
    GRAVITY,
    HAND_COUNT,
    CleanReport,
    Piece,
    Session,
    Trajectory,
    Uncertainty,
    classify_session,
    classify_trajectory,
)
from juggling_analyser.io import QtmScan, read_qtm, scan_qtm

__version__ = "0.1.0.dev0"

__all__ = [
    "BALL_DIAMETER",
    "BALL_MASS",
    "GRAVITY",
    "HAND_COUNT",
    "CleanReport",
    "Piece",
    "QtmScan",
    "Session",
    "Trajectory",
    "Uncertainty",
    "__version__",
    "classify_session",
    "classify_trajectory",
    "load",
    "read_qtm",
    "scan_qtm",
]


def load(path: str) -> Session:
    """Read a recording and classify its trajectories in one call."""
    session, _report = classify_session(read_qtm(path))
    return session
