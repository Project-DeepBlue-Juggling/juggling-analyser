"""Synthetic ground truth: clean truth in, realistic mocap out (DESIGN.md §12).

Airtime is the shared source of physics truth and exports exact labelled
trajectories (`io.truth` reads them into :class:`Truth`). This module is the other
half: it **degrades** that truth into something that looks like what QTM actually
hands over, and it hands back a labelled answer key so the linker (DESIGN.md §7)
can be *scored* rather than eyeballed.

Everything here is a pure function of its arguments and randomness arrives as an
explicit ``np.random.Generator`` (CLAUDE.md rule 1), so a seed identifies a
synthetic recording exactly.

The degradations, in the order they are applied:

1. **Position error** — per-sample, per-axis, with a per-trajectory base σ so the
   noise level varies between balls as well as within one.
2. **Identity swaps** — two balls that pass close enough may exchange the rest of
   their samples, so one output trajectory carries two physical balls.
3. **Occlusion dropouts** — concentrated at flight apexes (where `v_z ≈ 0`, the
   ball lingers, and the camera geometry is worst) and at ball crossings.
4. **Fragmentation** — a dropout either breaks the lane into two trajectories or
   leaves an internal hole inside one (QTM's *Mixed*), per
   :attr:`DegradationParams.internal_gap_fraction`.
5. **Spurious short trajectories** — reflections: a few samples of jitter near but
   not on a real ball, and nowhere near ballistic.

## Two things about this model that are deliberate and easy to misread

**The reported σ is optimistic on purpose.** :class:`Uncertainty` is populated with
``true_sigma / sigma_report_factor``, default 3.0, because that is what the real
instrument does: BUILD_LOG Phase 2 measured clean flights fitting to ~1.2 mm where
QTM's reported residual was ~0.35 mm, with χ²/dof running at 15–35 rather than 1.
The linker will be weighted by the reported σ, so a linker that treats it as a
calibrated 1σ **must** be punished by the synthetic data exactly as it is punished
by the real thing. Do not "fix" this by reporting the truth. The understatement has
a second half, too: the per-sample variation in the reported σ is only *weakly*
predictive of the actual error (measured correlation 0.27–0.31), which is what
:attr:`DegradationParams.error_sigma_coupling` encodes.

**The position error is not white.** Measured on the corpus (third-difference
estimator over 146 clean flights), the genuinely per-sample component of QTM's
position error is only ~0.035 mm — about a tenth of the reported σ and a
thirtieth of the true error. The rest is smooth: the data is an excellent parabola
that sits in the wrong place, not a noisy one. That distinction is load-bearing,
because differentiating twice amplifies white noise by ~`f_s²` and leaves a smooth
error almost untouched: injecting the whole 3× understatement as white noise
destroys flight segmentation (43 flights → 3 on a 5-ball fixture) while the real
recordings segment cleanly at the same reported σ. So the injected error is split
into a white part and a slowly-varying part by
:attr:`DegradationParams.white_error_fraction`, calibrated from that measurement.
This is a deliberate extension of DESIGN.md §12's "Gaussian position noise";
CLAUDE.md rule 3 says the measurement wins.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

import numpy as np

from .params import BALL_DIAMETER, RESIDUAL_SIGMA_FLOOR
from .trajectory import Piece, Session, Trajectory, Uncertainty

_AXES = 3

#: The event kinds an Airtime truth export carries (DESIGN.md §3's `Event.kind`
#: minus `drop`, which a clean simulation never produces).
EVENT_KINDS = frozenset({"throw", "catch", "apex"})

#: ``""`` is permitted because an apex belongs to a ball, not to a hand; Airtime
#: happens to label it with the throwing hand, and both are accepted.
HAND_NAMES = frozenset({"left", "right", ""})


# --------------------------------------------------------------------------- #
# the truth types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, eq=False)
class TruthEvent:
    """One exactly-known event from the simulator.

    ``frame`` is the absolute **1-based** sample the event is nearest to and
    ``time`` is the exact instant in seconds — events do not land on sample
    boundaries, so ``time`` is the truth and ``frame`` is a convenience. Airtime
    rounds half **up**: ``frame = floor(time · f_s + 1.5)``.

    ``eq=False`` because ``position`` is a numpy array; a generated ``__eq__``
    would return an array and silently poison any ``==``.
    """

    #: ``throw`` | ``catch`` | ``apex``.
    kind: str
    ball: int
    #: ``left`` | ``right``, or ``""`` when the event is not tied to a hand.
    hand: str
    #: Absolute 1-based frame index (NOTATION.md § Conventions).
    frame: int
    #: Exact event time in seconds, with frame 1 at ``t = 0``.
    time: float
    #: ``(3,)`` position in metres, juggling frame.
    position: np.ndarray
    #: Siteswap digit `h` for a throw; ``None`` otherwise.
    throw_value: int | None = None

    def __post_init__(self) -> None:
        position = np.asarray(self.position, dtype=np.float64)
        object.__setattr__(self, "position", position)
        if self.kind not in EVENT_KINDS:
            raise ValueError(
                f"unknown event kind {self.kind!r}, expected one of {sorted(EVENT_KINDS)}"
            )
        if self.hand not in HAND_NAMES:
            raise ValueError(f"unknown hand {self.hand!r}, expected one of {sorted(HAND_NAMES)}")
        if self.ball < 0:
            raise ValueError(f"ball index must be non-negative, got {self.ball}")
        if self.frame < 1:
            raise ValueError(f"frames are 1-based; got frame={self.frame}")
        if position.shape != (_AXES,):
            raise ValueError(f"event position must be (3,), got {position.shape}")
        if not np.all(np.isfinite(position)):
            raise ValueError(f"{self.kind} event on ball {self.ball} has a non-finite position")
        if not math.isfinite(self.time):
            raise ValueError(f"{self.kind} event on ball {self.ball} has a non-finite time")
        # A throw without its value is unusable: the ordinal siteswap digit is the
        # one thing the simulator knows and the analyser must recover (§8).
        if self.kind == "throw" and self.throw_value is None:
            raise ValueError(f"throw of ball {self.ball} at frame {self.frame} has no throw_value")
        if self.throw_value is not None and self.throw_value < 0:
            raise ValueError(f"throw_value must be non-negative, got {self.throw_value}")


@dataclass(frozen=True)
class AverageTheorem:
    """`b = mean(h)` measured on a truth export, as data rather than a raise.

    The identity holds over a whole period of a valid vanilla pattern
    (NOTATION.md § Shared identities), but a *finite window* of one need not
    satisfy it: a clip that starts and ends mid-cycle over-samples whichever
    throws happen to fall inside it. `423` truncated to 27 throws reads 3.52
    against 3 balls for exactly that reason. So a mismatch is a finding about the
    generator or the window, never a parse error (CLAUDE.md rule 3).
    """

    ball_count: int
    n_throws: int
    mean_throw_value: float

    @property
    def error(self) -> float:
        """`mean(h) − b`. Zero for a whole number of periods."""
        return self.mean_throw_value - self.ball_count

    def holds(self, tolerance: float = 1e-9) -> bool:
        return abs(self.error) <= tolerance


@dataclass(frozen=True, eq=False)
class Truth:
    """A clean, exactly-labelled simulated recording (DESIGN.md §12 layer 2).

    ``positions`` is ``(ball_count, frame_count, 3)`` in metres in the juggling
    frame, sampled at ``f_s``; row ``k`` is absolute frame ``k + 1``. Every ball
    is present at every frame — occlusion is :func:`degrade`'s job, not the
    simulator's.
    """

    pattern: str
    ball_count: int
    hand_count: int
    #: `τ_b`, s.
    beat_period: float
    #: `r_d = t_d / (n_h · τ_b)`.
    dwell_ratio: float
    #: The `g` the simulation used, m/s². Recorded rather than assumed, so a
    #: fixture generated with a different constant cannot silently pass.
    gravity: float
    f_s: float
    frame_count: int
    #: ``(ball_count, frame_count, 3)`` m, juggling frame.
    positions: np.ndarray
    events: tuple[TruthEvent, ...]

    def __post_init__(self) -> None:
        positions = np.asarray(self.positions, dtype=np.float64)
        object.__setattr__(self, "positions", positions)
        if self.ball_count < 1:
            raise ValueError(f"ball_count must be positive, got {self.ball_count}")
        if self.hand_count < 1:
            raise ValueError(f"hand_count must be positive, got {self.hand_count}")
        if self.frame_count < 1:
            raise ValueError(f"frame_count must be positive, got {self.frame_count}")
        if self.f_s <= 0.0:
            raise ValueError(f"f_s must be positive, got {self.f_s}")
        if self.beat_period <= 0.0:
            raise ValueError(f"beat_period must be positive, got {self.beat_period}")
        if self.dwell_ratio < 0.0:
            raise ValueError(f"dwell_ratio must be non-negative, got {self.dwell_ratio}")
        if self.gravity <= 0.0:
            raise ValueError(f"gravity must be positive, got {self.gravity}")
        expected = (self.ball_count, self.frame_count, _AXES)
        if positions.shape != expected:
            raise ValueError(f"positions must be {expected}, got {positions.shape}")
        if not np.all(np.isfinite(positions)):
            raise ValueError("positions contain non-finite values")
        for event in self.events:
            if event.ball >= self.ball_count:
                raise ValueError(
                    f"{event.kind} event names ball {event.ball} but there are "
                    f"only {self.ball_count} balls"
                )
            if event.frame > self.frame_count:
                raise ValueError(
                    f"{event.kind} event on ball {event.ball} is at frame "
                    f"{event.frame}, past the last frame {self.frame_count}"
                )

    # -- shape ------------------------------------------------------------ #

    @property
    def n_balls(self) -> int:
        """Number of balls — the spelling downstream code reads best."""
        return self.ball_count

    @property
    def duration(self) -> float:
        """Recording length in seconds. Frame 1 is `t = 0`, so this is `N/f_s`."""
        return self.frame_count / self.f_s

    @property
    def dwell_time(self) -> float:
        """`t_d = r_d · n_h · τ_b`, s (NOTATION.md § Shared identities)."""
        return self.dwell_ratio * self.hand_count * self.beat_period

    def times(self) -> np.ndarray:
        """``(frame_count,)`` sample times in seconds, `t = (k − 1)/f_s`."""
        return np.arange(self.frame_count, dtype=np.float64) / self.f_s

    def position_at(self, ball: int, frame: int) -> np.ndarray:
        """``(3,)`` true position of ``ball`` at absolute 1-based ``frame``, m."""
        if not 1 <= frame <= self.frame_count:
            raise ValueError(f"frame {frame} outside 1..{self.frame_count}")
        return np.asarray(self.positions[ball, frame - 1])

    # -- events ----------------------------------------------------------- #

    def of_kind(self, kind: str) -> tuple[TruthEvent, ...]:
        """Every event of one kind, in file order."""
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {kind!r}")
        return tuple(e for e in self.events if e.kind == kind)

    def throws(self) -> tuple[TruthEvent, ...]:
        return self.of_kind("throw")

    def catches(self) -> tuple[TruthEvent, ...]:
        return self.of_kind("catch")

    def apexes(self) -> tuple[TruthEvent, ...]:
        return self.of_kind("apex")

    def events_for(self, ball: int) -> tuple[TruthEvent, ...]:
        """Every event belonging to one ball, in frame order."""
        return tuple(sorted((e for e in self.events if e.ball == ball), key=lambda e: e.time))

    def average_theorem(self) -> AverageTheorem:
        """`b` against `mean(h)` over the throws present, as data (§8 step 3)."""
        values = [e.throw_value for e in self.throws() if e.throw_value is not None]
        mean = float(np.mean(values)) if values else float("nan")
        return AverageTheorem(
            ball_count=self.ball_count, n_throws=len(values), mean_throw_value=mean
        )

    def summary(self) -> str:
        counts = {kind: len(self.of_kind(kind)) for kind in sorted(EVENT_KINDS)}
        parts = ", ".join(f"{n} {kind}" for kind, n in counts.items())
        return (
            f"truth {self.pattern!r}: {self.ball_count} balls, {self.hand_count} hands\n"
            f"  {self.f_s:g} Hz, {self.duration:.2f} s ({self.frame_count} frames)\n"
            f"  τ_b = {self.beat_period:g} s, r_d = {self.dwell_ratio:g}, "
            f"g = {self.gravity:g} m/s²\n"
            f"  {parts}"
        )


# --------------------------------------------------------------------------- #
# crossings — where real tracking fails
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Crossing:
    """The closest approach of two balls, below a threshold distance.

    A crossing is where mocap tracking actually breaks: two markers merge in the
    camera image, so one is lost and the tracker may resume on the wrong one
    (DESIGN.md §7). One :class:`Crossing` per closest-approach event, not one per
    frame, so a slow pass does not count as fifty crossings.
    """

    #: Absolute 1-based frame of closest approach.
    frame: int
    ball_a: int
    ball_b: int
    #: Centre-to-centre separation at that frame, m.
    distance: float


def crossing_events(positions: np.ndarray, threshold: float) -> tuple[Crossing, ...]:
    """Closest approaches below ``threshold`` metres, in frame order.

    ``positions`` is ``(n_balls, n_frames, 3)`` in metres. Returns one
    :class:`Crossing` per contiguous run of frames in which a pair is within the
    threshold, taken at the run's minimum separation. Deterministic and free of
    randomness, so the same truth always yields the same crossing list.
    """
    if positions.ndim != 3 or positions.shape[2] != _AXES:
        raise ValueError(f"positions must be (n_balls, n_frames, 3), got {positions.shape}")
    if threshold <= 0.0:
        raise ValueError(f"threshold must be positive, got {threshold}")
    n_balls = positions.shape[0]
    found: list[Crossing] = []
    for a in range(n_balls):
        for b in range(a + 1, n_balls):
            distance = np.linalg.norm(positions[a] - positions[b], axis=1)
            for start, end in _true_runs(distance < threshold):
                offset = int(np.argmin(distance[start : end + 1]))
                index = start + offset
                found.append(
                    Crossing(
                        frame=index + 1,
                        ball_a=a,
                        ball_b=b,
                        distance=float(distance[index]),
                    )
                )
    return tuple(sorted(found, key=lambda c: (c.frame, c.ball_a, c.ball_b)))


def _true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Inclusive ``(start, end)`` 0-based index runs of True."""
    if not mask.any():
        return []
    padded = np.concatenate([[False], mask, [False]])
    edges = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1) - 1
    return [(int(s), int(e)) for s, e in zip(starts, ends, strict=True)]


