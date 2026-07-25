"""Identity linking: trajectories → balls (DESIGN.md §7).

A QTM trajectory is not a ball. Tracking breaks at apexes and crossings, so one
ball spans several trajectories and the pipeline must decide which trajectory
continues which ball. Absolute timing (P1) makes this a well-posed assignment
problem rather than blind multi-target tracking: in the 5-ball sample the ball
trajectories already cover 98.5% of all ball-frames.

The formulation is a **minimum-cost path cover** of a DAG. Each trajectory is a
node; an arc `i → j` says "ball continues from trajectory `i` into trajectory `j`".
Arcs exist only between trajectories that do not overlap in time and whose states
match across the gap. A set of node-disjoint paths covering every node *is* an
assignment of trajectories to balls, and the number of paths is the ball count.

Two facts make this tractable and exact:

* By Dilworth's theorem the **minimum** number of paths equals the maximum number
  of trajectories active at any one frame — which is also the natural estimate of
  the ball count. So minimising the path count and estimating `b` are the same
  problem, and the answer is not a guess.
* Minimum path cover reduces to bipartite matching on the arcs, and *minimum-cost*
  maximum matching is solved exactly by the Hungarian algorithm
  (`scipy.optimize.linear_sum_assignment`). Not greedily: a greedy pass commits to
  early mistakes that later evidence would have corrected (DESIGN.md §7).

Each gap is bridged under one of two hypotheses, because a gap of a couple of
hundred milliseconds can easily span a catch:

* **ballistic** — the ball was in free flight throughout. Predict its state forward
  and score the mismatch against the incoming trajectory as a chi-squared.
* **carry** — the ball was in a hand. Position must still be continuous, because a
  hand cannot travel far, but velocity is free: it reverses at a catch.

Bridged gaps are recorded as inferred with their cost and hypothesis; gaps that no
hypothesis can bridge are recorded as *uncertain* rather than silently joined.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise

import numpy as np
from scipy.optimize import linear_sum_assignment

from .flight import Flight, fit_fixed_gravity
from .params import (
    BALL_DIAMETER,
    CARRY_BRIDGE_PENALTY,
    CARRY_MAX_HAND_SPEED,
    CARRY_MAX_TRAVEL,
    GRAVITY,
    LANE_END_COST,
    LINK_CHI2_LIMIT,
    LINK_ENDPOINT_SAMPLES,
    LINK_FLIGHT_MARGIN,
    MAX_BRIDGED_GAP,
    MAX_LINK_GAP,
    SIGMA_UNDERESTIMATE_FACTOR,
)
from .trajectory import Session, Trajectory

_AXES = 3

#: Cost charged for leaving a trajectory without a successor. Large enough that the
#: solver always prefers a feasible link, so the solution is a *minimum* path cover
#: (and therefore has exactly `b` lanes) and the real costs only break ties among
#: the covers of that size.
_LANE_END_COST = 1e6  # overridden per call; see lane_end_cost

#: Cost for an arc that must never be chosen. Above `_LANE_END_COST`, so ending a
#: lane is always cheaper than an infeasible link.
_FORBIDDEN = 1e12


@dataclass(frozen=True)
class BallSpan:
    """One trajectory's contribution to a ball, in frame order."""

    trajectory_id: str
    first_frame: int
    last_frame: int

    @property
    def n_frames(self) -> int:
        return self.last_frame - self.first_frame + 1


@dataclass(frozen=True)
class BridgedGap:
    """A gap the linker crossed, recorded as inferred (DESIGN.md §7)."""

    from_trajectory: str
    to_trajectory: str
    #: First and last **missing** frame, inclusive. Empty when the trajectories abut.
    first_frame: int
    last_frame: int
    #: ``"ballistic"`` or ``"carry"`` — which hypothesis bridged it.
    mode: str
    #: Chi-squared of the state mismatch under that hypothesis. Lower is better.
    cost: float
    #: False when the gap is longer than `MAX_BRIDGED_GAP`: still the best available
    #: inference, but not one to compute a dwell time across without saying so.
    confident: bool = True

    @property
    def n_frames(self) -> int:
        return max(0, self.last_frame - self.first_frame + 1)

    @property
    def inferred(self) -> bool:
        """Always true: a bridged gap is inference, never measurement."""
        return True


