"""Unit tests for the data model (DESIGN.md §3).

These need no recording: the model is pure data with invariants, and the
invariants are the point. A malformed :class:`Trajectory` must be impossible to
construct, because every stage downstream trusts ``frames`` to be 1-based and
strictly increasing and ``pieces`` to account for every sample.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from juggling_analyser.core.trajectory import Piece, Session, Trajectory, Uncertainty


def make_trajectory(
    *,
    pieces: tuple[Piece, ...] = (Piece(1, 4, 1),),
    sigma: float = 1e-3,
) -> Trajectory:
    frames = np.concatenate([np.arange(p.start_frame, p.end_frame + 1) for p in pieces]).astype(
        np.int64
    )
    n = len(frames)
    return Trajectory(
        id="1",
        frames=frames,
        positions=np.stack([np.zeros(n), np.zeros(n), np.arange(n, dtype=float)], axis=1),
        uncertainty=Uncertainty.isotropic(np.full(n, sigma)),
        sample_type=np.concatenate([np.full(p.length, p.sample_type) for p in pieces]),
        pieces=pieces,
    )


# --------------------------------------------------------------------------- #
# Uncertainty
# --------------------------------------------------------------------------- #


def test_isotropic_uncertainty_expands_to_a_covariance() -> None:
    u = Uncertainty.isotropic(np.array([2.0, 3.0]))
    assert np.allclose(u.cov(0), np.eye(3) * 4.0)
    assert np.allclose(u.cov(1), np.eye(3) * 9.0)
    assert np.allclose(u.variances(), [[4.0] * 3, [9.0] * 3])
    assert np.allclose(u.sigma(), [2.0, 3.0])
    assert len(u) == 2


def test_diagonal_and_full_agree_when_they_describe_the_same_thing() -> None:
    sigma = np.array([[1.0, 2.0, 3.0]])
    diagonal = Uncertainty.diagonal(sigma)
    full = Uncertainty.full(np.diag(sigma[0] ** 2)[None, :, :])
    assert np.allclose(diagonal.cov(0), full.cov(0))
    assert np.allclose(diagonal.variances(), full.variances())
    assert np.allclose(diagonal.sigma(), full.sigma())


def test_inv_cov_is_the_inverse() -> None:
    covariance = np.array([[[4.0, 1.0, 0.0], [1.0, 9.0, 0.0], [0.0, 0.0, 16.0]]])
    u = Uncertainty.full(covariance)
    assert np.allclose(u.cov(0) @ u.inv_cov(0), np.eye(3), atol=1e-12)


def test_inv_cov_refuses_a_singular_covariance() -> None:
    """A zero-variance axis is a modelling error, not something to paper over."""
    u = Uncertainty.isotropic(np.array([0.0]))
    with pytest.raises(np.linalg.LinAlgError):
        u.inv_cov(0)


def test_scaled_inflates_sigma_not_variance() -> None:
    iso = Uncertainty.isotropic(np.array([2.0])).scaled(3.0)
    assert np.allclose(iso.sigma(), [6.0])
    full = Uncertainty.full(np.eye(3)[None] * 4.0).scaled(3.0)
    assert np.allclose(full.sigma(), [2.0 * 3.0])


def test_take_selects_samples() -> None:
    u = Uncertainty.isotropic(np.arange(5, dtype=float))
    assert np.allclose(u.take(np.array([1, 3])).values, [1.0, 3.0])
    assert np.allclose(u.take(slice(2, 4)).values, [2.0, 3.0])


@pytest.mark.parametrize(
    ("form", "values"),
    [
        ("isotropic", np.zeros((2, 3))),
        ("diagonal", np.zeros(2)),
        ("diagonal", np.zeros((2, 2))),
        ("full", np.zeros((2, 3))),
        ("full", np.zeros((2, 2, 2))),
    ],
)
def test_uncertainty_rejects_wrong_shapes(form: str, values: np.ndarray) -> None:
    with pytest.raises(ValueError, match="uncertainty needs"):
        Uncertainty(form, values)  # type: ignore[arg-type]


def test_uncertainty_rejects_negative_sigma_and_nan() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        Uncertainty.isotropic(np.array([-1.0]))
    with pytest.raises(ValueError, match="non-finite"):
        Uncertainty.isotropic(np.array([np.nan]))


def test_scaled_rejects_a_negative_factor() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        Uncertainty.isotropic(np.array([1.0])).scaled(-1.0)


# --------------------------------------------------------------------------- #
# Piece
# --------------------------------------------------------------------------- #


def test_piece_length_is_inclusive() -> None:
    assert Piece(1681, 1691, 1).length == 11
    assert Piece(7, 7, 2).length == 1


def test_piece_rejects_zero_based_or_empty_ranges() -> None:
    with pytest.raises(ValueError, match="1-based"):
        Piece(0, 5, 1)
    with pytest.raises(ValueError, match="empty piece"):
        Piece(5, 4, 1)


# --------------------------------------------------------------------------- #
# Trajectory
# --------------------------------------------------------------------------- #


def test_trajectory_geometry() -> None:
    trajectory = make_trajectory(pieces=(Piece(10, 13, 1),))
    assert trajectory.n_samples == 4
    assert trajectory.first_frame == 10
    assert trajectory.last_frame == 13
    assert trajectory.frame_span == 4
    assert trajectory.is_contiguous
    assert trajectory.gaps() == ()
    assert trajectory.active_at(10)
    assert trajectory.active_at(13)
    assert not trajectory.active_at(9)
    assert not trajectory.active_at(14)
    assert trajectory.height_span == pytest.approx(3.0)


def test_trajectory_with_an_internal_gap() -> None:
    trajectory = make_trajectory(pieces=(Piece(1, 3, 1), Piece(10, 12, 1)))
    assert trajectory.n_samples == 6
    assert trajectory.frame_span == 12
    assert not trajectory.is_contiguous
    assert trajectory.gaps() == ((4, 9),)
    assert not trajectory.active_at(5)


def test_abutting_pieces_are_contiguous_but_still_gap_filled() -> None:
    """QTM's *Mixed* covers gap-filled runs that leave no missing frame."""
    trajectory = make_trajectory(pieces=(Piece(1, 3, 1), Piece(4, 5, 2), Piece(6, 8, 1)))
    assert trajectory.is_contiguous
    assert trajectory.gaps() == ()
    assert trajectory.has_gap_filled_samples


