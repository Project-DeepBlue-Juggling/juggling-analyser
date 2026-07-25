"""Reader behaviour across every recording in the corpus.

The exact pinning against a QTM export lives in ``test_qtm_oracle.py``; this
module asserts the invariants that must hold for *any* ``.qtm`` file, so a second
or third recording cannot quietly violate the model.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from juggling_analyser import load
from juggling_analyser.core.clean import classify_session
from juggling_analyser.io.qtm import read_qtm, scan_qtm

from .conftest import CALIBRATION_QTM, JUGGLING_QTM_SAMPLES, QTM_SAMPLES, sample

SAMPLES = QTM_SAMPLES
JUGGLING = JUGGLING_QTM_SAMPLES

pytestmark = pytest.mark.skipif(not SAMPLES, reason="no sample .qtm files in the corpus")


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name)
def test_model_invariants_hold(path: Path) -> None:
    session = read_qtm(path)

    assert session.f_s == 300.0
    assert session.frame_count > 0
    assert session.trajectories, "no trajectories decoded"
    assert session.frame == "qtm"

    for trajectory in session.trajectories:
        n = trajectory.n_samples
        assert trajectory.positions.shape == (n, 3)
        assert trajectory.sample_type.shape == (n,)
        assert len(trajectory.uncertainty) == n
        # 1-based frames, inside the recording, strictly increasing.
        assert trajectory.first_frame >= 1
        assert trajectory.last_frame <= session.frame_count
        assert np.all(np.diff(trajectory.frames) > 0)
        # Pieces account for every sample and are in frame order.
        assert sum(p.length for p in trajectory.pieces) == n
        ends = [p.end_frame for p in trajectory.pieces]
        starts = [p.start_frame for p in trajectory.pieces]
        assert starts == sorted(starts)
        assert all(s > e for s, e in zip(starts[1:], ends[:-1], strict=True))


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name)
def test_every_trajectory_object_validates_against_its_series(path: Path) -> None:
    """The reader's integrity checks are not vacuous: every object really matches.

    ``read_qtm`` raises if a Parts table disagrees with its data series, so this
    passing across the whole corpus is the evidence that the object grammar in
    ``docs/qtm-format.md`` is right, not merely plausible.
    """
    scan = scan_qtm(path)
    assert scan.objects, "no trajectory objects found in Data Items"
    # Object ids are unique and ascending — a mis-locked scan would repeat one.
    ids = [o.object_id for o in scan.objects]
    assert ids == sorted(set(ids))
    # Series ids are unique too: one series belongs to at most one trajectory.
    series = [o.series_id for o in scan.objects]
    assert len(set(series)) == len(series)
    for obj in scan.objects:
        assert obj.pieces or obj.point_type == 0, "an object with no parts has no point type"


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name)
def test_uncertainty_is_physically_plausible(path: Path) -> None:
    session = read_qtm(path)
    for trajectory in session.trajectories:
        sigma = trajectory.uncertainty.sigma()
        assert np.all(np.isfinite(sigma))
        assert np.all(sigma >= 1e-4), "sigma must respect the residual floor"
        assert np.all(sigma < 0.05), "a 5 cm marker residual would mean a broken solve"


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name)
def test_positions_are_metres_within_a_capture_volume(path: Path) -> None:
    session = read_qtm(path)
    for trajectory in session.trajectories:
        assert np.all(np.isfinite(trajectory.positions))
        assert np.all(np.abs(trajectory.positions) < 10.0)


@pytest.mark.parametrize("path", JUGGLING, ids=lambda p: p.name)
def test_classification_finds_ball_trajectories(path: Path) -> None:
    """Only over recordings that contain juggling — see the calibration test below."""
    session, report = classify_session(read_qtm(path))
    assert report.ball >= 3, f"expected at least 3 ball trajectories, got {report}"
    assert report.ball + report.spurious + report.unknown == session.n_trajectories
    for trajectory in session.balls:
        assert trajectory.height_span >= 0.30


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name)
def test_reading_twice_gives_identical_arrays(path: Path) -> None:
    """Ingestion is deterministic — the base of the byte-identical guarantee (P7)."""
    first = read_qtm(path)
    second = read_qtm(path)
    assert [t.id for t in first.trajectories] == [t.id for t in second.trajectories]
    for a, b in zip(first.trajectories, second.trajectories, strict=True):
        assert np.array_equal(a.frames, b.frames)
        assert np.array_equal(a.positions, b.positions)
        assert np.array_equal(a.sample_type, b.sample_type)


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name)
def test_include_unexported_is_a_superset(path: Path) -> None:
    default = read_qtm(path)
    everything = read_qtm(path, include_unexported=True)
    assert {t.id for t in default.trajectories} <= {t.id for t in everything.trajectories}
    assert everything.n_trajectories >= default.n_trajectories


def test_load_helper_classifies() -> None:
    session = load(str(JUGGLING[0]))
    assert any(t.kind == "ball" for t in session.trajectories)
    assert all(t.kind != "unknown" or t.n_samples >= 15 for t in session.trajectories)


def test_a_scene_with_no_juggling_yields_no_balls() -> None:
    """The negative case, and it is a real one.

    `2026-06-10-1m_markers_calibration.qtm` is 10 s of robots and floor markers with
    nothing thrown. Every trajectory is motionless, so `core.clean` must classify all
    of them as spurious and `core.flight` must find no flight. A classifier that
    hallucinated a ball here would be free to hallucinate one anywhere.
    """
    from juggling_analyser.core.flight import segment_session

    session, report = classify_session(read_qtm(sample(CALIBRATION_QTM)))
    assert session.n_trajectories == 26
    assert report.ball == 0, f"a static scene produced {report.ball} ball trajectories"
    assert report.unknown == 0
    assert report.spurious == 26
    assert segment_session(session, calibrate=False).flights == ()


def test_reader_rejects_a_file_that_is_not_a_qtm_measurement(tmp_path: Path) -> None:
    not_qtm = tmp_path / "nope.qtm"
    not_qtm.write_bytes(b"not an OLE2 document")
    with pytest.raises(OSError, match="not an OLE2"):
        read_qtm(not_qtm)
