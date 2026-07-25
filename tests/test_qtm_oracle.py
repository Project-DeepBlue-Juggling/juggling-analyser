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

from .conftest import BALLS_ONLY_QTM, BALLS_ONLY_TSV, sample

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