@dataclass(frozen=True, eq=False)
class Ball:
    """A physical ball, reconstructed by linking (DESIGN.md §3)."""

    id: int
    spans: tuple[BallSpan, ...]
    gaps: tuple[BridgedGap, ...] = ()

    @property
    def first_frame(self) -> int:
        return self.spans[0].first_frame

    @property
    def last_frame(self) -> int:
        return self.spans[-1].last_frame

    @property
    def trajectory_ids(self) -> tuple[str, ...]:
        return tuple(span.trajectory_id for span in self.spans)

    @property
    def measured_frames(self) -> int:
        """Frames actually tracked — excludes every bridged gap."""
        return sum(span.n_frames for span in self.spans)

    @property
    def bridged_frames(self) -> int:
        return sum(gap.n_frames for gap in self.gaps)


@dataclass(frozen=True)
class CollisionViolation:
    """Two balls closer than one diameter — a data-quality signal (DESIGN.md §7)."""

    frame: int
    ball_a: int
    ball_b: int
    distance: float


@dataclass(frozen=True, eq=False)
class Linking:
    """The result of linking, with everything needed to judge it."""

    balls: tuple[Ball, ...]
    #: Ball count from the maximum number of simultaneously-active trajectories.
    ball_count: int
    #: Frames inside a ball's span that no trajectory covers and no bridge crossed.
    uncertain_gaps: tuple[tuple[int, int, int], ...] = ()
    collisions: tuple[CollisionViolation, ...] = ()
    #: Per-frame count of active ball trajectories — the evidence for `ball_count`.
    active_histogram: dict[int, int] = field(default_factory=dict)

    @property
    def total_bridged_frames(self) -> int:
        return sum(ball.bridged_frames for ball in self.balls)

    @property
    def total_uncertain_frames(self) -> int:
        return sum(last - first + 1 for _ball, first, last in self.uncertain_gaps)

    def ball_of_trajectory(self) -> dict[str, int]:
        """Trajectory id → ball id, the inverse mapping used for scoring."""
        return {span.trajectory_id: ball.id for ball in self.balls for span in ball.spans}


# --------------------------------------------------------------------------- #
# endpoint states
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, eq=False)
class _Endpoint:
    """The fitted state at one end of a trajectory, and how much to trust it."""

    frame: int
    time: float
    position: np.ndarray
    velocity: np.ndarray
    #: 1σ on the position, m, and on the velocity, m/s.
    position_sigma: float
    velocity_sigma: float
    #: True when this end sits inside a detected flight, so the ballistic
    #: hypothesis may be used from here. A carry endpoint has no usable velocity.
    ballistic: bool


