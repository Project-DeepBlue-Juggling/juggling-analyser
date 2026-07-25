"""Smoke tests for the QTM reader against the sample recordings in data/."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from juggling_analyser import load, read_qtm
from juggling_analyser.core.clean import classify_session

from .conftest import QTM_SAMPLES

SAMPLES = QTM_SAMPLES

pytestmark = pytest.mark.skipif(not SAMPLES, reason="no sample .qtm files in the corpus")


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name)
def test_reads_and_classifies(path: Path) -> None:
    session = read_qtm(str(path))

    # capture metadata decoded
    assert session.frame_rate == 300.0
    assert session.frame_count and session.frame_count > 0
    assert session.fragments, "no fragments decoded"

    # every fragment is a well-formed (N, 3) trajectory with matching arrays
    for frag in session.fragments:
        assert frag.positions.ndim == 2 and frag.positions.shape[1] == 3
        assert frag.residual.shape[0] == frag.n_samples
        assert frag.flags.shape[0] == frag.n_samples

    report = classify_session(session)
    assert report.ball >= 3, "expected at least a few ball fragments"

    # decoded ball positions are physically plausible (metres, within a room)
    for frag in session.balls:
        v = frag.positions[frag.valid]
        assert np.all(np.abs(v) < 10.0)
        assert frag.mean_residual < 0.01  # sub-cm residuals


def test_load_helper_classifies() -> None:
    session = load(str(SAMPLES[0]))
    assert any(f.kind == "ball" for f in session.fragments)
