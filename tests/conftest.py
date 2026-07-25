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

#: A static scene: robots and two floor markers a tape-measured 1000 mm apart, with
#: **no juggling at all**. It is both a physical scale oracle and the negative case
#: for classification — a recording containing no ball must yield no ball.
CALIBRATION_QTM = "2026-06-10-1m_markers_calibration.qtm"

#: Distance between the two floor markers, and the tape's own precision. Ground
#: truth from outside the software entirely, which is what makes it worth testing.
CALIBRATION_KNOWN_DISTANCE = 1.000
CALIBRATION_TAPE_TOLERANCE = 0.005

#: Recordings that actually contain juggling. Tests about balls, flights and catches
#: belong here rather than over every `.qtm` in the corpus.
JUGGLING_QTM_SAMPLES = [p for p in QTM_SAMPLES if "calibration" not in p.name]
