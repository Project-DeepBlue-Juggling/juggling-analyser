"""Ballistic segmentation and event detection (DESIGN.md §6).

A ball is in **flight** when its acceleration is `(0, 0, −g)` within tolerance.
This is the single most reliable signal in the data and everything downstream is
built on it — it needs neither ball identity nor a coordinate frame, only that Z
is up, which is true in both the QTM and the juggling frame. That is why this
stage runs before linking (§7) and before the frame is derived (§5).

The pipeline per trajectory is:

1. **Smooth derivatives.** Velocity and acceleration come from a Savitzky–Golay
   filter, never a raw finite difference: differentiating 300 Hz position data
   twice amplifies noise by ~`f_s²`. Polynomial order 2 is exact for ballistic
   motion — the second derivative of a local quadratic fit *is* the acceleration.
2. **Ballistic test**, with the tolerance widened for noisy samples by the
   *propagated* acceleration uncertainty, so a poorly-solved trajectory is not
   fragmented by its own noise.
3. **Segment**, closing sub-carry-length gaps in the mask.
4. **Refine the boundaries against a fitted parabola.** Smoothing blurs the ends
   of a flight by half a window (35 ms at the default settings), which is far too
   coarse for an air time. The parabola needs no smoothing, so extending the
   interval outward while the position residual stays small recovers the release
   and catch instants to single-sample precision.
5. **Fit twice.** Once with gravity *fixed* — that fit defines the events and its
   residual is their confidence — and once with gravity *free*, which is a
   diagnostic and never feeds a result. The free fit exists because
   `g` is the strongest independent check available on the whole pipeline
   (NOTATION.md § Shared identities), and a check that used a fitted value would
   not be a check.

Everything here is a pure function of its arguments (CLAUDE.md rule 1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import binary_closing
from scipy.signal import savgol_coeffs, savgol_filter

from .params import (
    BALLISTIC_CLOSE_SECONDS,
    BALLISTIC_SIGMA_MULTIPLE,
    BALLISTIC_TOLERANCE,
    BOUNDARY_TOLERANCE,
    GRAVITY,
    MAX_FLIGHT_RESIDUAL,
    MAX_GRAVITY_DEVIATION,
    MIN_FLIGHT_DURATION,
    SAVGOL_POLYORDER,
    SAVGOL_WINDOW_SECONDS,
)
from .trajectory import Session, Trajectory

_AXES = 3

#: Free parameters of the fixed-gravity fit: (x0, vx), (y0, vy), (z0, vz).
_FIXED_G_PARAMETERS = 6


@dataclass(frozen=True, eq=False)
class Flight:
    """One free-flight arc, with the events at its ends.

    Indices are 0-based positions in the trajectory's arrays; frames are absolute
    1-based (NOTATION.md § Conventions). Times are seconds with frame 1 at t = 0.

    `truncated_start` / `truncated_end` matter: a flight that runs into the end of
    the trajectory's data was cut off by lost tracking, so its endpoint is **not**
    a throw or a catch. Downstream code must not emit an event for one, which is
    why the flag travels with the flight rather than being re-derived.
    """

    trajectory_id: str
    start_index: int
    end_index: int
    start_frame: int
    end_frame: int
    start_time: float
    end_time: float
    #: Positions and velocities from the fixed-gravity fit, evaluated at the ends.
    start_position: np.ndarray
    end_position: np.ndarray
    start_velocity: np.ndarray
    end_velocity: np.ndarray
    #: Apex of the fitted parabola: the zero-crossing of `v_z`, sub-sample.
    apex_time: float
    apex_position: np.ndarray
    #: `z_apex` — apex height **above the release point** (DESIGN.md §9).
    apex_height: float
    #: True when the apex lies inside the observed span; False for a fragment
    #: that caught only one side of the arc, whose apex is an extrapolation.
    apex_observed: bool
    #: Weighted RMS position residual of the fixed-gravity fit, m — the fit the
    #: events come from. Grows as the square of the flight's duration whenever the
    #: recording's real vertical acceleration differs from the `g` used, so judge
    #: *segment quality* by `free_residual` instead.
    residual: float
    #: Reduced chi-squared of the same fit, in units of the samples' own σ.
    chi2_per_dof: float
    #: Gravity from the *free* fit, m/s², its 1σ, and that fit's RMS residual (m).
    #: Diagnostic only; `free_residual` is the event confidence.
    fitted_gravity: float
    fitted_gravity_sigma: float
    free_residual: float
    #: The gravity the fixed fit used, m/s² — the reference `fitted_gravity` is
    #: judged against, which is the session's calibrated value, not `GRAVITY`.
    gravity_used: float
    truncated_start: bool
    truncated_end: bool

    @property
    def n_samples(self) -> int:
        return self.end_index - self.start_index + 1

    @property
    def air_time(self) -> float:
        """`t_air` for this arc, s. Meaningless unless both ends are untruncated."""
        return self.end_time - self.start_time

    @property
    def is_complete(self) -> bool:
        """True when both ends are real events rather than lost tracking."""
        return not self.truncated_start and not self.truncated_end

    def is_suspect(
        self,
        tolerance: float = MAX_FLIGHT_RESIDUAL,
        gravity_deviation: float = MAX_GRAVITY_DEVIATION,
    ) -> bool:
        """True when this segment does not look like one ball in free flight.

        Two independent tests, because either alone can be fooled:

        * the **free-gravity residual** — measurement-limited, so the threshold
          means the same thing whatever the flight's duration or the recording's
          scale calibration;
        * the **free-fit gravity itself** — a low residual only proves the path is
          smooth. A near-straight segment fits a free quadratic beautifully with
          `g` near zero, and one such segment exists in `3_ball_juggling_cut`.

        DESIGN.md §6: a poor fit marks a segment suspect rather than being
        silently accepted.
        """
        if self.free_residual > tolerance:
            return True
        return abs(self.fitted_gravity - self.gravity_used) > gravity_deviation * self.gravity_used

    def apex_height_from_velocity(self) -> float:
        """`v_z²/2g` at release — the independent cross-check on `apex_height`."""
        return float(self.start_velocity[2] ** 2 / (2.0 * GRAVITY))


@dataclass(frozen=True, eq=False)
class Carry:
    """A non-ballistic span inside one trajectory.

    DESIGN.md §6 defines a carry as "everything else". Note that a *true* carry —
    catch to throw of the same ball — needs ball identity (§7), so this is the
    per-trajectory approximation: correct whenever the trajectory really is one
    ball, and superseded by `core.events` once lanes exist.
    """

    trajectory_id: str
    start_index: int
    end_index: int
    start_frame: int
    end_frame: int

    @property
    def n_samples(self) -> int:
        return self.end_index - self.start_index + 1


@dataclass(frozen=True)
class GravityCheck:
    """The session-level `g` self-check (ORCHESTRATOR §8, NOTATION § Identities).

    `weighted` combines the per-flight free-gravity fits by inverse variance;
    `median` is the robust alternative and is the figure to quote, because the
    per-flight formal σ is far smaller than the flight-to-flight spread.
    """

    n_flights: int
    median: float
    weighted_mean: float
    weighted_sigma: float
    spread: float
    #: Fractional deviation of `median` from `GRAVITY`, e.g. −0.0287 for −2.87%.
    relative_error: float

    @property
    def within(self) -> float:
        """Absolute fractional deviation — compare against an acceptance bound."""
        return abs(self.relative_error)


# --------------------------------------------------------------------------- #
# derivatives
# --------------------------------------------------------------------------- #


def savgol_window_samples(f_s: float, window_seconds: float = SAVGOL_WINDOW_SECONDS) -> int:
    """Odd Savitzky–Golay window length for a sample rate, ≥ `polyorder + 1`.

    0.070 s at 300 Hz gives 21, DESIGN.md §13's default; expressing it as a
    duration means a 100 Hz or 500 Hz recording gets an equivalent filter rather
    than an equivalent number of samples.
    """
    if f_s <= 0.0:
        raise ValueError(f"f_s must be positive, got {f_s}")
    if window_seconds <= 0.0:
        raise ValueError(f"window_seconds must be positive, got {window_seconds}")
    length = round(window_seconds * f_s)
    if length % 2 == 0:
        length += 1
    # The window must exceed the polynomial order, and stay odd.
    return max(length, SAVGOL_POLYORDER + 1 + (SAVGOL_POLYORDER + 1) % 2)


def acceleration_noise_gain(window: int, f_s: float, polyorder: int = SAVGOL_POLYORDER) -> float:
    """Factor by which the second-derivative filter multiplies white position noise.

    Returns `σ_a / σ_p` in `s⁻²`. The Savitzky–Golay derivative is a linear
    filter, so for independent per-sample noise the output σ is the 2-norm of its
    coefficients. Computing it instead of hard-coding it keeps the ballistic
    tolerance honest when the window or the sample rate changes: at 300 Hz the
    gain is ~1.2e3 for a 21-sample window and ~4.5e2 for 31, so the same 0.5 mm
    of position noise becomes 0.6 or 0.2 m/s² of acceleration noise.
    """
    coefficients = savgol_coeffs(window, polyorder, deriv=2, delta=1.0 / f_s)
    return float(np.linalg.norm(coefficients))


def derivatives(
    positions: np.ndarray,
    f_s: float,
    window: int,
    polyorder: int = SAVGOL_POLYORDER,
) -> tuple[np.ndarray, np.ndarray]:
    """Smoothed velocity (m/s) and acceleration (m/s²) of an `(N, 3)` path.

    The path must be uniformly sampled at `f_s` — callers split a trajectory at
    its internal gaps first (see `_contiguous_spans`).
    """
    if positions.shape[0] < window:
        raise ValueError(f"need at least {window} samples for a {window}-sample window")
    delta = 1.0 / f_s
    velocity = savgol_filter(
        positions, window, polyorder, deriv=1, delta=delta, axis=0, mode="interp"
    )
    acceleration = savgol_filter(
        positions, window, polyorder, deriv=2, delta=delta, axis=0, mode="interp"
    )
    return np.asarray(velocity), np.asarray(acceleration)


def ballistic_mask(
    acceleration: np.ndarray,
    sigma_position: np.ndarray,
    noise_gain: float,
    *,
    gravity: float = GRAVITY,
    a_tol: float = BALLISTIC_TOLERANCE,
    sigma_multiple: float = BALLISTIC_SIGMA_MULTIPLE,
) -> np.ndarray:
    """Boolean mask of samples whose acceleration is free-fall within tolerance.

    The test is `‖a − (0, 0, −g)‖ < max(a_tol, sigma_multiple · σ_a)` where
    `σ_a = σ_p · noise_gain` is the acceleration noise propagated from that
    sample's own position uncertainty. The `max` is what "weighted by
    uncertainty" (DESIGN.md §6) buys: a clean trajectory is judged against the
    design tolerance, while a noisy one is not fragmented by noise it cannot help.
    """
    target = np.array([0.0, 0.0, -gravity])
    error = np.linalg.norm(acceleration - target, axis=1)
    threshold = np.maximum(a_tol, sigma_multiple * sigma_position * noise_gain)
    return np.asarray(error < threshold)


# --------------------------------------------------------------------------- #
# parabola fits
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, eq=False)
class _FixedGravityFit:
    """Weighted fit of `x,y` linear and `z` quadratic with gravity fixed."""

    #: Time the fit is parameterised about, s.
    origin_time: float
    #: `(3,)` position at `origin_time`.
    position: np.ndarray
    #: `(3,)` velocity at `origin_time`.
    velocity: np.ndarray
    residual: float
    chi2_per_dof: float
    gravity: float

    def at(self, time: float) -> tuple[np.ndarray, np.ndarray]:
        """Position and velocity at an absolute time, from the fit."""
        dt = time - self.origin_time
        acceleration = np.array([0.0, 0.0, -self.gravity])
        return (
            self.position + self.velocity * dt + 0.5 * acceleration * dt**2,
            self.velocity + acceleration * dt,
        )

    def positions_at(self, times: np.ndarray) -> np.ndarray:
        dt = (times - self.origin_time)[:, None]
        acceleration = np.array([0.0, 0.0, -self.gravity])
        return self.position + self.velocity * dt + 0.5 * acceleration * dt**2


def fit_fixed_gravity(
    times: np.ndarray,
    positions: np.ndarray,
    sigma: np.ndarray,
    *,
    gravity: float = GRAVITY,
) -> _FixedGravityFit:
    """Weighted least-squares ballistic fit with `g` fixed (DESIGN.md §6).

    Fixing gravity is the right choice for extracting events: `g` is a measured
    constant, not a free parameter, and letting it float would trade a real
    physical constraint for a slightly smaller residual. The returned residual is
    the weighted RMS in metres and becomes the events' confidence.
    """
    if times.shape[0] < 3:
        raise ValueError(f"need at least 3 samples to fit a parabola, got {times.shape[0]}")
    origin_time = float(times.mean())
    dt = times - origin_time
    weight = 1.0 / sigma
    # Remove the known quadratic from z, then every axis is linear in (p0, v).
    target = positions.copy()
    target[:, 2] += 0.5 * gravity * dt**2

    design = np.stack([np.ones_like(dt), dt], axis=1) * weight[:, None]
    position = np.empty(_AXES)
    velocity = np.empty(_AXES)
    weighted_residuals = np.empty_like(positions)
    for axis in range(_AXES):
        solution, *_ = np.linalg.lstsq(design, target[:, axis] * weight, rcond=None)
        position[axis], velocity[axis] = solution
        weighted_residuals[:, axis] = design @ solution - target[:, axis] * weight

    chi2 = float((weighted_residuals**2).sum())
    dof = max(positions.size - _FIXED_G_PARAMETERS, 1)
    residual = float(np.sqrt(np.mean((weighted_residuals / weight[:, None]) ** 2)))
    return _FixedGravityFit(
        origin_time=origin_time,
        position=position,
        velocity=velocity,
        residual=residual,
        chi2_per_dof=chi2 / dof,
        gravity=gravity,
    )


def fit_free_gravity(
    times: np.ndarray, z: np.ndarray, sigma: np.ndarray
) -> tuple[float, float, float]:
    """Weighted quadratic fit of height; returns `(g, σ_g, rms_residual)`.

    `g` and `σ_g` are m/s², the residual is metres. Diagnostic only — never used
    to compute a result. This is the independent identity check that ORCHESTRATOR
    §8 asks for, and the number that revealed the corpus's systematic −2.9%
    acceleration deficit (BUILD_LOG, Phase 2).

    The residual matters separately from the fixed-`g` one. Forcing a `g` that the
    data does not obey leaves a systematic error of `½·Δg·(t − t̄)²`, so the
    fixed-`g` residual grows as the *square* of the flight's duration and says
    more about the instrument than about the segment. Letting `g` float absorbs
    exactly that term, which makes this the residual to judge segment quality by.
    """
    if times.shape[0] < 4:
        raise ValueError(f"need at least 4 samples for a free-gravity fit, got {times.shape[0]}")
    dt = times - times.mean()
    weight = 1.0 / sigma
    design = np.stack([dt**2, dt, np.ones_like(dt)], axis=1) * weight[:, None]
    observed = z * weight
    solution, *_ = np.linalg.lstsq(design, observed, rcond=None)
    weighted_residual = design @ solution - observed
    dof = max(len(z) - 3, 1)
    scale = float(weighted_residual @ weighted_residual) / dof
    covariance = scale * np.linalg.inv(design.T @ design)
    rms = float(np.sqrt(np.mean((weighted_residual / weight) ** 2)))
    return -2.0 * float(solution[0]), 2.0 * float(np.sqrt(covariance[0, 0])), rms


# --------------------------------------------------------------------------- #
# segmentation
# --------------------------------------------------------------------------- #


def _adapted_window(
    sigma: np.ndarray,
    f_s: float,
    window_seconds: float,
    a_tol: float,
    min_flight_samples: int,
) -> int:
    """Choose the smoothing window from the trajectory's own noise.

    The window exists to control the noise that double differentiation amplifies,
    so it should be chosen from that noise rather than fixed. The default
    (DESIGN.md §13's 0.070 s, 21 samples at 300 Hz) is the *floor*: it is widened
    only when this trajectory's median σ would otherwise put the propagated
    acceleration noise above `a_tol / 2`, at which point the design tolerance
    would be rejecting genuine flight samples rather than carries.

    Widening is capped at `min_flight_samples`, because a window longer than the
    shortest flight that must be resolvable would leave no sample inside such a
    flight whose window does not straddle its ends.

    On the corpus (median σ ≈ 0.2–0.5 mm) this returns the default 21 unchanged;
    it only engages on trajectories several times noisier, where the alternative
    is to find no flights at all.
    """
    floor = savgol_window_samples(f_s, window_seconds)
    cap = min_flight_samples if min_flight_samples % 2 == 1 else min_flight_samples - 1
    median_sigma = float(np.median(sigma))
    window = floor
    while window + 2 <= cap and median_sigma * acceleration_noise_gain(window, f_s) > a_tol / 2.0:
        window += 2
    return window


def _contiguous_spans(frames: np.ndarray) -> list[tuple[int, int]]:
    """Inclusive `(start_index, end_index)` runs of consecutive frame numbers.

    Savitzky–Golay assumes uniform sampling, so a trajectory with an internal gap
    is processed one span at a time rather than across the hole.
    """
    if frames.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(frames) != 1)
    starts = np.concatenate([[0], breaks + 1])
    ends = np.concatenate([breaks, [frames.size - 1]])
    return [(int(a), int(b)) for a, b in zip(starts, ends, strict=True)]


def _mask_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Inclusive `(start, end)` index runs of True."""
    if not mask.any():
        return []
    padded = np.concatenate([[False], mask, [False]])
    edges = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1) - 1
    return [(int(a), int(b)) for a, b in zip(starts, ends, strict=True)]