# --------------------------------------------------------------------------- #
# the knobs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DegradationParams:
    """Every knob of the degradation model, each with its derivation.

    The numbers in the presets below are calibrated against the two real clips
    (see :data:`CLEAN_PRESET` and :data:`NOISY_PRESET`); the defaults here are the
    clean-clip values, so ``DegradationParams()`` is a usable starting point.
    """

    # -- position error --------------------------------------------------- #

    #: Median of the per-**trajectory** base σ, m. This is the *reported* σ, the
    #: value that ends up in :class:`Uncertainty`. Measured on the 5-ball clip:
    #: the ten ball trajectories have per-trajectory median σ of 0.394–0.493 mm.
    sigma_base_median: float = 4.4e-4

    #: Log-normal spread of that base σ **between** trajectories. Measured: the
    #: standard deviation of `log(median σ)` across ball trajectories is 0.066 on
    #: the 5-ball clip and 0.320 on the 3-ball clip — the noisy recording differs
    #: from ball to ball far more than the clean one does.
    sigma_base_log_spread: float = 0.07

    #: Log-normal spread of the per-**sample** σ about its trajectory's base.
    #: Measured within-trajectory `sd(log σ)` is 0.66–0.78 (5-ball) and 0.86–1.11
    #: (3-ball). QTM's residual spikes whenever a marker is seen by fewer cameras,
    #: which is a per-sample property of the camera geometry.
    sigma_sample_log_spread: float = 0.80

    #: Lower clamp on the reported σ, m — `core.params.RESIDUAL_SIGMA_FLOOR`, the
    #: same floor the reader applies, so synthetic and real σ have the same shape
    #: at the bottom (1.7–2.2% of real samples sit on it).
    sigma_floor: float = RESIDUAL_SIGMA_FLOOR

    #: Upper clamp on the reported σ, m. The two real clips top out at 5.743 mm
    #: and 5.890 mm from 24 452 and 25 013 samples — close enough to each other to
    #: read as a saturation in QTM rather than a coincidence.
    sigma_ceiling: float = 6.0e-3

    #: How much the *reported* σ understates the true position error.
    #:
    #: **Deliberately optimistic, because the instrument's is.** BUILD_LOG Phase 2:
    #: clean flights fit to ~1.2 mm where QTM reports ~0.35 mm, and χ²/dof runs at
    #: 15–35 instead of 1. The linker is weighted by the reported σ, so synthetic
    #: data has to punish over-confidence the same way the real data does.
    sigma_report_factor: float = 3.0

    #: How strongly the *injected* error follows the per-sample reported σ, as an
    #: exponent: injected σ = `sigma_report_factor · base · (σ_i / base) ** coupling`.
    #: 0 makes the error scale constant within a trajectory, 1 makes it track the
    #: reported σ exactly.
    #:
    #: Measured on the corpus, `corr(log σ_reported, log |fit residual|)` is only
    #: 0.27–0.31, and across σ deciles the residual grows ~2.7× while σ grows ~5× —
    #: an exponent of ~0.6. QTM's residual spikes are mostly camera geometry, not
    #: position error: the 3-ball clip reports σ up to 5.9 mm while its median
    #: free-gravity fit residual is 0.42 mm. So the reported σ is not merely biased
    #: low, it is only **weakly informative per sample** — the second half of the
    #: understatement story, and something a linker must not assume away. 0.5 also
    #: keeps the tail sane: fully coupling a σ that reaches 6 mm to a 3× error
    #: factor would put 18 mm outliers in the data, which no real flight contains.
    error_sigma_coupling: float = 0.5

    #: Share of the true error σ carried by the **white**, per-sample component;
    #: the remainder is carried by a slowly-varying one, so that the two add in
    #: quadrature to `sigma_report_factor · reported σ`.
    #:
    #: Measured on the corpus with a third-difference estimator over 146 clean
    #: flights, the purely white part of QTM's position error is ~0.035 mm — about
    #: 0.09 of the reported σ and 0.03 of the true error. 0.10 keeps the model
    #: marginally noisier at high frequency than the corpus, which errs on the
    #: side of not tuning downstream code to unrealistically smooth data.
    white_error_fraction: float = 0.10

    #: Correlation time of the smooth error component, s. It must be long
    #: compared with the Savitzky–Golay window (70 ms, DESIGN.md §13) or the
    #: smooth error behaves like white noise under double differentiation, and
    #: short compared with a flight (0.5–0.95 s) or the parabola absorbs it
    #: entirely and the fit residual stops reflecting the error at all.
    smooth_error_seconds: float = 0.25

    #: 1σ of a per-ball constant offset along **+Y (forward)**, m.
    #:
    #: Airtime's default hand preset is a *line*: `y` is identically 0.0 in every
    #: fixture, so the throw/catch cloud is collinear and `core.frame`'s second
    #: principal axis is degenerate. A constant per-ball offset is the cheapest
    #: honest fix — it is exactly ballistic (a constant is absorbed by a parabola
    #: fit's position term) and it gives the cloud real depth extent. 30 mm is the
    #: order of a juggler's actual front-back scatter.
    forward_spread: float = 0.03

    # -- occlusion dropouts ----------------------------------------------- #

    #: Probability that a flight **apex** costs the tracker the ball.
    #:
    #: Elevated, not certain: real tracking often survives an apex. The apex is
    #: where `v_z ≈ 0`, so the ball lingers, and it is the top of the volume where
    #: camera coverage is thinnest. The 5-ball clip's 10 ball trajectories over
    #: 5 balls are 5 breaks against ~81 apexes, and the crossings contribute a
    #: little on top, so ~0.04.
    apex_dropout_probability: float = 0.040

    #: Half-width of the uniform jitter on where an apex dropout is centred, s.
    #: A dropout does not begin exactly at the turning point.
    apex_jitter_seconds: float = 0.06

    #: Separation below which two balls count as crossing, m. Three ball
    #: diameters: markers merge well before the balls touch, and DESIGN.md §7
    #: names "a few ball diameters" as the scale.
    crossing_distance: float = 3.0 * BALL_DIAMETER

    #: Probability that a crossing costs the tracker *each* of the two balls,
    #: drawn independently. Low, because a cascade has many crossings and most
    #: survive; the apex is the dominant failure mode in this corpus.
    crossing_dropout_probability: float = 0.004

    #: Dropouts per ball per second from everything else — a camera dropping out,
    #: a hand passing in front of a marker. Small: the corpus's losses are
    #: overwhelmingly at apexes and crossings.
    background_dropout_rate: float = 0.0

    #: Median length of a dropout that **breaks** the trajectory, s, log-normal.
    #: 5-ball clip: 383 missing frames over 5 breaks is 77 frames = 0.26 s; the
    #: log-normal *mean* exceeds its median, so the median knob sits a little
    #: below that.
    gap_median_seconds: float = 0.24

    #: Log-normal spread of that length.
    gap_log_spread: float = 0.45

    #: Share of dropouts that leave an **internal hole** in one trajectory
    #: instead of breaking it — QTM's *Mixed*. The 5-ball clip has none; the
    #: 3-ball clip has 5 internal gaps against 16 breaks, so about a quarter.
    internal_gap_fraction: float = 0.0

    #: Median length of an internal gap, s. Measured on the 3-ball clip: 12, 14,
    #: 16, 26, 27 frames, median 16 = 0.053 s. Internal gaps are an order of
    #: magnitude shorter than breaks, which is the tracker's own logic showing
    #: through: a short loss is bridged and identity survives, a long one is not.
    internal_gap_median_seconds: float = 0.053

    #: Log-normal spread of the internal-gap length.
    internal_gap_log_spread: float = 0.30

    #: Shortest trajectory the model will emit; shorter slivers between two
    #: dropouts become part of the loss instead.
    #:
    #: 15 samples is `core.clean.MIN_LIFETIME`: below it a fragment is classified
    #: `spurious` on lifetime alone, so the real clips' *ball*-trajectory length
    #: statistics — which is what the calibration targets are — can never contain
    #: one. Emitting them would compare unlike with unlike. It is also physically
    #: reasonable: a tracker that loses a marker, sees it for 40 ms and loses it
    #: again does not start a trajectory for those 40 ms.
    min_trajectory_samples: int = 15

    # -- identity swaps --------------------------------------------------- #

    #: Separation below which two balls may exchange identity, m.
    #:
    #: Three ball diameters, **not** one, and the reason is measured. Markers
    #: merge in the camera image well before the balls touch, and an idealised
    #: pattern is more regular than a real one: the minimum ball separation over
    #: the whole Airtime fixture set is 138–199 mm (`423` alone dips to 62 mm),
    #: because the fixtures are planar with `y ≡ 0` and every arc is identical. A
    #: one-diameter threshold therefore fires on almost no synthetic recording,
    #: and the failure mode the linker most needs to survive would go untested.
    swap_distance: float = 3.0 * BALL_DIAMETER

    #: Probability that a close-enough crossing swaps the two lanes' remaining
    #: samples. This is the failure mode the linker must survive, so it is real
    #: rather than cosmetic: at the default a 5-ball, 16 s clip gets a handful.
    swap_probability: float = 0.02

    # -- spurious reflections --------------------------------------------- #

    #: Spurious trajectories per second. Measured: 9 spurious in 16.6 s (5-ball)
    #: and 33 in 30.3 s (3-ball) — 0.54/s and 1.09/s.
    spurious_rate: float = 0.55

    #: Sample-count bounds of a reflection, inclusive.
    #:
    #: DESIGN.md §12 says 3–20. The default caps at 14, one below
    #: `core.clean.MIN_LIFETIME`, so every reflection this model emits is
    #: classified `spurious` on lifetime alone. Raise it to 20 and the longer ones
    #: land in `unknown` instead — which is a correct answer, not a bug, but it
    #: makes the fixture's labels less crisp.
    spurious_min_samples: int = 3
    spurious_max_samples: int = 14

    #: Distance band from the anchoring ball, m. Near enough that a linker has to
    #: consider it, far enough that it is not the ball.
    spurious_offset_min: float = 0.05
    spurious_offset_max: float = 0.40

    #: Per-sample random-walk step of a reflection, m. At 300 Hz this implies an
    #: acceleration of order `step · f_s²` ≈ 1800 m/s², so a reflection cannot
    #: pass the ballistic test however lucky the fit — which is the point:
    #: DESIGN.md §12 requires that they not look ballistic.
    spurious_step: float = 0.02

    #: Multiplier on a reflection's reported σ. A poorly-conditioned
    #: reconstruction from two rays has a larger residual than a real marker.
    spurious_sigma_factor: float = 2.0

    def __post_init__(self) -> None:
        for name in (
            "sigma_base_median",
            "sigma_ceiling",
            "sigma_report_factor",
            "smooth_error_seconds",
            "crossing_distance",
            "gap_median_seconds",
            "internal_gap_median_seconds",
            "swap_distance",
            "spurious_step",
            "spurious_sigma_factor",
        ):
            if getattr(self, name) <= 0.0:
                raise ValueError(f"{name} must be positive, got {getattr(self, name)}")
        for name in (
            "sigma_base_log_spread",
            "sigma_sample_log_spread",
            "sigma_floor",
            "forward_spread",
            "apex_jitter_seconds",
            "background_dropout_rate",
            "gap_log_spread",
            "internal_gap_log_spread",
            "spurious_rate",
            "spurious_offset_min",
        ):
            if getattr(self, name) < 0.0:
                raise ValueError(f"{name} must be non-negative, got {getattr(self, name)}")
        for name in (
            "error_sigma_coupling",
            "white_error_fraction",
            "apex_dropout_probability",
            "crossing_dropout_probability",
            "internal_gap_fraction",
            "swap_probability",
        ):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be a probability, got {getattr(self, name)}")
        if self.sigma_ceiling < self.sigma_floor:
            raise ValueError(
                f"sigma_ceiling {self.sigma_ceiling} is below sigma_floor {self.sigma_floor}"
            )
        if self.min_trajectory_samples < 1:
            raise ValueError(
                f"min_trajectory_samples must be positive, got {self.min_trajectory_samples}"
            )
        if self.spurious_min_samples < 1:
            raise ValueError(
                f"spurious_min_samples must be positive, got {self.spurious_min_samples}"
            )
        if self.spurious_max_samples < self.spurious_min_samples:
            raise ValueError(
                f"spurious_max_samples {self.spurious_max_samples} is below "
                f"spurious_min_samples {self.spurious_min_samples}"
            )
        if self.spurious_offset_max < self.spurious_offset_min:
            raise ValueError(
                f"spurious_offset_max {self.spurious_offset_max} is below "
                f"spurious_offset_min {self.spurious_offset_min}"
            )


