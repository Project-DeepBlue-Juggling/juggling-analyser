"""The reader pinned against the QTM TSV export (DESIGN.md §12, layer 1).

``data/5_ball_juggling_cut_balls_only.tsv`` is a one-off QTM export of
``…_balls_only.qtm``. Asserting that the raw reader reproduces it frame for frame
pins ingestion completely: after this, no later bug can be blamed on the reader.

The tolerance is 1e-6 m because the TSV writes millimetres to three decimals, so
a value read back from text can differ from the binary original by up to 0.5 µm
from rounding alone. That floor is *measured* in
:func:`test_positions_match_to_the_tsv_quantisation_floor` rather than assumed.
"""

from __future__ import annotations

import numpy as np
import pytest

from juggling_analyser.core.trajectory import Session
from juggling_analyser.io.qtm import QtmScan, read_qtm, scan_qtm
from juggling_analyser.io.tsv import TsvExport, read_tsv

from .conftest import (
    BALLS_ONLY_QTM,
    BALLS_ONLY_TSV,
    CALIBRATION_KNOWN_DISTANCE,
    CALIBRATION_QTM,
    CALIBRATION_TAPE_TOLERANCE,
    VERTICAL_CALIBRATION_QTM,
    sample,
)

#: Positions must agree to this, per PLAN.md P1.
POSITION_TOLERANCE = 1e-6

#: QTM writes mm to 3 decimals, so text round-tripping alone costs up to 0.5 µm.
TSV_QUANTISATION = 0.5e-6


@pytest.fixture(scope="module")
def oracle() -> TsvExport:
    return read_tsv(sample(BALLS_ONLY_TSV))


@pytest.fixture(scope="module")
def session_pair() -> tuple[Session, QtmScan]:
    path = sample(BALLS_ONLY_QTM)
    return read_qtm(path), scan_qtm(path)


def test_reader_finds_nineteen_trajectories_not_twenty_five(
    session_pair: tuple[Session, QtmScan],
) -> None:
    """The acceptance count: 19 trajectories from 25 decodable data series."""
    session, scan = session_pair
    assert len(scan.decodable_series) == 25, "expected 25 series to decode as XYZR records"
    assert session.n_trajectories == 19
    # The two gates that get from 25 to 19, each doing distinct work.
    assert scan.orphan_series == (232,), "series 232 has no trajectory object"
    assert len(scan.unexported_objects) == 5, "five trajectories are Trajectory Type 2"
    assert {o.trajectory_type for o in scan.unexported_objects} == {2}


def test_capture_metadata(session_pair: tuple[Session, QtmScan]) -> None:
    session, _scan = session_pair
    assert session.f_s == 300.0
    assert session.frame_count == 4967
    assert session.duration == pytest.approx(4967 / 300.0)
    assert session.frame == "qtm"


def test_exactly_five_trajectories_active_at_frame_one(
    session_pair: tuple[Session, QtmScan],
) -> None:
    """Five balls are in play at the start of the clip."""
    session, _scan = session_pair
    assert len(session.active_at(1)) == 5


def test_trajectory_count_and_order_match_the_export(
    session_pair: tuple[Session, QtmScan], oracle: TsvExport
) -> None:
    session, _scan = session_pair
    assert session.n_trajectories == oracle.n_markers
    # QTM exports markers in trajectory order, so sample counts line up 1:1.
    reader_counts = [t.n_samples for t in session.trajectories]
    oracle_counts = [int(oracle.present(i).sum()) for i in range(oracle.n_markers)]
    assert reader_counts == oracle_counts


def test_frames_match_the_export(session_pair: tuple[Session, QtmScan], oracle: TsvExport) -> None:
    """Every trajectory occupies exactly the frames the export says it does."""
    session, _scan = session_pair
    for i, trajectory in enumerate(session.trajectories):
        expected = oracle.frames[oracle.present(i)]
        assert np.array_equal(trajectory.frames, expected), f"marker {i + 1} frame mismatch"