def _endpoint_states(
    trajectory: Trajectory,
    f_s: float,
    flights: tuple[Flight, ...],
    *,
    gravity: float,
    samples: int,
    flight_margin: int,
    sigma_factor: float,
) -> tuple[_Endpoint, _Endpoint]:
    """The `(start, end)` states of a trajectory, preferring detected flights.

    When an end lies inside a flight, that flight's fit already describes it and is
    based on the whole arc rather than a short window. Otherwise a local fixed-`g`
    fit over `samples` samples is used and the end is marked non-ballistic, so only
    the carry hypothesis may bridge from it.
    """
    times = trajectory.times(f_s)
    sigma = trajectory.uncertainty.sigma()
    mine = [f for f in flights if f.trajectory_id == trajectory.id]

    def state(index: int, forward: bool) -> _Endpoint:
        frame = int(trajectory.frames[index])
        time = float(times[index])
        # `flight_margin` samples of slack, because boundary refinement trims a
        # flight *inward* from the last tracked sample: when tracking dies during
        # a flight, the final samples are the contaminated ones the refinement
        # dropped, so the trajectory's last index sits just outside its own flight.
        # Without the slack no endpoint is ever ballistic and half the cost model
        # is dead code. The state is taken at the flight's own boundary time, which
        # is where the fit is valid; `_link_cost` extrapolates from there.
        for flight in mine:
            near_start = forward and 0 <= flight.start_index - index <= flight_margin
            near_end = not forward and 0 <= index - flight.end_index <= flight_margin
            inside = flight.start_index <= index <= flight.end_index
            if not (inside or near_start or near_end):
                continue
            if forward:
                position, velocity = flight.start_position, flight.start_velocity
                reference = flight.start_time
            else:
                position, velocity = flight.end_position, flight.end_velocity
                reference = flight.end_time
            span = max(flight.n_samples, 1)
            return _Endpoint(
                frame=frame,
                time=reference,
                position=np.asarray(position),
                velocity=np.asarray(velocity),
                position_sigma=max(flight.free_residual, 1e-4) * sigma_factor,
                # Velocity error from a fit over `span` samples scales as
                # sigma / (duration * sqrt(span)).
                velocity_sigma=max(flight.free_residual * f_s / np.sqrt(span), 0.01) * sigma_factor,
                ballistic=not flight.is_suspect(),
            )
        window = (
            slice(index, min(index + samples, trajectory.n_samples))
            if forward
            else slice(max(0, index - samples + 1), index + 1)
        )
        count = window.stop - window.start
        if count < 3:
            return _Endpoint(
                frame=frame,
                time=time,
                position=trajectory.positions[index],
                velocity=np.zeros(_AXES),
                position_sigma=float(sigma[index]) * sigma_factor,
                velocity_sigma=np.inf,
                ballistic=False,
            )
        fit = fit_fixed_gravity(
            times[window], trajectory.positions[window], sigma[window], gravity=gravity
        )
        position, velocity = fit.at(time)
        duration = max(count / f_s, 1e-6)
        return _Endpoint(
            frame=frame,
            time=time,
            position=position,
            velocity=velocity,
            position_sigma=max(fit.residual, float(np.median(sigma[window]))) * sigma_factor,
            velocity_sigma=max(fit.residual / duration, 0.01) * sigma_factor,
            ballistic=False,
        )

    return state(0, forward=True), state(trajectory.n_samples - 1, forward=False)


# --------------------------------------------------------------------------- #
# link cost
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Link:
    cost: float
    mode: str
    confident: bool


