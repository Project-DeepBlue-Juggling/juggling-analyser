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

from .conftest import (
    JUGGLING_QTM_SAMPLES,
    PENDULUM_QTM,
    QTM_SAMPLES,
    VERTICAL_CALIBRATION_QTM,
    sample,
)

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


@pytest.mark.parametrize("name", [VERTICAL_CALIBRATION_QTM, PENDULUM_QTM])
def test_a_scene_with_no_juggling_yields_no_balls(name: str) -> None:
    """The negative case: a recording with nothing thrown must produce no ball.

    Two of them, and the pendulum is the harder one. The vertical-baseline clip is
    entirely static, but the pendulum clip contains a marker swinging through 0.7 m on a
    curved path with real acceleration — the closest thing to a thrown ball that is not
    one. A classifier that hallucinated a ball there would be free to hallucinate one
    anywhere, and `core.flight` must find no free flight in either.
    """
    from juggling_analyser.core.flight import segment_session

    session, report = classify_session(read_qtm(sample(name)))
    assert report.ball == 0, f"{name} produced {report.ball} ball trajectories"
    assert segment_session(session, calibrate=False).flights == ()


def test_a_marker_fragmented_into_hundreds_of_pieces_still_reads() -> None:
    """The droppy marker: 957 pieces in one trajectory, and every invariant holds.

    The most fragmented trajectory in the corpus by two orders of magnitude, so it is the
    real test of the `Parts` table handling: 957 piece lengths summing exactly to the
    decoded sample count.

    It is also the clearest example in the corpus of what QTM's *Mixed* actually means.
    Every one of those 957 pieces **abuts** its neighbour — QTM gap-filled all 610 lost
    samples, so the trajectory has no missing frame at all despite dropping out
    constantly. `is_contiguous` is therefore True while `has_gap_filled_samples` is also
    True, which is exactly why they are separate properties (docs/qtm-format.md).
    """
    session = read_qtm(sample(VERTICAL_CALIBRATION_QTM), include_unexported=True)
    worst = max(session.trajectories, key=lambda t: len(t.pieces))
    assert len(worst.pieces) > 900, f"expected heavy fragmentation, got {len(worst.pieces)}"
    assert sum(p.length for p in worst.pieces) == worst.n_samples
    # Gap-filled, not holed: the pieces abut and no frame is missing.
    assert worst.is_contiguous
    assert worst.gaps() == ()
    assert worst.has_gap_filled_samples
    gap_filled = int((worst.sample_type == 2).sum())
    assert gap_filled > 500, f"expected many gap-filled samples, got {gap_filled}"
    assert np.all(np.diff(worst.frames) == 1)


def test_reader_rejects_a_file_that_is_not_a_qtm_measurement(tmp_path: Path) -> None:
    not_qtm = tmp_path / "nope.qtm"
    not_qtm.write_bytes(b"not an OLE2 document")
    with pytest.raises(OSError, match="not an OLE2"):
        read_qtm(not_qtm)
