"""Pure analysis core: no I/O, no wall clock, no unseeded randomness.

See CLAUDE.md rule 1 and DESIGN.md §2. The rule is enforced twice — by
``core/.ruff.toml`` and by ``tests/test_core_purity.py``.
"""

from .clean import CleanReport, classify_session, classify_trajectory
from .params import BALL_DIAMETER, BALL_MASS, GRAVITY, HAND_COUNT
from .trajectory import Piece, Session, Trajectory, Uncertainty

__all__ = [
    "BALL_DIAMETER",
    "BALL_MASS",
    "GRAVITY",
    "HAND_COUNT",
    "CleanReport",
    "Piece",
    "Session",
    "Trajectory",
    "Uncertainty",
    "classify_session",
    "classify_trajectory",
]