#: Calibrated against `5_ball_juggling_cut_balls_only` — **5 balls, 4967 frames,
#: 16.6 s**. That clip is the clean end of the range: 10 ball trajectories for
#: 5 balls, no internal gaps, 98.5% frame coverage, reported σ median 0.441 mm.
CLEAN_PRESET = DegradationParams()

#: Calibrated against `3_ball_juggling_cut` — **3 balls, 9101 frames, 30.3 s**,
#: the noisy acceptance recording (DESIGN.md §12 layer 3). 19 ball trajectories
#: for 3 balls, 5 internal gaps, 91.6% coverage, reported σ median 0.559 mm.
#:
#: Fragmentation per ball depends on the clip's length as well as its quality,
#: which is why the two presets are calibrated on differently-shaped clips rather
#: than on one clip at two noise levels.
NOISY_PRESET = replace(
    CLEAN_PRESET,
    sigma_base_median=5.6e-4,
    sigma_base_log_spread=0.32,
    sigma_sample_log_spread=0.95,
    apex_dropout_probability=0.170,
    crossing_dropout_probability=0.02,
    gap_median_seconds=0.38,
    gap_log_spread=0.55,
    internal_gap_fraction=0.24,
    swap_probability=0.05,
    spurious_rate=1.1,
)


# --------------------------------------------------------------------------- #
# the answer key
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DegradationTruth:
    """The labelled answer key for one degraded recording (DESIGN.md §12).

    This is what makes synthetic data worth generating: the linker (§7) can be
    *scored* against it instead of inspected. Every trajectory the degrader
    emitted appears in :attr:`ball_of_trajectory`.
    """

    #: Trajectory id → the physical ball it belongs to, ``-1`` for a reflection.
    #: For a trajectory that contains an identity swap this is the ball
    #: contributing the **most** samples, and the id is also in
    #: :attr:`swapped_ids` — a single integer cannot describe two balls.
    ball_of_trajectory: Mapping[str, int]
    #: Ids of the reflections. Disjoint from the ball trajectories.
    spurious_ids: frozenset[str]
    #: Ids of trajectories whose samples come from more than one ball.
    swapped_ids: frozenset[str]
    #: Ball → ``(first_missing_frame, last_missing_frame)`` pairs, 1-based
    #: inclusive: the frames where that ball appears in **no** trajectory. Every
    #: ball has an entry, empty when it was tracked throughout.
    introduced_gaps: Mapping[int, tuple[tuple[int, int], ...]]

    @property
    def n_trajectories(self) -> int:
        return len(self.ball_of_trajectory)

    def true_ball(self, trajectory_id: str) -> int:
        """The ball a trajectory belongs to, ``-1`` for a reflection."""
        if trajectory_id not in self.ball_of_trajectory:
            raise KeyError(f"no answer-key entry for trajectory {trajectory_id!r}")
        return self.ball_of_trajectory[trajectory_id]

    def ids_of_ball(self, ball: int) -> tuple[str, ...]:
        """Every trajectory attributed to one ball, in id order."""
        return tuple(sorted(i for i, b in self.ball_of_trajectory.items() if b == ball))

    def missing_frames(self, ball: int) -> int:
        """Frames of one ball that no trajectory covers."""
        return sum(last - first + 1 for first, last in self.introduced_gaps.get(ball, ()))

    def coverage(self, frame_count: int) -> float:
        """Share of all ball-frames that some trajectory covers, 0–1.

        The headline quality number of a recording: DESIGN.md §7 quotes 98.5% for
        the 5-ball clip and it is what makes linking a well-posed problem.
        """
        balls = len(self.introduced_gaps)
        total = balls * frame_count
        if total == 0:
            return 1.0
        return 1.0 - sum(self.missing_frames(b) for b in self.introduced_gaps) / total