def test_positions_match_to_the_tsv_quantisation_floor(
    session_pair: tuple[Session, QtmScan], oracle: TsvExport
) -> None:
    """All 19 trajectories reproduce the export within 1e-6 m.

    The measured worst case is reported so a regression shows as a number, not
    just a pass/fail: it should sit at the TSV's own 0.5 µm rounding floor.
    """
    session, _scan = session_pair
    worst = 0.0
    for i, trajectory in enumerate(session.trajectories):
        present = oracle.present(i)
        difference = np.abs(oracle.positions[present, i, :] - trajectory.positions)
        worst = max(worst, float(difference.max()))
    assert worst <= POSITION_TOLERANCE, f"worst position difference {worst:.3e} m"
    assert worst <= TSV_QUANTISATION * 1.001, (
        f"worst difference {worst:.3e} m exceeds the TSV's own rounding floor "
        f"{TSV_QUANTISATION:.1e} m, so the disagreement is real, not quantisation"
    )


def test_trajectory_types_match_the_export(
    session_pair: tuple[Session, QtmScan], oracle: TsvExport
) -> None:
    """QTM's *Mixed* is exactly "has an internal gap or a gap-filled sample"."""
    session, _scan = session_pair
    for i, trajectory in enumerate(session.trajectories):
        mixed = not trajectory.is_contiguous or trajectory.has_gap_filled_samples
        expected = oracle.trajectory_types[i]
        assert mixed == (expected == "Mixed"), (
            f"marker {i + 1}: reader says {'Mixed' if mixed else 'Measured'}, "
            f"export says {expected}"
        )


def test_three_piece_trajectory_matches_the_documented_example(
    session_pair: tuple[Session, QtmScan],
) -> None:
    """The worked example in docs/qtm-format.md: [1681-1691], [1692-1693], [1694-4967]."""
    session, _scan = session_pair
    by_id = {t.id: t for t in session.trajectories}
    trajectory = by_id["448"]
    assert [(p.start_frame, p.end_frame, p.sample_type) for p in trajectory.pieces] == [
        (1681, 1691, 1),
        (1692, 1693, 2),
        (1694, 4967, 1),
    ]
    assert trajectory.n_samples == 3287
    # The three pieces abut, so there is no missing frame — the split exists only
    # because the middle piece was gap-filled. QTM still calls that *Mixed*.
    assert trajectory.gaps() == ()
    assert trajectory.is_contiguous
    assert trajectory.has_gap_filled_samples


def test_sample_times_follow_the_frame_convention(
    session_pair: tuple[Session, QtmScan], oracle: TsvExport
) -> None:
    """``t = (k - 1) / f_s``, so frame 1 is t = 0 (NOTATION.md § Conventions)."""
    session, _scan = session_pair
    trajectory = session.trajectories[2]  # a full-length one
    times = trajectory.times(session.f_s)
    assert times[0] == 0.0
    expected = oracle.times[oracle.present(2)]
    assert np.allclose(times, expected, atol=1e-5)


def test_include_unexported_recovers_the_static_markers() -> None:
    """``include_unexported`` returns all 24 trajectories, and they are the statics."""
    path = sample(BALLS_ONLY_QTM)
    session = read_qtm(path, include_unexported=True)
    assert session.n_trajectories == 24
    extra = [t for t in session.trajectories if t.id in {"432", "433", "434", "435", "440"}]
    assert len(extra) == 5
    for trajectory in extra:
        # Static markers on the rig: motionless, and below everything else.
        assert trajectory.height_span < 0.01
        assert trajectory.positions[:, 2].mean() < -0.5


