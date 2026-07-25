"""juggling-analyser — analyse ball-juggling from motion-capture recordings."""

from .core import (
    GRAVITY,
    CleanReport,
    Fragment,
    Session,
    classify_fragment,
    classify_session,
)
from .io import read_qtm

__version__ = "0.1.0.dev0"

__all__ = [
    "GRAVITY",
    "CleanReport",
    "Fragment",
    "Session",
    "__version__",
    "classify_fragment",
    "classify_session",
    "read_qtm",
]


def load(path: str) -> Session:
    """Read a recording and classify its fragments in one call."""
    session = read_qtm(path)
    classify_session(session)
    return session