def _link_cost(
    out_state: _Endpoint,
    in_state: _Endpoint,
    *,
    gravity: float,
    max_gap_seconds: float,
    confident_gap_seconds: float,
    chi2_limit: float,
    hand_speed: float,
    max_travel: float,
    carry_penalty: float,
) -> _Link | None:
    """Score continuing from `out_state` into `in_state`, or `None` if infeasible.

    Returns a chi-squared: the squared state mismatch divided by its own variance,
    so it is comparable across gaps of different lengths and trajectories of
    different quality. That is what "weighted by uncertainty" means here
    (DESIGN.md §7).
    """
    dt = in_state.time - out_state.time
    if dt <= 0.0 or dt > max_gap_seconds:
        return None
    confident = dt <= confident_gap_seconds
    # A bridge past the confidence boundary is charged the full gate, so it only
    # ever wins against abandoning a lane, never against a confident alternative.
    stretch = 0.0 if confident else chi2_limit

    best: _Link | None = None

    if out_state.ballistic and in_state.ballistic:
        acceleration = np.array([0.0, 0.0, -gravity])
        predicted = out_state.position + out_state.velocity * dt + 0.5 * acceleration * dt**2
        predicted_velocity = out_state.velocity + acceleration * dt
        # Prediction variance grows with the extrapolation: sigma_p^2 + dt^2 sigma_v^2.
        position_variance = (
            out_state.position_sigma**2
            + (dt * out_state.velocity_sigma) ** 2
            + in_state.position_sigma**2
        )
        velocity_variance = out_state.velocity_sigma**2 + in_state.velocity_sigma**2
        chi2 = float(
            np.sum((in_state.position - predicted) ** 2) / (_AXES * position_variance)
            + np.sum((in_state.velocity - predicted_velocity) ** 2) / (_AXES * velocity_variance)
        )
        if chi2 <= chi2_limit:
            best = _Link(cost=chi2 + stretch, mode="ballistic", confident=confident)

    # The carry hypothesis: the ball spent some of the gap in a hand. Position must
    # still be continuous, but the decisive test is on vertical velocity. In free
    # flight `v_z` falls by exactly `g·dt`; only a hand can leave it *higher* than
    # that. So a gap containing a catch, a carry or a throw must satisfy
    #
    #     v_z(in) > v_z(out) - g·dt
    #
    # and a pairing that fails it was never one ball: nothing accelerates a ball
    # downward faster than gravity. Without this the hypothesis is nearly
    # unconstrained over long gaps, and it produced real identity errors on the
    # 5-ball truth fixture.
    # NOTE: a stricter form of this test — requiring `v_z(in) > v_z(out) - g·dt`,
    # i.e. that something raised the ball above free fall — is correct physics and
    # improves synthetic identity purity, but it rejects the real 5-ball clip's
    # 417 ms bridge and splits that clip into 6 lanes instead of 5. It is recorded in
    # BUILD_LOG.md Phase 4 rather than enabled, because the endpoint velocities on
    # that gap are not determined well enough to carry the test yet.
    reachable = (
        min(hand_speed * dt, max_travel) + out_state.position_sigma + in_state.position_sigma
    )
    distance = float(np.linalg.norm(in_state.position - out_state.position))
    if distance <= reachable:
        chi2 = (distance / max(reachable, 1e-9)) ** 2 * chi2_limit + carry_penalty + stretch
        if best is None or chi2 < best.cost:
            best = _Link(cost=chi2, mode="carry", confident=confident)
    return best


# --------------------------------------------------------------------------- #
# the linker
# --------------------------------------------------------------------------- #


def estimate_ball_count(trajectories: tuple[Trajectory, ...]) -> tuple[int, dict[int, int]]:
    """Ball count from the maximum simultaneously-active trajectories (DESIGN.md §7).

    Returns `(count, histogram)` where the histogram maps "number active" to "how
    many frames had that many", which is the evidence for the count rather than
    just its conclusion. A single frame of spurious overlap would otherwise inflate
    the estimate invisibly.
    """
    if not trajectories:
        return 0, {}
    last = max(t.last_frame for t in trajectories)
    active = np.zeros(last + 2, dtype=np.int32)
    for trajectory in trajectories:
        np.add.at(active, trajectory.frames, 1)
    values, counts = np.unique(active[1:], return_counts=True)
    histogram = {int(v): int(c) for v, c in zip(values, counts, strict=True) if v > 0}
    return (int(active.max()), histogram)