#: Fewest samples a seed interval may have and still be fitted.
_MIN_SEED_SAMPLES = 6


def _seed_interval(run: tuple[int, int], erode: int) -> tuple[int, int]:
    """Shrink a detected run to the part whose acceleration estimate is trustworthy.

    The Savitzky–Golay window straddles the flight boundary for the outermost
    `window // 2` samples, so their acceleration is a blend of carry and flight
    and their membership is not evidence either way. Eroding by that much leaves
    an interior that is certainly inside the flight, which is what a fit needs;
    `_refine_boundaries` then grows back out against the parabola itself.

    A run too short to erode keeps its central 60% instead, so a brief flight is
    still seeded from its most reliable part rather than skipped.
    """
    start, end = run
    length = end - start + 1
    if length - 2 * erode >= _MIN_SEED_SAMPLES:
        return start + erode, end - erode
    margin = int(length * 0.2)
    if length - 2 * margin >= 4:
        return start + margin, end - margin
    return start, end


def _refine_boundaries(
    times: np.ndarray,
    positions: np.ndarray,
    sigma: np.ndarray,
    seed: tuple[int, int],
    span: tuple[int, int],
    *,
    gravity: float,
    tolerance: float,
    close_samples: int,
    max_iterations: int = 40,
) -> tuple[int, int]:
    """Fit the seed, then take the maximal run of samples that fit, and repeat.

    The smoothed acceleration cannot locate a release instant better than half a
    filter window; the parabola can. Just outside a flight the hand accelerates
    the ball at tens of m/s², so the deviation from the fitted parabola leaves the
    noise floor within two or three samples at 300 Hz and then grows quadratically.

    Each iteration re-selects the *whole* consistent run rather than creeping
    outward one sample at a time, so the interval can shrink as well as grow and a
    single noisy sample cannot halt it. Small gaps in the consistency mask are
    closed for the same reason they are closed in the ballistic mask. Iterating
    matters: a parabola fitted to a short seed extrapolates poorly, so the first
    pass under-reaches and each refit reaches further, until it stops changing.

    Returns the refined inclusive `(start, end)` indices.
    """
    span_start, span_end = span
    start, end = seed
    anchor = (start + end) // 2
    limit = np.maximum(tolerance, 3.0 * sigma)
    structure = np.ones(max(1, close_samples), dtype=bool)
    window = slice(span_start, span_end + 1)

    for _ in range(max_iterations):
        fit = fit_fixed_gravity(
            times[start : end + 1],
            positions[start : end + 1],
            sigma[start : end + 1],
            gravity=gravity,
        )
        deviation = np.linalg.norm(positions[window] - fit.positions_at(times[window]), axis=1)
        consistent = deviation <= limit[window]
        closed = consistent
        if structure.size > 1:
            closed = np.asarray(binary_closing(consistent, structure=structure, border_value=0))

        run = _run_containing(closed, anchor - span_start)
        if run is None:
            break
        # Closing tolerates an isolated outlier *inside* a flight, but it must not
        # extend the flight over contaminated samples at its ends — those cost
        # air-time accuracy directly. So the run comes from the closed mask and
        # its endpoints from the raw one.
        run = _shrink_to_consistent(run, consistent)
        if run is None:
            break
        new_start, new_end = span_start + run[0], span_start + run[1]
        if new_end - new_start + 1 < _MIN_SEED_SAMPLES:
            break
        if (new_start, new_end) == (start, end):
            break
        start, end = new_start, new_end
    return start, end