# --------------------------------------------------------------------------- #
# degradation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, eq=False)
class _Measured:
    """Per-ball noisy positions and the σ that will be *reported* for them."""

    #: ``(n_balls, n_frames, 3)`` m.
    positions: np.ndarray
    #: ``(n_balls, n_frames)`` m — the optimistic, stored σ.
    reported_sigma: np.ndarray


@dataclass(frozen=True)
class _Dropout:
    """Missing frames on one lane, 1-based inclusive."""

    first: int
    last: int
    #: True when the loss leaves a hole *inside* one trajectory (QTM *Mixed*);
    #: False when it breaks the lane into two trajectories.
    internal: bool


@dataclass(frozen=True, eq=False)
class _Segment:
    """One emitted trajectory before it is given an id."""

    #: ``(m,)`` absolute 1-based frames, strictly increasing.
    frames: np.ndarray
    #: ``(m,)`` which physical ball each sample came from, ``-1`` for a reflection.
    source: np.ndarray
    #: ``(m, 3)`` m.
    positions: np.ndarray
    #: ``(m,)`` m, reported.
    sigma: np.ndarray
    spurious: bool


def degrade(
    truth: Truth,
    rng: np.random.Generator,
    params: DegradationParams = CLEAN_PRESET,
) -> tuple[Session, DegradationTruth]:
    """Degrade clean truth into a realistic :class:`Session` plus its answer key.

    ``rng`` is supplied by the caller and never created here (CLAUDE.md rule 1),
    so the same seed and params give byte-identical output. Returns the session —
    positions in metres in the juggling frame, absolute 1-based frames, per-sample
    reported σ — and the :class:`DegradationTruth` that labels it.

    Emitted trajectories carry ``kind="unknown"``: this stands in for the reader,
    and classifying them is `core.clean`'s job, not the degrader's.
    """
    n_balls, n_frames = truth.ball_count, truth.frame_count
    measured = _measure(truth, rng, params)
    crossings = crossing_events(truth.positions, params.crossing_distance)
    source = _apply_swaps(n_balls, n_frames, crossings, rng, params)
    dropouts = _draw_dropouts(truth, crossings, source, rng, params)
    segments = _cut_lanes(measured, source, dropouts, params)
    segments += _reflections(truth, measured, rng, params)

    trajectories: list[Trajectory] = []
    ball_of: dict[str, int] = {}
    spurious_ids: set[str] = set()
    swapped_ids: set[str] = set()
    covered = np.zeros((n_balls, n_frames), dtype=bool)
    width = max(3, len(str(len(segments))))

    # Sorted by first frame so the id scheme carries no information about ball
    # identity — a linker must not be able to cheat by parsing the id.
    order = sorted(
        range(len(segments)),
        key=lambda i: (
            int(segments[i].frames[0]),
            int(segments[i].frames[-1]),
            segments[i].spurious,
            i,
        ),
    )
    for position, index in enumerate(order, start=1):
        segment = segments[index]
        identifier = f"t{position:0{width}d}"
        trajectories.append(
            Trajectory(
                id=identifier,
                frames=segment.frames,
                positions=segment.positions,
                uncertainty=Uncertainty.isotropic(segment.sigma),
                sample_type=np.ones(segment.frames.size, dtype=np.uint8),
                pieces=_pieces(segment.frames),
                kind="unknown",
            )
        )
        if segment.spurious:
            spurious_ids.add(identifier)
            ball_of[identifier] = -1
            continue
        sources = np.bincount(segment.source, minlength=n_balls)
        ball_of[identifier] = int(np.argmax(sources))
        if int(np.count_nonzero(sources)) > 1:
            swapped_ids.add(identifier)
        covered[segment.source, segment.frames - 1] = True

    session = Session(
        source=f"synth:{truth.pattern}",
        f_s=truth.f_s,
        frame_count=n_frames,
        trajectories=tuple(trajectories),
        frame="juggling",
    )
    gaps = {
        ball: tuple((int(start) + 1, int(end) + 1) for start, end in _true_runs(~covered[ball]))
        for ball in range(n_balls)
    }
    key = DegradationTruth(
        ball_of_trajectory=MappingProxyType(ball_of),
        spurious_ids=frozenset(spurious_ids),
        swapped_ids=frozenset(swapped_ids),
        introduced_gaps=MappingProxyType(gaps),
    )
    return session, key