def test_reader_reproduces_a_tape_measured_horizontal_metre() -> None:
    """A second oracle, physical rather than another file — but only for X and Y.

    The TSV test above proves the reader agrees with QTM. It cannot prove either of
    them is *metrically right*, only that they agree. This closes that gap with ground
    truth from outside the software: two markers laid on the floor a tape-measured
    1000 mm apart.

    **What it does not constrain is the vertical scale**, and that distinction is the
    whole finding. The separation vector is 99.5% along X with 10 mm of vertical
    component, while `g` depends on nothing but the vertical scale — and in this very
    recording the fitted `g` implies the vertical axis is compressed by 5.1%. The name
    of this test says "horizontal" for that reason (BUILD_LOG, "anisotropic scale
    error"). An isotropic −2.87% error is excluded; a vertical-only one is not.
    """
    session = read_qtm(sample(CALIBRATION_QTM), include_unexported=True)
    means = {t.id: t.positions.mean(axis=0) for t in session.trajectories}
    floor = {i: p for i, p in means.items() if p[2] < 0.0}
    # The two floor markers are the only pair on the ground about a metre apart.
    pairs = [
        (a, b, float(np.linalg.norm(means[a] - means[b]))) for a in floor for b in floor if a < b
    ]
    metre = [p for p in pairs if abs(p[2] - CALIBRATION_KNOWN_DISTANCE) < 0.05]
    assert len(metre) == 1, f"expected one ~1 m floor pair, found {len(metre)}"
    first, second, distance = metre[0]

    assert distance == pytest.approx(CALIBRATION_KNOWN_DISTANCE, abs=CALIBRATION_TAPE_TOLERANCE)
    assert distance == pytest.approx(1.00056, abs=1e-4), "the pinned measured value"

    # The point of the test: this baseline is horizontal, so it cannot speak for Z.
    offset = means[second] - means[first]
    assert abs(offset[2]) < 0.02, "baseline is horizontal"
    assert abs(offset[0]) / distance > 0.99, "and essentially all of it is along X"

    # Stable to microns across the whole recording: this is a measurement, not a fluke.
    a = next(t for t in session.trajectories if t.id == first)
    b = next(t for t in session.trajectories if t.id == second)
    shared = np.intersect1d(a.frames, b.frames)
    per_frame = np.linalg.norm(
        a.positions[np.isin(a.frames, shared)] - b.positions[np.isin(b.frames, shared)], axis=1
    )
    assert per_frame.std() < 1e-4
    assert len(per_frame) == 3000

    # And the hypothesis it excludes, stated so the exclusion cannot be lost.
    assert abs(distance - CALIBRATION_KNOWN_DISTANCE * (1 - 0.0287)) > 0.02


def test_reader_reproduces_a_tape_measured_vertical_metre() -> None:
    """The vertical companion to the horizontal oracle above — and it refuted a hypothesis.

    Two markers hung as a plumb line a tape-measured 1000 mm apart. Because `g` depends
    on nothing but the vertical scale, and because the horizontal oracle cannot speak for
    Z, this is the measurement that decides whether the corpus's gravity deficit is a
    vertical scale error. It is not: the baseline reads 996.72 mm, where a vertical scale
    error large enough to explain `g = 9.2757` would have put it at 948.9 mm.

    Both assertions matter. The first says the vertical scale is right; the second says
    the refutation cannot be quietly lost (BUILD_LOG, "Vertical scale is CORRECT too").
    """
    session = read_qtm(sample(VERTICAL_CALIBRATION_QTM), include_unexported=True)
    means = {t.id: t.positions.mean(axis=0) for t in session.trajectories}
    plumb = [
        (a, b)
        for a in means
        for b in means
        if a < b
        and abs(np.linalg.norm(means[a] - means[b]) - CALIBRATION_KNOWN_DISTANCE) < 0.06
        and abs(means[a][2] - means[b][2]) / np.linalg.norm(means[a] - means[b]) > 0.99
    ]
    assert len(plumb) == 1, f"expected one plumb-line pair, found {len(plumb)}"
    first, second = plumb[0]

    a = next(t for t in session.trajectories if t.id == first)
    b = next(t for t in session.trajectories if t.id == second)
    shared = np.intersect1d(a.frames, b.frames)
    per_frame = np.linalg.norm(
        a.positions[np.isin(a.frames, shared)] - b.positions[np.isin(b.frames, shared)], axis=1
    )
    distance = float(per_frame.mean())

    assert distance == pytest.approx(CALIBRATION_KNOWN_DISTANCE, abs=CALIBRATION_TAPE_TOLERANCE)
    assert distance == pytest.approx(0.99672, abs=1e-4), "the pinned measured value"
    assert per_frame.std() < 1e-4
    # The vertical-scale hypothesis is excluded, and by a wide margin.
    assert distance > 0.98, "a vertical scale error explaining g = 9.2757 needs 948.9 mm"
