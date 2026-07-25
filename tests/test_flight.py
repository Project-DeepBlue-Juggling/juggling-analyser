"""Flight segmentation, against synthetic truth first and the corpus second.

Synthetic data comes first deliberately: it is the only place where the throw
instant, the catch instant, the apex and `g` are all known exactly, so it can
measure *accuracy* rather than self-consistency. The corpus tests then pin the
measured numbers from the real recordings so a regression shows up as a changed
number rather than a still-passing assertion.
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pytest

from juggling_analyser.core.clean import classify_session, refine_with_flights
from juggling_analyser.core.flight import (
    Flight,
    acceleration_noise_gain,
    ballistic_mask,
    check_gravity,
    derivatives,
    event_positions,
    fit_fixed_gravity,
    fit_free_gravity,
    savgol_window_samples,
    segment_session,
    segment_trajectory,
)
from juggling_analyser.core.params import GRAVITY, MAX_FLIGHT_RESIDUAL
from juggling_analyser.core.trajectory import Piece, Trajectory, Uncertainty
from juggling_analyser.io.qtm import read_qtm

from .conftest import BALLS_ONLY_QTM, THREE_BALL_QTM, sample

F_S = 300.0


# --------------------------------------------------------------------------- #
# synthetic truth
# --------------------------------------------------------------------------- #


def make_pattern(
    *,
    n_throws: int = 4,
    air_time: float = 0.5,
    dwell_time: float = 0.3,
    hand_separation: float = 0.4,
    sigma: float = 5e-4,
    seed: int = 7,
    gravity: float = GRAVITY,
) -> tuple[Trajectory, list[tuple[float, float]]]:
    """One ball alternating carry and flight, plus the exact flight time windows.

    The carry is a constant-acceleration arc that starts and ends at rest in the
    hand, so its acceleration is nowhere near `−g` and the boundary between the
    two regimes is unambiguous — which is exactly what a segmenter must find.
    Returns the trajectory and `[(throw_time, catch_time)]` ground truth.
    """
    rng = np.random.default_rng(seed)
    dt = 1.0 / F_S
    times: list[float] = []
    points: list[np.ndarray] = []
    truth: list[tuple[float, float]] = []

    t = 0.0
    # Start with a carry so the first flight is not truncated.
    for throw in range(n_throws + 1):
        side = -1.0 if throw % 2 == 0 else 1.0
        x0 = side * hand_separation / 2.0
        # carry: rise 0.15 m over dwell_time, ending at the release velocity
        n_carry = round(dwell_time / dt)
        for i in range(n_carry):
            s = i / n_carry
            times.append(t + i * dt)
            points.append(np.array([x0, 0.0, 1.0 + 0.15 * s**2]))
        t += n_carry * dt
        if throw == n_throws:
            break
        # flight: exact parabola from this hand to the other
        n_air = round(air_time / dt)
        v_z = gravity * air_time / 2.0
        v_x = -2.0 * x0 / air_time
        truth.append((t, t + n_air * dt))
        for i in range(n_air + 1):
            tau = i * dt
            times.append(t + tau)
            points.append(
                np.array([x0 + v_x * tau, 0.0, 1.15 + v_z * tau - 0.5 * gravity * tau**2])
            )
        t += n_air * dt

    positions = np.asarray(points)
    positions += rng.normal(0.0, sigma, size=positions.shape)
    n = len(positions)
    frames = np.arange(1, n + 1, dtype=np.int64)
    return (
        Trajectory(
            id="synthetic",
            frames=frames,
            positions=positions,
            uncertainty=Uncertainty.isotropic(np.full(n, sigma)),
            sample_type=np.ones(n, dtype=np.uint8),
            pieces=(Piece(1, n, 1),),
        ),
        truth,
    )


def test_synthetic_flights_are_found_with_the_right_count() -> None:
    trajectory, truth = make_pattern(n_throws=4)
    flights, carries = segment_trajectory(trajectory, F_S)
    assert len(flights) == len(truth) == 4
    # Flights and carries tile the trajectory exactly, with no overlap.
    covered = sorted(
        [(f.start_index, f.end_index) for f in flights]
        + [(c.start_index, c.end_index) for c in carries]
    )
    assert covered[0][0] == 0
    assert covered[-1][1] == trajectory.n_samples - 1
    for before, after in pairwise(covered):
        assert after[0] == before[1] + 1


def test_synthetic_throw_and_catch_times_are_accurate() -> None:
    """The refined boundaries must land within a few samples of the truth."""
    trajectory, truth = make_pattern(n_throws=4)
    flights, _carries = segment_trajectory(trajectory, F_S)
    tolerance = 4.0 / F_S  # 13 ms
    for flight, (throw, catch) in zip(flights, truth, strict=True):
        assert abs(flight.start_time - throw) <= tolerance, (
            f"throw off by {(flight.start_time - throw) * 1000:.1f} ms"
        )
        assert abs(flight.end_time - catch) <= tolerance, (
            f"catch off by {(flight.end_time - catch) * 1000:.1f} ms"
        )
        assert abs(flight.air_time - (catch - throw)) <= 2 * tolerance


def test_synthetic_gravity_is_recovered() -> None:
    """On data that obeys `g`, the free fit must return `g`. Sanity for the check."""
    trajectory, _truth = make_pattern(n_throws=6, air_time=0.6)
    flights, _carries = segment_trajectory(trajectory, F_S)
    check = check_gravity(flights, min_samples=100)
    assert check.within < 0.02, f"synthetic g came back {check.median:.4f}"
    assert abs(check.median - GRAVITY) < 0.05


def test_synthetic_apex_is_sub_sample_accurate() -> None:
    """`z_apex` from the fitted parabola beats the sampled maximum."""
    air_time = 0.5
    trajectory, _truth = make_pattern(n_throws=2, air_time=air_time)
    flights, _carries = segment_trajectory(trajectory, F_S)
    expected = GRAVITY * air_time**2 / 8.0  # identity 3, NOTATION.md
    for flight in flights:
        assert flight.apex_observed
        assert flight.apex_height == pytest.approx(expected, abs=3e-3)
        # The fixed-gravity fit makes z_apex and v_z^2/2g the same quantity;
        # asserting it guards the sign and factor rather than the physics.
        assert flight.apex_height == pytest.approx(flight.apex_height_from_velocity(), abs=1e-9)


def test_synthetic_flights_are_not_suspect_and_residuals_are_noise_sized() -> None:
    trajectory, _truth = make_pattern(n_throws=4, sigma=5e-4)
    flights, _carries = segment_trajectory(trajectory, F_S)
    for flight in flights:
        assert not flight.is_suspect()
        assert flight.free_residual < 2e-3
        assert flight.chi2_per_dof < 20.0


@pytest.mark.parametrize("sigma", [5e-4, 1e-3, 1.5e-3, 2e-3])
@pytest.mark.parametrize("seed", [7, 11, 23])
def test_segmentation_survives_four_times_the_corpus_noise(sigma: float, seed: int) -> None:
    """The measured robustness envelope, asserted so it cannot silently shrink.

    The corpus sits at σ ≈ 0.2–0.5 mm. Every flight is still found at 2 mm — four
    times worse — because the smoothing window adapts to the noise. Beyond that it
    degrades honestly: at 3 mm some flights are lost and at 5 mm all are, which is
    recorded in BUILD_LOG.md rather than hidden by a looser assertion here.
    """
    trajectory, truth = make_pattern(n_throws=4, sigma=sigma, seed=seed)
    flights, _carries = segment_trajectory(trajectory, F_S)
    assert len(flights) == len(truth)
    for flight, (throw, catch) in zip(flights, truth, strict=True):
        assert abs(flight.start_time - throw) <= 4.0 / F_S
        assert abs(flight.end_time - catch) <= 4.0 / F_S


def test_segmentation_gives_up_rather_than_inventing_flights_in_extreme_noise() -> None:
    """At 10× the corpus noise there is no acceleration signal, and none is claimed."""
    trajectory, _truth = make_pattern(n_throws=4, sigma=5e-3, seed=7)
    flights, _carries = segment_trajectory(trajectory, F_S)
    assert flights == ()


def test_a_straight_line_is_never_a_flight() -> None:
    """Constant velocity fits a free quadratic perfectly; it is not free fall."""
    n = 400
    times = np.arange(n) / F_S
    positions = np.stack([times * 0.5, np.zeros(n), 1.0 + times * 0.3], axis=1)
    trajectory = Trajectory(
        id="line",
        frames=np.arange(1, n + 1, dtype=np.int64),
        positions=positions,
        uncertainty=Uncertainty.isotropic(np.full(n, 5e-4)),
        sample_type=np.ones(n, dtype=np.uint8),
        pieces=(Piece(1, n, 1),),
    )
    flights, carries = segment_trajectory(trajectory, F_S)
    assert flights == ()
    assert len(carries) == 1


def test_a_static_marker_is_never_a_flight() -> None:
    n = 900
    trajectory = Trajectory(
        id="static",
        frames=np.arange(1, n + 1, dtype=np.int64),
        positions=np.tile(np.array([0.1, 0.2, -0.66]), (n, 1)),
        uncertainty=Uncertainty.isotropic(np.full(n, 2e-4)),
        sample_type=np.ones(n, dtype=np.uint8),
        pieces=(Piece(1, n, 1),),
    )
    flights, _carries = segment_trajectory(trajectory, F_S)
    assert flights == ()


def test_a_trajectory_shorter_than_the_window_yields_no_flight() -> None:
    """Too short to estimate acceleration: no flight, and it is all carry."""
    n = 10
    trajectory = Trajectory(
        id="tiny",
        frames=np.arange(1, n + 1, dtype=np.int64),
        positions=np.zeros((n, 3)),
        uncertainty=Uncertainty.isotropic(np.full(n, 1e-4)),
        sample_type=np.ones(n, dtype=np.uint8),
        pieces=(Piece(1, n, 1),),
    )
    flights, carries = segment_trajectory(trajectory, F_S)
    assert flights == ()
    assert [(c.start_index, c.end_index) for c in carries] == [(0, n - 1)]


def test_truncated_flights_are_flagged() -> None:
    """A trajectory that starts mid-flight has no throw at its first sample."""
    trajectory, _truth = make_pattern(n_throws=3)
    # The first carry is 90 samples and the first flight 151, so cropping at 150
    # opens the data 60 samples into a flight — enough to detect, with no throw.
    crop = 150
    keep = slice(crop, trajectory.n_samples)
    n = trajectory.n_samples - crop
    cropped = Trajectory(
        id="cropped",
        frames=np.arange(1, n + 1, dtype=np.int64),
        positions=trajectory.positions[keep],
        uncertainty=trajectory.uncertainty.take(keep),
        sample_type=trajectory.sample_type[keep],
        pieces=(Piece(1, n, 1),),
    )
    flights, _carries = segment_trajectory(cropped, F_S)
    assert flights
    assert flights[0].truncated_start
    assert not flights[0].is_complete


def test_internal_gaps_are_not_smoothed_across() -> None:
    """A trajectory with a hole is processed one contiguous span at a time."""
    trajectory, _truth = make_pattern(n_throws=4)
    n = trajectory.n_samples
    keep = np.concatenate([np.arange(0, 400), np.arange(600, n)])
    spliced = Trajectory(
        id="gapped",
        frames=np.concatenate([np.arange(1, 401), np.arange(601, n + 1)]).astype(np.int64),
        positions=trajectory.positions[keep],
        uncertainty=trajectory.uncertainty.take(keep),
        sample_type=trajectory.sample_type[keep],
        pieces=(Piece(1, 400, 1), Piece(601, n, 1)),
    )
    flights, _carries = segment_trajectory(spliced, F_S)
    # No flight may straddle the hole.
    for flight in flights:
        assert not (flight.start_frame <= 400 < flight.end_frame)


# --------------------------------------------------------------------------- #
# the primitives
# --------------------------------------------------------------------------- #


def test_savgol_window_is_odd_and_scales_with_f_s() -> None:
    assert savgol_window_samples(300.0) == 21  # DESIGN.md §13's default
    assert savgol_window_samples(100.0) == 7
    assert savgol_window_samples(500.0) == 35
    for f_s in (60.0, 100.0, 240.0, 300.0, 1000.0):
        assert savgol_window_samples(f_s) % 2 == 1
    with pytest.raises(ValueError, match="f_s must be positive"):
        savgol_window_samples(0.0)
    with pytest.raises(ValueError, match="window_seconds must be positive"):
        savgol_window_samples(300.0, 0.0)


def test_acceleration_noise_gain_matches_a_monte_carlo() -> None:
    """The analytic 2-norm of the filter must equal the observed noise gain."""
    rng = np.random.default_rng(3)
    for window in (11, 21, 31):
        predicted = acceleration_noise_gain(window, F_S)
        noise = rng.normal(0.0, 1.0, size=40000)
        _velocity, acceleration = derivatives(noise[:, None] * np.ones(3), F_S, window)
        assert acceleration[:, 0].std() == pytest.approx(predicted, rel=0.05)
    # Longer windows are quieter, which is the whole reason to smooth.
    assert acceleration_noise_gain(31, F_S) < acceleration_noise_gain(21, F_S)


def test_ballistic_mask_widens_with_uncertainty() -> None:
    acceleration = np.array([[0.0, 0.0, -GRAVITY - 2.0]])
    clean = ballistic_mask(acceleration, np.array([1e-4]), 1000.0)
    noisy = ballistic_mask(acceleration, np.array([2e-3]), 1000.0)
    assert not clean[0], "2 m/s^2 off should fail the design tolerance"
    assert noisy[0], "a sample with 2 mm sigma cannot be judged that tightly"


def test_fixed_gravity_fit_recovers_a_known_parabola() -> None:
    times = np.arange(150) / F_S
    position0 = np.array([0.1, -0.2, 1.0])
    velocity0 = np.array([0.5, 0.05, 2.4])
    truth = (
        position0
        + velocity0 * times[:, None]
        - 0.5 * np.array([0.0, 0.0, GRAVITY]) * times[:, None] ** 2
    )
    fit = fit_fixed_gravity(times, truth, np.full(len(times), 1e-4))
    at_zero, velocity_at_zero = fit.at(0.0)
    assert at_zero == pytest.approx(position0, abs=1e-9)
    assert velocity_at_zero == pytest.approx(velocity0, abs=1e-8)
    assert fit.residual < 1e-12


def test_free_gravity_fit_recovers_a_known_gravity() -> None:
    times = np.arange(200) / F_S
    for truth_g in (9.80665, 9.55, 12.0):
        z = 1.0 + 2.0 * times - 0.5 * truth_g * times**2
        gravity, sigma, residual = fit_free_gravity(z * 0 + times, z, np.full(len(times), 1e-4))
        assert gravity == pytest.approx(truth_g, abs=1e-6)
        assert residual < 1e-12
        assert sigma >= 0.0


def test_fits_reject_too_few_samples() -> None:
    with pytest.raises(ValueError, match="at least 3 samples"):
        fit_fixed_gravity(np.zeros(2), np.zeros((2, 3)), np.ones(2))
    with pytest.raises(ValueError, match="at least 4 samples"):
        fit_free_gravity(np.zeros(3), np.zeros(3), np.ones(3))


def test_check_gravity_refuses_to_pass_vacuously() -> None:
    with pytest.raises(ValueError, match="cannot be checked"):
        check_gravity(())


def test_event_positions_excludes_truncated_and_suspect_ends() -> None:
    trajectory, _truth = make_pattern(n_throws=3)
    flights, _carries = segment_trajectory(trajectory, F_S)
    points = event_positions(flights)
    assert points.shape == (2 * len(flights), 3)
    assert event_positions(()).shape == (0, 3)


def test_suspect_catches_a_smooth_non_ballistic_segment() -> None:
    """A flight-shaped object whose fitted g is wrong is suspect, low residual or not."""
    base = Flight(
        trajectory_id="x",
        start_index=0,
        end_index=99,
        start_frame=1,
        end_frame=100,
        start_time=0.0,
        end_time=0.33,
        start_position=np.zeros(3),
        end_position=np.zeros(3),
        start_velocity=np.zeros(3),
        end_velocity=np.zeros(3),
        apex_time=0.0,
        apex_position=np.zeros(3),
        apex_height=0.0,
        apex_observed=True,
        residual=1e-4,
        chi2_per_dof=1.0,
        fitted_gravity=GRAVITY,
        fitted_gravity_sigma=0.01,
        free_residual=1e-4,
        gravity_used=GRAVITY,
        truncated_start=False,
        truncated_end=False,
    )
    assert not base.is_suspect()
    from dataclasses import replace

    assert replace(base, fitted_gravity=-0.577).is_suspect()
    assert replace(base, free_residual=6e-3).is_suspect()


# --------------------------------------------------------------------------- #
# the corpus
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("name", "expect_flights", "expect_gravity", "expect_axis_degrees"),
    [
        (THREE_BALL_QTM, 62, -0.0259, 0.59),
        (BALLS_ONLY_QTM, 87, -0.0265, 1.56),
    ],
)
def test_corpus_measurements_are_pinned(
    name: str, expect_flights: int, expect_gravity: float, expect_axis_degrees: float
) -> None:
    """Pin what the segmenter actually measures on the real recordings.

    These are *measurements*, not targets: they are asserted so that an algorithm
    change has to acknowledge moving them. The gravity figure fails PLAN.md P2's
    2% criterion and the reason is recorded in BUILD_LOG.md — the two recordings
    agree to 0.15 percentage points, which is an instrument offset, not a bug.
    """
    from juggling_analyser.core.frame import derive_frame

    session, _report = classify_session(read_qtm(sample(name)))
    segmentation = segment_session(session)
    assert len(segmentation.flights) == expect_flights

    check = segmentation.gravity_check
    assert check is not None
    assert check.relative_error == pytest.approx(expect_gravity, abs=5e-4)

    transform = derive_frame(event_positions(segmentation.flights))
    assert np.degrees(transform.angle_to_nominal_hand_axis()) == pytest.approx(
        expect_axis_degrees, abs=0.05
    )


@pytest.mark.parametrize("name", [THREE_BALL_QTM, BALLS_ONLY_QTM])
def test_corpus_flight_residuals_are_below_tolerance(name: str) -> None:
    """PLAN.md P2: every detected flight's parabola residual is within tolerance."""
    session, _report = classify_session(read_qtm(sample(name)))
    segmentation = segment_session(session)
    assert segmentation.flights
    suspect = [f for f in segmentation.flights if f.is_suspect()]
    assert not suspect, f"{len(suspect)} suspect flights: {[f.trajectory_id for f in suspect]}"
    worst = max(f.free_residual for f in segmentation.flights)
    assert worst < MAX_FLIGHT_RESIDUAL, f"worst free-gravity residual {worst * 1000:.2f} mm"