# -- 1. position error ------------------------------------------------------ #


def _measure(truth: Truth, rng: np.random.Generator, params: DegradationParams) -> _Measured:
    """Add position error to every ball and draw the σ that will be reported.

    Two levels of reported σ, because real data has two: a base σ per trajectory
    (how well this marker is solved overall) and a per-sample σ around it (how
    many cameras saw it at that instant).

    The *injected* error is `sigma_report_factor` × the trajectory's base σ,
    modulated by the per-sample σ only as far as
    :attr:`DegradationParams.error_sigma_coupling` allows, and split between a
    white and a slowly-varying component by
    :attr:`DegradationParams.white_error_fraction`. Both of those splits are
    measured properties of the corpus, not conveniences — see the module docstring.
    """
    n_balls, n_frames = truth.ball_count, truth.frame_count
    positions = np.empty((n_balls, n_frames, _AXES))
    reported = np.empty((n_balls, n_frames))
    window = _smoothing_window(params.smooth_error_seconds, truth.f_s, n_frames)
    white_share = params.white_error_fraction
    smooth_share = math.sqrt(max(0.0, 1.0 - white_share**2))

    for ball in range(n_balls):
        base = params.sigma_base_median * math.exp(
            float(rng.normal(0.0, params.sigma_base_log_spread))
        )
        sigma = base * np.exp(rng.normal(0.0, params.sigma_sample_log_spread, n_frames))
        sigma = np.clip(sigma, params.sigma_floor, params.sigma_ceiling)
        # The injected error tracks the trajectory's base σ, and the per-sample
        # reported σ only as far as the measured coupling allows.
        true_sigma = (
            params.sigma_report_factor * base * (sigma / base) ** params.error_sigma_coupling
        )

        error = rng.normal(0.0, 1.0, (n_frames, _AXES)) * (white_share * true_sigma)[:, None]
        if smooth_share > 0.0:
            error += _smooth_unit(rng, n_frames, window) * (smooth_share * true_sigma)[:, None]
        # Airtime's `y` is identically zero, so the model has to supply the whole
        # forward extent itself or `core.frame`'s second axis is degenerate.
        error[:, 1] += float(rng.normal(0.0, params.forward_spread))

        positions[ball] = truth.positions[ball] + error
        reported[ball] = sigma
    return _Measured(positions=positions, reported_sigma=reported)