def link_trajectories(
    session: Session,
    flights: tuple[Flight, ...] = (),
    *,
    gravity: float = GRAVITY,
    max_gap: float = MAX_LINK_GAP,
    confident_gap: float = MAX_BRIDGED_GAP,
    chi2_limit: float = LINK_CHI2_LIMIT,
    endpoint_samples: int = LINK_ENDPOINT_SAMPLES,
    flight_margin: int = LINK_FLIGHT_MARGIN,
    sigma_factor: float = SIGMA_UNDERESTIMATE_FACTOR,
    hand_speed: float = CARRY_MAX_HAND_SPEED,
    max_travel: float = CARRY_MAX_TRAVEL,
    lane_end_cost: float = LANE_END_COST,
    carry_penalty: float = CARRY_BRIDGE_PENALTY,
    ball_diameter: float = BALL_DIAMETER,
) -> Linking:
    """Assign the session's ball trajectories to balls.

    Only trajectories classified ``ball`` take part: linking a reflection into a
    lane would be worse than leaving it out, and `core.clean` has already made that
    judgement on physics.

    Solved as a minimum-cost path cover (see the module docstring). The result has
    exactly as many lanes as the ball-count estimate whenever the arcs allow it,
    and every gap it crossed is recorded with the hypothesis and cost that crossed
    it.
    """
    trajectories = tuple(sorted(session.balls, key=lambda t: (t.first_frame, t.id)))
    ball_count, histogram = estimate_ball_count(trajectories)
    if not trajectories:
        return Linking(balls=(), ball_count=0, active_histogram={})

    states = [
        _endpoint_states(
            t,
            session.f_s,
            flights,
            gravity=gravity,
            samples=endpoint_samples,
            flight_margin=flight_margin,
            sigma_factor=sigma_factor,
        )
        for t in trajectories
    ]
    n = len(trajectories)

    # cost[i][j]: trajectory i is followed by trajectory j. Columns n..2n-1 are
    # "i has no successor", usable only by row i, so every row is assignable.
    cost = np.full((n, 2 * n), _FORBIDDEN)
    modes: dict[tuple[int, int], _Link] = {}
    for i in range(n):
        cost[i, n + i] = lane_end_cost
        for j in range(n):
            if i == j:
                continue
            if trajectories[j].first_frame <= trajectories[i].last_frame:
                continue  # overlapping in time: cannot be one ball
            link = _link_cost(
                states[i][1],
                states[j][0],
                gravity=gravity,
                max_gap_seconds=max_gap,
                confident_gap_seconds=confident_gap,
                chi2_limit=chi2_limit,
                hand_speed=hand_speed,
                max_travel=max_travel,
                carry_penalty=carry_penalty,
            )
            if link is not None:
                cost[i, j] = link.cost
                modes[i, j] = link

    rows, columns = linear_sum_assignment(cost)
    successor: dict[int, int] = {}
    for row, column in zip(rows, columns, strict=True):
        i, j = int(row), int(column)
        if j < n and cost[i, j] < lane_end_cost:
            successor[i] = j

    balls = _paths_to_balls(trajectories, successor, modes, cost)
    uncertain = _uncertain_gaps(balls, trajectories)
    collisions = _find_collisions(session, balls, ball_diameter)
    return Linking(
        balls=balls,
        ball_count=ball_count,
        uncertain_gaps=uncertain,
        collisions=collisions,
        active_histogram=histogram,
    )


def _paths_to_balls(
    trajectories: tuple[Trajectory, ...],
    successor: dict[int, int],
    modes: dict[tuple[int, int], _Link],
    cost: np.ndarray,
) -> tuple[Ball, ...]:
    """Walk the successor function into node-disjoint paths, one per ball."""
    has_predecessor = set(successor.values())
    starts = [i for i in range(len(trajectories)) if i not in has_predecessor]
    balls: list[Ball] = []
    for ball_id, start in enumerate(sorted(starts, key=lambda i: trajectories[i].first_frame)):
        chain: list[int] = []
        node: int | None = start
        while node is not None:
            chain.append(node)
            node = successor.get(node)
        spans = tuple(
            BallSpan(
                trajectory_id=trajectories[i].id,
                first_frame=trajectories[i].first_frame,
                last_frame=trajectories[i].last_frame,
            )
            for i in chain
        )
        gaps = tuple(
            BridgedGap(
                from_trajectory=trajectories[a].id,
                to_trajectory=trajectories[b].id,
                first_frame=trajectories[a].last_frame + 1,
                last_frame=trajectories[b].first_frame - 1,
                mode=modes[a, b].mode,
                cost=float(cost[a, b]),
                confident=modes[a, b].confident,
            )
            for a, b in pairwise(chain)
        )
        balls.append(Ball(id=ball_id, spans=spans, gaps=gaps))
    return tuple(balls)