@pytest.mark.parametrize("name", [THREE_BALL_QTM, BALLS_ONLY_QTM])
def test_corpus_hand_axis_is_within_fifteen_degrees(name: str) -> None:
    """PLAN.md P2's acceptance criterion on the derived frame."""
    from juggling_analyser.core.frame import derive_frame

    session, _report = classify_session(read_qtm(sample(name)))
    segmentation = segment_session(session)
    transform = derive_frame(event_positions(segmentation.flights))
    assert np.degrees(transform.angle_to_nominal_hand_axis()) < 15.0


@pytest.mark.parametrize("name", [THREE_BALL_QTM, BALLS_ONLY_QTM])
def test_corpus_gravity_is_consistent_even_though_it_is_low(name: str) -> None:
    """The measured `g` is ~2.6% low. Assert it is *consistent*, and say so.

    PLAN.md P2 asks for `g` within 2% of 9.80665; this corpus does not meet that
    and no amount of algorithm work will fix it (BUILD_LOG.md, Phase 2). What can
    honestly be asserted is that the deficit is a systematic offset rather than
    noise: it must be tight across dozens of independent flights, and the same in
    two separately-recorded clips.
    """
    session, _report = classify_session(read_qtm(sample(name)))
    check = segment_session(session).gravity_check
    assert check is not None
    assert check.n_flights >= 25
    assert -0.035 < check.relative_error < -0.020
    # Tight enough that the offset is not a wide distribution straddling GRAVITY.
    assert check.spread < 0.30


