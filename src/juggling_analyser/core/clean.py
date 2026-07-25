"""Reject trajectories that are not ball paths (DESIGN.md §2, §3).

A recording contains more than balls: static reference markers on the rig, and
short-lived spurious trajectories from reflections and reconstruction errors. The
phantom *series* that an ungated reader used to invent are gone — the reader now
accepts only real trajectory objects (DESIGN.md §4) — so what is left here is a
genuine classification problem over real marker paths.

``kind`` is one of ``ball`` / ``spurious`` / ``unknown``. ``unknown`` is a real
answer, not a failure: a trajectory long enough to matter but without a clear
ballistic signature is exactly what the next stage should look at, and forcing it
into a bucket here would hide it.

This module classifies on lifetime and gross geometry only. The physics test —
does the path actually follow ``a = (0, 0, −g)`` — belongs to ``core.flight``
(§6) and refines these labels once flights are segmented.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .trajectory import Kind, Session, Trajectory

#: A real throw never spans more than this in any axis; beyond it the
#: reconstruction is wrong, not the juggling.
MAX_EXTENT = 5.0

#: A thrown ball rises and falls by at least this much. Below a 3-throw the
#: apex is still ~0.3 m above the hands, so this is deliberately generous.
BALL_MIN_HEIGHT_SPAN = 0.30

#: A fixed marker moves less than this over its whole lifetime...
STATIC_MAX_HEIGHT_SPAN = 0.06

#: ...and persists for at least this many samples (2 s at 300 Hz).
STATIC_MIN_SAMPLES = 600

#: Shorter than this and there is nothing to measure: 15 samples is 50 ms at
#: 300 Hz, well under the 100 ms minimum flight duration (DESIGN.md §13).
MIN_LIFETIME = 15


@dataclass(frozen=True)
class CleanReport:
    """Tally of the classification, one count per ``kind``."""

    ball: int = 0
    spurious: int = 0
    unknown: int = 0

    def __str__(self) -> str:
        return f"balls={self.ball} spurious={self.spurious} unclassified={self.unknown}"


def classify_trajectory(trajectory: Trajectory) -> Kind:
    """Classify one trajectory as ``ball``, ``spurious`` or ``unknown``."""
    if trajectory.n_samples < MIN_LIFETIME:
        return "spurious"

    bounds = trajectory.bounds()
    if bounds is None:
        return "spurious"
    low, high = bounds
    if not np.all(np.isfinite(low)) or np.any(high - low > MAX_EXTENT):
        return "spurious"

    height_span = trajectory.height_span
    if height_span < STATIC_MAX_HEIGHT_SPAN and trajectory.n_samples >= STATIC_MIN_SAMPLES:
        return "spurious"
    if height_span >= BALL_MIN_HEIGHT_SPAN:
        return "ball"
    return "unknown"


def classify_session(session: Session) -> tuple[Session, CleanReport]:
    """Classify every trajectory, returning a new session and a tally.

    The model is immutable, so this returns a new :class:`Session` rather than
    mutating in place — an analysis is a chain of values, which is what makes it
    diffable between algorithm changes (DESIGN.md §2).
    """
    classified = tuple(t.with_kind(classify_trajectory(t)) for t in session.trajectories)
    counts = {"ball": 0, "spurious": 0, "unknown": 0}
    for trajectory in classified:
        counts[trajectory.kind] += 1
    return session.with_trajectories(classified), CleanReport(**counts)