def test_times_put_frame_one_at_zero() -> None:
    trajectory = make_trajectory(pieces=(Piece(1, 4, 1),))
    assert np.allclose(trajectory.times(300.0), [0.0, 1 / 300, 2 / 300, 3 / 300])


def test_trajectory_rejects_inconsistent_arrays() -> None:
    with pytest.raises(ValueError, match=r"positions must be \(N, 3\)"):
        Trajectory(
            id="1",
            frames=np.array([1, 2]),
            positions=np.zeros((3, 3)),
            uncertainty=Uncertainty.isotropic(np.zeros(2)),
            sample_type=np.ones(2, dtype=np.uint8),
            pieces=(Piece(1, 2, 1),),
        )


def test_trajectory_rejects_pieces_that_do_not_account_for_every_sample() -> None:
    with pytest.raises(ValueError, match="pieces cover"):
        Trajectory(
            id="1",
            frames=np.array([1, 2, 3]),
            positions=np.zeros((3, 3)),
            uncertainty=Uncertainty.isotropic(np.zeros(3)),
            sample_type=np.ones(3, dtype=np.uint8),
            pieces=(Piece(1, 2, 1),),
        )


def test_trajectory_rejects_non_increasing_frames() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        Trajectory(
            id="1",
            frames=np.array([3, 1, 2]),
            positions=np.zeros((3, 3)),
            uncertainty=Uncertainty.isotropic(np.zeros(3)),
            sample_type=np.ones(3, dtype=np.uint8),
            pieces=(Piece(1, 3, 1),),
        )


def test_trajectory_rejects_a_mismatched_uncertainty_length() -> None:
    with pytest.raises(ValueError, match="uncertainty covers"):
        Trajectory(
            id="1",
            frames=np.array([1, 2]),
            positions=np.zeros((2, 3)),
            uncertainty=Uncertainty.isotropic(np.zeros(5)),
            sample_type=np.ones(2, dtype=np.uint8),
            pieces=(Piece(1, 2, 1),),
        )


def test_with_kind_and_with_positions_do_not_mutate() -> None:
    original = make_trajectory()
    relabelled = original.with_kind("ball")
    assert original.kind == "unknown"
    assert relabelled.kind == "ball"
    moved = original.with_positions(original.positions + 1.0)
    assert original.positions[0, 2] == 0.0
    assert moved.positions[0, 2] == 1.0


# --------------------------------------------------------------------------- #
# Session
# --------------------------------------------------------------------------- #


def test_session_summary_and_queries() -> None:
    ball = make_trajectory(pieces=(Piece(1, 5, 1),)).with_kind("ball")
    junk = make_trajectory(pieces=(Piece(20, 22, 1),)).with_kind("spurious")
    session = Session(source="x.qtm", f_s=300.0, frame_count=600, trajectories=(ball, junk))
    assert session.duration == pytest.approx(2.0)
    assert session.n_trajectories == 2
    assert session.balls == (ball,)
    assert session.of_kind("spurious") == (junk,)
    assert session.active_at(1) == (ball,)
    assert session.active_at(21) == (junk,)
    assert "1 ball, 1 spurious" in session.summary()


def test_session_rejects_impossible_metadata() -> None:
    with pytest.raises(ValueError, match="f_s must be positive"):
        Session(source="x", f_s=0.0, frame_count=1)
    with pytest.raises(ValueError, match="frame_count must be non-negative"):
        Session(source="x", f_s=300.0, frame_count=-1)


# --------------------------------------------------------------------------- #
# properties
# --------------------------------------------------------------------------- #


@given(
    st.lists(st.floats(min_value=1e-6, max_value=1.0), min_size=1, max_size=32),
    st.floats(min_value=0.0, max_value=100.0),
)
def test_scaling_isotropic_uncertainty_scales_sigma_linearly(
    sigmas: list[float], factor: float
) -> None:
    u = Uncertainty.isotropic(np.array(sigmas))
    assert np.allclose(u.scaled(factor).sigma(), u.sigma() * factor)


@given(st.integers(min_value=1, max_value=5000), st.integers(min_value=1, max_value=200))
def test_frame_span_never_undercounts_samples(start: int, length: int) -> None:
    trajectory = make_trajectory(pieces=(Piece(start, start + length - 1, 1),))
    assert trajectory.frame_span >= trajectory.n_samples
    assert trajectory.first_frame == start
