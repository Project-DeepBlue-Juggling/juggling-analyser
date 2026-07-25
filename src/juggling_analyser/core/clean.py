"""Classify raw fragments into balls, static references, and ghosts.

The raw export mixes juggled balls with other markers in the scene: static
reference markers on the floor/rig, and occasional "ghost" trajectories with
physically impossible coordinates (reflections, reconstruction errors). Balls are
separated on physics and lifetime alone — no body markers are assumed present.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .trajectory import Fragment, Session

# metres / samples
GHOST_MAX_EXTENT = 5.0  # a real throw never spans >5 m in any axis
BALL_MIN_HEIGHT_SPAN = 0.5  # a thrown ball rises/falls at least ~0.5 m
STATIC_MAX_SPAN = 0.06  # a fixed marker moves <6 cm over its lifetime
STATIC_MIN_SAMPLES = 600  # ...and persists for a long time
MIN_LIFETIME = 15  # fragments shorter than this are noise/ghosts


@dataclass
class CleanReport:
    ball: int = 0
    static: int = 0
    ghost: int = 0
    unknown: int = 0

    def __str__(self) -> str:
        return (
            f"balls={self.ball} static-refs={self.static} "
            f"ghosts={self.ghost} unclassified={self.unknown}"
        )


def classify_fragment(frag: Fragment) -> str:
    """Return ``"ball"``, ``"static"``, ``"ghost"`` or ``"unknown"``."""
    valid = frag.valid
    n = int(valid.sum())
    if n < MIN_LIFETIME:
        return "ghost"

    v = frag.positions[valid]
    extent = v.max(axis=0) - v.min(axis=0)
    if np.any(extent > GHOST_MAX_EXTENT):
        return "ghost"

    height_span = float(extent[2])
    if height_span < STATIC_MAX_SPAN and n >= STATIC_MIN_SAMPLES:
        return "static"
    if height_span >= BALL_MIN_HEIGHT_SPAN:
        return "ball"
    return "unknown"


def classify_session(session: Session) -> CleanReport:
    """Assign ``kind`` to every fragment in place and return a tally."""
    report = CleanReport()
    for frag in session.fragments:
        frag.kind = classify_fragment(frag)
        setattr(report, frag.kind, getattr(report, frag.kind) + 1)
    return report