def _shrink_to_consistent(run: tuple[int, int], consistent: np.ndarray) -> tuple[int, int] | None:
    """Pull both ends of `run` inward to the first sample that really fits."""
    start, end = run
    while start <= end and not consistent[start]:
        start += 1
    while end >= start and not consistent[end]:
        end -= 1
    return (start, end) if end >= start else None


def _run_containing(mask: np.ndarray, index: int) -> tuple[int, int] | None:
    """The inclusive True-run of `mask` containing `index`, or None."""
    if not (0 <= index < mask.size) or not mask[index]:
        return None
    start = index
    while start > 0 and mask[start - 1]:
        start -= 1
    end = index
    while end + 1 < mask.size and mask[end + 1]:
        end += 1
    return start, end


def segment_trajectory(
    trajectory: Trajectory,
    f_s: float,
    *,
    gravity: float = GRAVITY,
    a_tol: float = BALLISTIC_TOLERANCE,
    sigma_multiple: float = BALLISTIC_SIGMA_MULTIPLE,
    min_flight_duration: float = MIN_FLIGHT_DURATION,
    window_seconds: float = SAVGOL_WINDOW_SECONDS,
    close_seconds: float = BALLISTIC_CLOSE_SECONDS,
    boundary_tolerance: float = BOUNDARY_TOLERANCE,
) -> tuple[tuple[Flight, ...], tuple[Carry, ...]]:
    """Segment one trajectory into flights and carries.

    Returns `(flights, carries)`, both in frame order. A trajectory shorter than
    the filter window yields nothing — with fewer samples than the smoother needs
    there is no acceleration estimate to test, and inventing one would be worse
    than reporting nothing.
    """
    min_samples = max(3, math.ceil(min_flight_duration * f_s))
    close_samples = max(1, round(close_seconds * f_s))
    times = trajectory.times(f_s)
    sigma = trajectory.uncertainty.sigma()
    window = _adapted_window(sigma, f_s, window_seconds, a_tol, min_samples)
    noise_gain = acceleration_noise_gain(window, f_s)
    flights: list[Flight] = []
    flight_indices: list[tuple[int, int]] = []

    for span_start, span_end in _contiguous_spans(trajectory.frames):
        length = span_end - span_start + 1
        if length < max(window, min_samples):
            continue
        local = slice(span_start, span_end + 1)
        _velocity, acceleration = derivatives(trajectory.positions[local], f_s, window)
        # Two thresholds, doing two different jobs. The *strict* one is the design
        # tolerance and decides whether a flight exists at all; the *widened* one
        # accounts for the samples' own uncertainty and decides how far it extends.
        # Using only the widened test lets a noisy trajectory — whose σ can widen
        # the tolerance to several m/s² — accept spans that are not free flight;
        # using only the strict one fragments a noisy flight into pieces.
        strict = ballistic_mask(
            acceleration, sigma[local], noise_gain, gravity=gravity, a_tol=a_tol, sigma_multiple=0.0
        )
        mask = ballistic_mask(
            acceleration,
            sigma[local],
            noise_gain,
            gravity=gravity,
            a_tol=a_tol,
            sigma_multiple=sigma_multiple,
        )
        if close_samples > 1:
            structure = np.ones(close_samples, dtype=bool)
            mask = np.asarray(binary_closing(mask, structure=structure, border_value=0))

        for run_start, run_end in _mask_runs(mask):
            if run_end - run_start + 1 < min_samples:
                continue
            if int(strict[run_start : run_end + 1].sum()) < min_samples // 2:
                continue
            seed = _seed_interval((span_start + run_start, span_start + run_end), window // 2)
            start, end = _refine_boundaries(
                times,
                trajectory.positions,
                sigma,
                seed,
                (span_start, span_end),
                gravity=gravity,
                tolerance=boundary_tolerance,
                close_samples=close_samples,
            )
            if end - start + 1 < min_samples:
                continue
            flight = _build_flight(
                trajectory,
                times,
                sigma,
                (start, end),
                (span_start, span_end),
                truncation_margin=window // 2,
                gravity=gravity,
            )
            flights.append(flight)
            flight_indices.append((start, end))

    return tuple(flights), _carries(trajectory, flight_indices)


def _build_flight(
    trajectory: Trajectory,
    times: np.ndarray,
    sigma: np.ndarray,
    interval: tuple[int, int],
    span: tuple[int, int],
    *,
    truncation_margin: int,
    gravity: float,
) -> Flight:
    start, end = interval
    window = slice(start, end + 1)
    fit = fit_fixed_gravity(
        times[window], trajectory.positions[window], sigma[window], gravity=gravity
    )
    free_gravity, free_sigma, free_residual = fit_free_gravity(
        times[window], trajectory.positions[window, 2], sigma[window]
    )

    start_time = float(times[start])
    end_time = float(times[end])
    start_position, start_velocity = fit.at(start_time)
    end_position, end_velocity = fit.at(end_time)

    # Apex is the zero-crossing of v_z on the fitted parabola: sub-sample, and
    # immune to the noise at the turning point where the velocity is smallest.
    apex_time = fit.origin_time + fit.velocity[2] / gravity
    apex_position = fit.at(apex_time)[0]

    # A flight whose refined end sits within half a filter window of the end of
    # the tracked data was cut off, not caught: there is no room for the carry
    # that a real throw or catch must be adjacent to. Its endpoint is lost
    # tracking and must not become an event.
    span_start, span_end = span
    return Flight(
        trajectory_id=trajectory.id,
        start_index=start,
        end_index=end,
        start_frame=int(trajectory.frames[start]),
        end_frame=int(trajectory.frames[end]),
        start_time=start_time,
        end_time=end_time,
        start_position=start_position,
        end_position=end_position,
        start_velocity=start_velocity,
        end_velocity=end_velocity,
        apex_time=apex_time,
        apex_position=apex_position,
        apex_height=float(apex_position[2] - start_position[2]),
        apex_observed=bool(start_time <= apex_time <= end_time),
        residual=fit.residual,
        chi2_per_dof=fit.chi2_per_dof,
        fitted_gravity=free_gravity,
        fitted_gravity_sigma=free_sigma,
        free_residual=free_residual,
        gravity_used=gravity,
        truncated_start=start - span_start <= truncation_margin,
        truncated_end=span_end - end <= truncation_margin,
    )


def _carries(trajectory: Trajectory, flights: list[tuple[int, int]]) -> tuple[Carry, ...]:
    """The complement of the flights inside each contiguous span."""
    carries: list[Carry] = []
    for span_start, span_end in _contiguous_spans(trajectory.frames):
        cursor = span_start
        inside = sorted(s for s in flights if span_start <= s[0] <= span_end)
        for flight_start, flight_end in inside:
            if flight_start > cursor:
                carries.append(_carry(trajectory, cursor, flight_start - 1))
            cursor = flight_end + 1
        if cursor <= span_end:
            carries.append(_carry(trajectory, cursor, span_end))
    return tuple(carries)


def _carry(trajectory: Trajectory, start: int, end: int) -> Carry:
    return Carry(
        trajectory_id=trajectory.id,
        start_index=start,
        end_index=end,
        start_frame=int(trajectory.frames[start]),
        end_frame=int(trajectory.frames[end]),
    )


@dataclass(frozen=True, eq=False)
class FlightSegmentation:
    """Every flight and carry in a session, plus how gravity was handled."""

    flights: tuple[Flight, ...]
    carries: tuple[Carry, ...]
    #: The free-fit `g` measured from the *first* pass, against nominal `GRAVITY`.
    gravity_check: GravityCheck | None
    #: The value the returned fits actually used, m/s².
    gravity_used: float
    #: True when `gravity_used` came from the data rather than from `GRAVITY`.
    calibrated: bool

    @property
    def complete_flights(self) -> tuple[Flight, ...]:
        return tuple(f for f in self.flights if f.is_complete)


def find_flights(session: Session, **kwargs: float) -> tuple[Flight, ...]:
    """Every flight in a session in time order, single pass, nominal gravity."""
    flights: list[Flight] = []
    for trajectory in session.trajectories:
        found, _carried = segment_trajectory(trajectory, session.f_s, **kwargs)
        flights.extend(found)
    return tuple(sorted(flights, key=lambda f: (f.start_time, f.trajectory_id)))


def segment_session(
    session: Session, *, calibrate: bool = True, **kwargs: float
) -> FlightSegmentation:
    """Segment a whole session, calibrating the measured gravity first.

    Two passes, and the reason is a real property of this corpus rather than
    fussiness. A fixed-`g` parabola fitted to data whose actual vertical
    acceleration differs from `g` by `Δg` leaves a systematic residual of
    `½·Δg·(t − t̄)²`: for the measured `Δg ≈ 0.26 m/s²` over a 0.46 s arc that is
    about 2 mm RMS, which swamps the 0.4 mm measurement noise. The fit residual is
    supposed to be the event's *confidence*, and a residual dominated by a
    constant instrument offset carries no information about confidence at all.

    So: pass one segments with nominal `GRAVITY` and fits `g` freely to measure
    it; pass two re-segments using the measured value, which makes the residual a
    genuine quality signal and sharpens the boundary refinement. `GRAVITY` stays
    the *reference* — `gravity_check` always reports the measured value against
    it, so the discrepancy is surfaced, never absorbed (CLAUDE.md rule 3).

    Pass `calibrate=False` for the single-pass behaviour, e.g. on synthetic data
    that is known to obey `GRAVITY` exactly.
    """
    first = find_flights(session, **kwargs)
    check: GravityCheck | None = None
    if calibrate:
        try:
            check = check_gravity(first)
        except ValueError:
            check = None

    if check is None:
        gravity_used = float(kwargs.get("gravity", GRAVITY))
        calibrated = False
    else:
        gravity_used = check.median
        calibrated = True

    flights: list[Flight] = []
    carries: list[Carry] = []
    for trajectory in session.trajectories:
        found, carried = segment_trajectory(
            trajectory, session.f_s, **{**kwargs, "gravity": gravity_used}
        )
        flights.extend(found)
        carries.extend(carried)
    return FlightSegmentation(
        flights=tuple(sorted(flights, key=lambda f: (f.start_time, f.trajectory_id))),
        carries=tuple(sorted(carries, key=lambda c: (c.start_frame, c.trajectory_id))),
        gravity_check=check,
        gravity_used=gravity_used,
        calibrated=calibrated,
    )


def event_positions(flights: tuple[Flight, ...], *, exclude_suspect: bool = True) -> np.ndarray:
    """`(N, 3)` throw and catch positions — the cloud the frame is derived from.

    Two kinds of endpoint are excluded, and both matter for DESIGN.md §5:

    * **truncated ends**, which are lost tracking rather than events — including
      them would drag the origin towards wherever tracking happened to fail;
    * **suspect flights**, which are not free flight at all. One such segment in
      `3_ball_juggling_cut` put a "catch" 1.5 m below the hands, which moves the
      derived origin and skews the hand axis.
    """
    points: list[np.ndarray] = []
    for flight in flights:
        if exclude_suspect and flight.is_suspect():
            continue
        if not flight.truncated_start:
            points.append(flight.start_position)
        if not flight.truncated_end:
            points.append(flight.end_position)
    if not points:
        return np.zeros((0, _AXES))
    return np.asarray(points)


def check_gravity(flights: tuple[Flight, ...], *, min_samples: int = 120) -> GravityCheck:
    """Combine the per-flight free-gravity fits into a session self-check.

    Only flights with at least `min_samples` samples (0.4 s at 300 Hz) count: a
    short arc constrains curvature weakly, and averaging in weakly-constrained
    fits would widen the spread without moving the centre. Raises ``ValueError``
    when nothing qualifies — an empty check must not read as a passing one.
    """
    qualifying = [f for f in flights if f.n_samples >= min_samples]
    if not qualifying:
        raise ValueError(
            f"no flight has {min_samples} or more samples, so gravity cannot be checked"
        )
    values = np.array([f.fitted_gravity for f in qualifying])
    sigmas = np.array([f.fitted_gravity_sigma for f in qualifying])
    weights = 1.0 / np.maximum(sigmas, 1e-9) ** 2
    weighted = float((values * weights).sum() / weights.sum())
    median = float(np.median(values))
    return GravityCheck(
        n_flights=len(qualifying),
        median=median,
        weighted_mean=weighted,
        weighted_sigma=float(np.sqrt(1.0 / weights.sum())),
        spread=float(values.std()),
        relative_error=(median - GRAVITY) / GRAVITY,
    )