def _smoothing_window(seconds: float, f_s: float, n_frames: int) -> int:
    """Boxcar width in samples for the smooth error component, ≥ 1."""
    return max(1, min(round(seconds * f_s), max(1, n_frames // 4)))


def _smooth_unit(rng: np.random.Generator, n: int, window: int) -> np.ndarray:
    """``(n, 3)`` unit-variance noise correlated over ``window`` samples.

    A boxcar-averaged white sequence, padded so the ends are not attenuated, then
    normalised by its realised standard deviation — normalising by the realised
    value rather than the analytic `1/√window` is what keeps the injected error's
    magnitude under tight control, which the calibration depends on.
    """
    if window <= 1:
        return np.asarray(rng.normal(0.0, 1.0, (n, _AXES)))
    pad = 3 * window
    raw = rng.normal(0.0, 1.0, (n + 2 * pad, _AXES))
    cumulative = np.concatenate([np.zeros((1, _AXES)), np.cumsum(raw, axis=0)])
    smoothed = (cumulative[window:] - cumulative[:-window]) / window
    smoothed = smoothed[pad : pad + n]
    deviation = smoothed.std(axis=0, keepdims=True)
    return np.asarray(smoothed / np.where(deviation > 0.0, deviation, 1.0))


# -- 2. identity swaps ------------------------------------------------------ #


def _apply_swaps(
    n_balls: int,
    n_frames: int,
    crossings: tuple[Crossing, ...],
    rng: np.random.Generator,
    params: DegradationParams,
) -> np.ndarray:
    """``(n_lanes, n_frames)`` map from tracker lane to the ball it is following.

    A *lane* is what the tracker follows: continuous in space, not necessarily in
    identity. When two balls pass within `swap_distance` the tracker may resume on
    the wrong one, so the two lanes exchange **all their remaining samples** — the
    mistake persists, which is exactly why it is hard for the linker (§7) and why
    a cosmetic one-sample glitch would not be a fair test.
    """
    source = np.tile(np.arange(n_balls)[:, None], (1, n_frames))
    if params.swap_probability <= 0.0:
        return source
    for crossing in crossings:
        if crossing.distance > params.swap_distance:
            continue
        if float(rng.random()) >= params.swap_probability:
            continue
        index = crossing.frame - 1
        lane_a = int(np.flatnonzero(source[:, index] == crossing.ball_a)[0])
        lane_b = int(np.flatnonzero(source[:, index] == crossing.ball_b)[0])
        if lane_a == lane_b:  # pragma: no cover - a column is always a permutation
            continue
        tail = source[lane_a, index:].copy()
        source[lane_a, index:] = source[lane_b, index:]
        source[lane_b, index:] = tail
    return source


# -- 3. occlusion dropouts -------------------------------------------------- #


def _draw_dropouts(
    truth: Truth,
    crossings: tuple[Crossing, ...],
    source: np.ndarray,
    rng: np.random.Generator,
    params: DegradationParams,
) -> list[tuple[_Dropout, ...]]:
    """Per-lane missing-frame intervals, merged and in frame order.

    Three sources, in a fixed order so the RNG stream is reproducible:

    * **apexes** — the dominant failure mode. `v_z ≈ 0` there, so the ball lingers
      near one point at the top of the volume where camera coverage is thinnest.
    * **crossings** — two markers merge; each of the two balls is lost
      independently, which is why both can go at once.
    * **background** — everything else, as a Poisson rate per ball per second.

    All three are *elevated probabilities*, never certainties: real tracking
    survives most apexes and most crossings (DESIGN.md §12).
    """
    n_lanes, n_frames = source.shape
    f_s = truth.f_s
    starts: list[list[int]] = [[] for _ in range(n_lanes)]

    jitter = max(1, round(params.apex_jitter_seconds * f_s))
    for apex in sorted(truth.apexes(), key=lambda e: (e.frame, e.ball)):
        if float(rng.random()) >= params.apex_dropout_probability:
            continue
        index = min(max(apex.frame - 1, 0), n_frames - 1)
        lane = int(np.flatnonzero(source[:, index] == apex.ball)[0])
        starts[lane].append(apex.frame + int(rng.integers(-jitter, jitter + 1)))

    for crossing in crossings:
        index = crossing.frame - 1
        for ball in (crossing.ball_a, crossing.ball_b):
            if float(rng.random()) >= params.crossing_dropout_probability:
                continue
            lane = int(np.flatnonzero(source[:, index] == ball)[0])
            starts[lane].append(crossing.frame)

    if params.background_dropout_rate > 0.0:
        expected = params.background_dropout_rate * truth.duration
        for lane in range(n_lanes):
            for _ in range(int(rng.poisson(expected))):
                starts[lane].append(1 + int(rng.integers(0, n_frames)))

    return [_lane_dropouts(starts[lane], n_frames, f_s, rng, params) for lane in range(n_lanes)]


def _lane_dropouts(
    centres: list[int],
    n_frames: int,
    f_s: float,
    rng: np.random.Generator,
    params: DegradationParams,
) -> tuple[_Dropout, ...]:
    """Turn dropout centres into merged, clipped, classified intervals."""
    drawn: list[_Dropout] = []
    for centre in sorted(centres):
        internal = float(rng.random()) < params.internal_gap_fraction
        median = params.internal_gap_median_seconds if internal else params.gap_median_seconds
        spread = params.internal_gap_log_spread if internal else params.gap_log_spread
        length = max(1, round(median * math.exp(float(rng.normal(0.0, spread))) * f_s))
        first = centre - length // 2
        drawn.append(
            _Dropout(
                first=max(1, first),
                last=min(n_frames, first + length - 1),
                internal=internal,
            )
        )
    return _merge_dropouts(drawn)


def _merge_dropouts(dropouts: list[_Dropout]) -> tuple[_Dropout, ...]:
    """Union overlapping or abutting intervals; a break beats an internal hole.

    Two losses that touch are one loss, and if either broke the lane the union
    breaks it — the tracker cannot have carried identity across a span it also
    failed to bridge.
    """
    merged: list[_Dropout] = []
    for dropout in sorted(dropouts, key=lambda d: (d.first, d.last)):
        if dropout.last < dropout.first:
            continue
        if merged and dropout.first <= merged[-1].last + 1:
            previous = merged[-1]
            merged[-1] = _Dropout(
                first=previous.first,
                last=max(previous.last, dropout.last),
                internal=previous.internal and dropout.internal,
            )
        else:
            merged.append(dropout)
    return tuple(merged)


# -- 4. fragmentation ------------------------------------------------------- #


def _cut_lanes(
    measured: _Measured,
    source: np.ndarray,
    dropouts: list[tuple[_Dropout, ...]],
    params: DegradationParams,
) -> list[_Segment]:
    """Apply the dropouts, splitting each lane into the trajectories it becomes."""
    n_lanes, n_frames = source.shape
    segments: list[_Segment] = []
    for lane in range(n_lanes):
        present = np.ones(n_frames, dtype=bool)
        piece_of = np.zeros(n_frames, dtype=np.int64)
        for dropout in dropouts[lane]:
            present[dropout.first - 1 : dropout.last] = False
            if not dropout.internal:
                # `last` is 1-based, so index `last` is the first surviving frame.
                piece_of[dropout.last :] += 1
        for part in range(int(piece_of.max()) + 1):
            index = np.flatnonzero(present & (piece_of == part))
            if index.size < params.min_trajectory_samples:
                continue
            ball = source[lane, index]
            segments.append(
                _Segment(
                    frames=index + 1,
                    source=ball,
                    positions=measured.positions[ball, index],
                    sigma=measured.reported_sigma[ball, index],
                    spurious=False,
                )
            )
    return segments


def _pieces(frames: np.ndarray) -> tuple[Piece, ...]:
    """One :class:`Piece` per contiguous run of frames, all *measured* (type 1).

    Gap-filled samples (QTM type 2) are not modelled: this model removes frames
    rather than inventing them, so every emitted sample really was measured.
    """
    breaks = np.flatnonzero(np.diff(frames) != 1)
    starts = np.concatenate([[0], breaks + 1])
    ends = np.concatenate([breaks, [frames.size - 1]])
    return tuple(
        Piece(int(frames[start]), int(frames[end]), 1)
        for start, end in zip(starts, ends, strict=True)
    )


# -- 5. spurious reflections ------------------------------------------------ #


def _reflections(
    truth: Truth,
    measured: _Measured,
    rng: np.random.Generator,
    params: DegradationParams,
) -> list[_Segment]:
    """Short, non-ballistic trajectories near but not on the real balls.

    A reflection is a random walk, not an arc: its implied acceleration is of
    order `spurious_step · f_s²`, three orders of magnitude from `g`, so no fit
    can mistake it for free flight. That is what DESIGN.md §12 asks for — a
    plausible-looking distractor for the linker that physics rejects outright.
    """
    if params.spurious_rate <= 0.0:
        return []
    n_frames = truth.frame_count
    count = int(rng.poisson(params.spurious_rate * truth.duration))
    segments: list[_Segment] = []
    for _ in range(count):
        length = int(rng.integers(params.spurious_min_samples, params.spurious_max_samples + 1))
        length = min(length, n_frames)
        first = 1 + int(rng.integers(0, max(1, n_frames - length + 1)))
        anchor = int(rng.integers(0, truth.ball_count))
        direction = rng.normal(0.0, 1.0, _AXES)
        norm = float(np.linalg.norm(direction))
        direction = direction / norm if norm > 0.0 else np.array([1.0, 0.0, 0.0])
        offset = float(rng.uniform(params.spurious_offset_min, params.spurious_offset_max))
        origin = measured.positions[anchor, first - 1] + direction * offset
        walk = np.cumsum(rng.normal(0.0, params.spurious_step, (length, _AXES)), axis=0)

        base = params.spurious_sigma_factor * params.sigma_base_median
        sigma = np.clip(
            base * np.exp(rng.normal(0.0, params.sigma_sample_log_spread, length)),
            params.sigma_floor,
            params.sigma_ceiling,
        )
        segments.append(
            _Segment(
                frames=np.arange(first, first + length, dtype=np.int64),
                source=np.full(length, -1, dtype=np.int64),
                positions=origin + walk,
                sigma=sigma,
                spurious=True,
            )
        )
    return segments