@pytest.mark.parametrize("name", [THREE_BALL_QTM, BALLS_ONLY_QTM])
def test_frame_round_trip_is_identity(name: str) -> None:
    """PLAN.md P2: round-tripping through the frame transform is identity to 1e-12."""
    from juggling_analyser.core.frame import derive_frame, to_juggling_frame

    session, _report = classify_session(read_qtm(sample(name)))
    segmentation = segment_session(session)
    transform = derive_frame(event_positions(segmentation.flights))
    juggling = to_juggling_frame(session, transform)
    assert juggling.frame == "juggling"
    assert juggling.frame_transform is transform
    for original, moved in zip(session.trajectories, juggling.trajectories, strict=True):
        back = transform.invert(moved.positions)
        assert np.abs(back - original.positions).max() < 1e-12


def test_physics_refinement_demotes_trajectories_that_never_fly() -> None:
    """The static rig markers survive geometry but not physics."""
    session, before = classify_session(read_qtm(sample(THREE_BALL_QTM)))
    segmentation = segment_session(session)
    session, after = refine_with_flights(session, segmentation.flights)
    assert after.spurious > before.spurious
    assert after.ball + after.spurious + after.unknown == session.n_trajectories
    for trajectory in session.balls:
        assert any(f.trajectory_id == trajectory.id for f in segmentation.flights)


def test_segmentation_is_deterministic() -> None:
    session, _report = classify_session(read_qtm(sample(BALLS_ONLY_QTM)))
    first = segment_session(session)
    second = segment_session(session)
    assert first.gravity_used == second.gravity_used
    assert [(f.trajectory_id, f.start_frame, f.end_frame) for f in first.flights] == [
        (f.trajectory_id, f.start_frame, f.end_frame) for f in second.flights
    ]
