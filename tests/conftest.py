"""Shared fixtures and the sample-data locator.

Tests that need a real recording are *skipped*, never failed, when the corpus is
absent (CLAUDE.md § Conventions), so a fresh clone without `data/` still gates
green. Set ``JA_DATA_DIR`` to point the corpus somewhere else — including at an
empty directory, which is how the "gates green without the corpus" property is
checked without touching `data/`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    override = os.environ.get("JA_DATA_DIR")
    return Path(override) if override else REPO_ROOT / "data"


def sample(name: str) -> Path:
    """Path to a sample recording, skipping the test if it is not present."""
    path = data_dir() / name
    if not path.exists():
        pytest.skip(f"sample recording {name} not present in {data_dir()}")
    return path


def samples(pattern: str) -> list[Path]:
    """All corpus files matching ``pattern`` (possibly empty)."""
    directory = data_dir()
    return sorted(directory.glob(pattern)) if directory.is_dir() else []


QTM_SAMPLES = samples("*.qtm")

#: The pinning oracle for the reader (DESIGN.md §12 layer 1).
BALLS_ONLY_QTM = "5_ball_juggling_cut_balls_only.qtm"
BALLS_ONLY_TSV = "5_ball_juggling_cut_balls_only.tsv"

#: The headline acceptance recording (DESIGN.md §12 layer 3).
THREE_BALL_QTM = "3_ball_juggling_cut.qtm"

#: 3-ball juggling with the robots in shot and two floor markers a tape-measured
#: 1000 mm apart, so one file carries both a scale oracle and ballistic motion.
#:
#: It replaced an earlier static-only version of the same filename mid-session, which
#: is why nothing here asserts an absence of balls: that fixture no longer exists.
CALIBRATION_QTM = "2026-06-10-1m_markers_calibration.qtm"

#: The tape-measured baseline and the tape's own precision. Ground truth from outside
#: the software entirely, which is what makes it worth testing.
#:
#: Note what it does *not* measure: the separation is 99.5% along X, so it constrains
#: the **horizontal** scale only. `g` depends on the vertical scale, and the two
#: disagree in this recording (BUILD_LOG, "anisotropic scale error").
CALIBRATION_KNOWN_DISTANCE = 1.000
CALIBRATION_TAPE_TOLERANCE = 0.005

#: Two markers hung as a plumb line a tape-measured 1000 mm apart, no juggling.
#:
#: The lower marker was "droppy" and arrives as **957 separate pieces**, which makes this
#: file a good stress test of the piece-table reader as well as a vertical scale oracle
#: and the negative case for classification.
VERTICAL_CALIBRATION_QTM = "2026-06-10-1m_markers_vertical_calibration.qtm"

#: Recordings that contain juggling. The vertical-baseline clip does not.
JUGGLING_QTM_SAMPLES = [p for p in QTM_SAMPLES if "vertical" not in p.name]