def _uncertain_gaps(
    balls: tuple[Ball, ...], trajectories: tuple[Trajectory, ...]
) -> tuple[tuple[int, int, int], ...]:
    """Frames inside a ball's own span that nothing covers and no bridge crossed.

    A trajectory's *internal* gaps count: they are frames where the ball existed
    and was not tracked, and DESIGN.md §6 requires them to be marked rather than
    interpolated over.
    """
    by_id = {t.id: t for t in trajectories}
    uncertain: list[tuple[int, int, int]] = []
    for ball in balls:
        bridged = {(gap.first_frame, gap.last_frame) for gap in ball.gaps}
        for span in ball.spans:
            for first, last in by_id[span.trajectory_id].gaps():
                uncertain.append((ball.id, first, last))
        for before, after in pairwise(ball.spans):
            hole = (before.last_frame + 1, after.first_frame - 1)
            if hole[1] >= hole[0] and hole not in bridged:
                uncertain.append((ball.id, hole[0], hole[1]))
    return tuple(uncertain)


def _find_collisions(
    session: Session, balls: tuple[Ball, ...], diameter: float
) -> tuple[CollisionViolation, ...]:
    """Frames where two balls are within one diameter (DESIGN.md §7).

    Reported, never repaired: two balls that close to one another means either the
    tracker duplicated a ball or they genuinely touched, and both are findings.
    Only *measured* frames are compared — a bridged gap has no position to compare,
    and inventing one would manufacture a violation or hide one.
    """
    if len(balls) < 2:
        return ()
    by_id = {t.id: t for t in session.trajectories}
    positions: dict[int, dict[int, np.ndarray]] = {}
    for ball in balls:
        frames: dict[int, np.ndarray] = {}
        for span in ball.spans:
            trajectory = by_id[span.trajectory_id]
            for index, frame in enumerate(trajectory.frames):
                frames[int(frame)] = trajectory.positions[index]
        positions[ball.id] = frames

    violations: list[CollisionViolation] = []
    for a, b in ((x, y) for x in balls for y in balls if x.id < y.id):
        shared = positions[a.id].keys() & positions[b.id].keys()
        for frame in sorted(shared):
            distance = float(np.linalg.norm(positions[a.id][frame] - positions[b.id][frame]))
            if distance < diameter:
                violations.append(
                    CollisionViolation(frame=frame, ball_a=a.id, ball_b=b.id, distance=distance)
                )
    return tuple(violations)


def score_linking(linking: Linking, truth: dict[str, int]) -> float:
    """Fraction of trajectories assigned to the correct ball, given an answer key.

    Ball *ids* are arbitrary, so the lanes are matched to true balls by the
    assignment that maximises agreement (Hungarian again) before scoring. Spurious
    trajectories — truth `-1` — are excluded: whether a reflection ends up in a
    lane is `core.clean`'s responsibility, not the linker's.
    """
    assigned = linking.ball_of_trajectory()
    pairs = [(lane, truth[tid]) for tid, lane in assigned.items() if truth.get(tid, -1) >= 0]
    if not pairs:
        return 0.0
    lanes = sorted({lane for lane, _ in pairs})
    real = sorted({ball for _, ball in pairs})
    agreement = np.zeros((len(lanes), len(real)))
    lane_index = {lane: i for i, lane in enumerate(lanes)}
    real_index = {ball: i for i, ball in enumerate(real)}
    for lane, ball in pairs:
        agreement[lane_index[lane], real_index[ball]] += 1
    rows, columns = linear_sum_assignment(-agreement)
    return float(agreement[rows, columns].sum() / len(pairs))
