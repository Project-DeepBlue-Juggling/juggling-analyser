"""Pure analysis core: no I/O, no wall clock, no unseeded randomness.

See CLAUDE.md rule 1 and DESIGN.md §2. The rule is enforced twice — by
``core/.ruff.toml`` and by ``tests/test_core_purity.py``.
"""

from .clean import CleanReport, classify_session, classify_trajectory, refine_with_flights
from .flight import (
    Carry,
    Flight,
    FlightSegmentation,
    GravityCheck,
    check_gravity,
    event_positions,
    find_flights,
    segment_session,
    segment_trajectory,
)
from .frame import FrameDiagnostics, FrameTransform, derive_frame, to_juggling_frame
from .link import (
    Ball,
    BallSpan,
    BridgedGap,
    CollisionViolation,
    Linking,
    estimate_ball_count,
    link_trajectories,
    score_linking,
)
from .params import BALL_DIAMETER, BALL_MASS, GRAVITY, HAND_COUNT
from .trajectory import Piece, Session, Trajectory, Uncertainty

__all__ = [
    "BALL_DIAMETER",
    "BALL_MASS",
    "GRAVITY",
    "HAND_COUNT",
    "Ball",
    "BallSpan",
    "BridgedGap",
    "Carry",
    "CleanReport",
    "CollisionViolation",
    "Flight",
    "FlightSegmentation",
    "FrameDiagnostics",
    "FrameTransform",
    "GravityCheck",
    "Linking",
    "Piece",
    "Session",
    "Trajectory",
    "Uncertainty",
    "check_gravity",
    "classify_session",
    "classify_trajectory",
    "derive_frame",
    "estimate_ball_count",
    "event_positions",
    "find_flights",
    "link_trajectories",
    "refine_with_flights",
    "score_linking",
    "segment_session",
    "segment_trajectory",
    "to_juggling_frame",
]
