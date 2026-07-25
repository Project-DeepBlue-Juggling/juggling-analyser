"""Physical constants and analysis defaults, in one place (DESIGN.md §13).

Every default the analysis depends on lives here so that the value used by a run
can be recorded verbatim in the session JSON (DESIGN.md §10) and so that tuning
is a visible, reviewable change rather than a number buried in an algorithm.

Units are metres, seconds, kilograms, radians (NOTATION.md § Conventions).
"""

from __future__ import annotations

#: Gravitational acceleration `g`. Fixed, not a knob: it is a measured constant
#: and the fitted value is one of the project's strongest self-checks
#: (NOTATION.md § Deltas from Airtime).
GRAVITY = 9.80665

#: Ball mass `m`, kg. The owner's balls are 71 g.
BALL_MASS = 0.071

#: Ball diameter `d`, m. Used for the non-collision constraint (DESIGN.md §7)
#: and for rendering at true scale (DESIGN.md §11).
BALL_DIAMETER = 0.074

#: Number of hands `n_h`. 2 for a human, 1 for Jugglebot.
HAND_COUNT = 2

#: Ballistic tolerance `a_tol`, m/s²: how far the smoothed acceleration may sit
#: from `(0, 0, −g)` and still count as free flight (DESIGN.md §13). Carries in
#: the sample recordings run at 20–45 m/s², so rejection has an enormous margin;
#: this value is really set by how much smoothing noise must be tolerated.
BALLISTIC_TOLERANCE = 1.5

#: Multiple of the *propagated* acceleration noise below which the tolerance is
#: widened automatically. A trajectory with a poor solve has a larger σ, so its
#: acceleration estimate is noisier, and a fixed tolerance would reject genuine
#: flight samples. This is the "weighted by uncertainty" part of DESIGN.md §6.
BALLISTIC_SIGMA_MULTIPLE = 3.0

#: Shortest span that counts as a flight, s (DESIGN.md §13). Shorter is noise.
MIN_FLIGHT_DURATION = 0.100

#: Savitzky–Golay window, s. 0.070 s is 21 samples at 300 Hz — DESIGN.md §13's
#: default expressed as a duration so it adapts to `f_s`.
SAVGOL_WINDOW_SECONDS = 0.070

#: Savitzky–Golay polynomial order. 2 is exactly right for ballistic motion: the
#: second derivative of a quadratic fit *is* the acceleration, with no bias.
SAVGOL_POLYORDER = 2

#: Longest run of non-ballistic samples to bridge when forming a flight, s.
#:
#: Differentiating twice amplifies noise, so a genuine flight picks up isolated
#: samples that fail the tolerance and would split one arc into several. A real
#: carry lasts `t_d` ≈ 0.25–0.35 s, so closing gaps an order of magnitude shorter
#: than that cannot merge two flights across a carry. Measured on the corpus:
#: without this, the 5-ball clip's ballistic fraction reads 0.52 against a
#: physically expected ~0.72.
BALLISTIC_CLOSE_SECONDS = 0.033

#: Position residual, m, within which a sample may be added to a flight during
#: boundary refinement — used as a floor under the `3σ` test.
#:
#: Savitzky–Golay locates a flight but blurs its ends by half a window; the
#: fitted parabola then locates the release and the catch to single-sample
#: precision. Just outside a flight the hand is accelerating the ball at
#: 20–45 m/s², so the deviation from the parabola grows as ~½·Δa·t² — about
#: 0.2 mm after one sample at 300 Hz and 2 mm after three. A 1.5 mm floor
#: therefore admits at most two or three contaminated samples.
BOUNDARY_TOLERANCE = 1.5e-3

#: Free-gravity parabola residual, m, above which a flight is marked *suspect*
#: rather than accepted (DESIGN.md §6).
#:
#: Deliberately an order of magnitude above the measurement floor. Measured on the
#: corpus, a clean flight's free-gravity residual sits at ~1.2 mm — itself about
#: 3× QTM's reported per-sample residual, so QTM's residual understates the true
#: position error. Segments that trip 5 mm are qualitatively different: on
#: `3_ball_juggling_cut` the two that do fit `g ≈ −0.6` and `g ≈ 0.4 m/s²`, i.e.
#: near-straight paths that are not one ball in free flight at all.
MAX_FLIGHT_RESIDUAL = 5e-3

#: Fractional deviation of a flight's *free-fit* gravity from the value the
#: session was fitted with, beyond which the segment is marked suspect.
#:
#: The residual test alone is not enough, and the corpus shows exactly why: a
#: near-straight segment on `3_ball_juggling_cut` fits a free quadratic with
#: `g ≈ −0.58 m/s²` to a residual of 3 mm, because a quadratic fits a straight
#: line perfectly. A low residual therefore proves the path is *smooth*, not that
#: it is *falling*. Measured per-flight gravity scatters by ~1.7% (1σ) and stays
#: inside ±4% at the 5th/95th percentiles, so 20% flags only segments that are not
#: free flight at all.
MAX_GRAVITY_DEVIATION = 0.20

# --------------------------------------------------------------------------- #
# Identity linking (DESIGN.md §7)
# --------------------------------------------------------------------------- #

#: Longest gap a bridge is considered *confident* over, s (DESIGN.md §13).
MAX_BRIDGED_GAP = 0.250

#: Longest gap the linker will bridge at all, s.
#:
#: DESIGN.md §13 gives one figure, 250 ms. Measured on `5_ball_juggling_cut`, the
#: five gaps that must be crossed to recover 5 balls are 413, 293, 280, 157 and
#: 150 ms — so a hard 250 ms ceiling makes the clip's own acceptance criterion
#: (5 lanes, ≤ 400 frames of gap) unreachable, and 383 frames of real gap would be
#: reported as five extra balls.
#:
#: So 250 ms becomes the *confidence* boundary rather than the feasibility one:
#: a longer bridge is still allowed when the combinatorics demand it — a lane
#: cannot simply be abandoned, since the minimum number of lanes is the ball count
#: — but it is charged more and `BridgedGap.confident` is False, so a metric
#: computed across it can be flagged. 600 ms is roughly two dwell times, beyond
#: which "the same ball" is not an inference any evidence here supports.
MAX_LINK_GAP = 0.600

#: Effective inflation of the reported per-sample uncertainty, for link scoring.
#:
#: **Not** "the sensor is 3x noisier than reported" — an earlier version of this comment
#: said that and it is wrong. Measured on a 26-marker static recording, the true
#: position noise of a motionless marker is 0.028 mm while QTM reports 2.333 mm for the
#: same samples: the reported residual is a ray-intersection residual that tracks camera
#: geometry, and it *overstates* position error by up to 80x. It is not a calibrated
#: sigma in either direction (BUILD_LOG, "Calibration recording").
#:
#: What this factor is really for: the linker's cost is a chi-squared on how far a ball's
#: real path lands from a ballistic prediction, and that distance is dominated by genuine
#: departure from the ballistic model — measured at 0.42-0.78 mm of parabola residual
#: against 0.03 mm of sensor noise — not by the reported residual. So this is an
#: **effective model-error** term, and 3.0 is a weakly-founded value: it was chosen
#: because it lets a known-correct link across the 5-ball clip's 417 ms gap pass a 3-sigma
#: gate that it otherwise fails at chi-squared 11.4. Replace it with a properly propagated
#: prediction covariance from the flight fit (see BUILD_LOG Phase 4).
#:
#: Note a probable double-count: `_endpoint_states` already uses `flight.free_residual`,
#: itself a measured path-deviation figure, and then applies this on top.
SIGMA_UNDERESTIMATE_FACTOR = 3.0

#: Chi-squared above which a candidate link is rejected outright.
#:
#: The link cost is a per-degree-of-freedom chi-squared over the position and
#: velocity mismatch, so 9 is a 3σ gate on each. Generous on purpose: the cost
#: still *ranks* candidates, and the global assignment is far better placed than
#: this threshold to resolve a close call. Its job is only to keep impossible
#: links out of the matrix.
LINK_CHI2_LIMIT = 9.0

#: Samples used to fit a trajectory's endpoint state when it is not inside a
#: detected flight. 20 samples is 67 ms at 300 Hz — long enough to constrain a
#: velocity, short enough not to average across a catch.
LINK_ENDPOINT_SAMPLES = 20

#: How many samples past a flight's refined boundary a trajectory endpoint may sit
#: and still be treated as ballistic.
#:
#: Boundary refinement trims a flight *inward* from the last tracked sample, so
#: when tracking dies mid-flight the trajectory's final samples are exactly the
#: contaminated ones the refinement dropped — and without slack here no endpoint
#: is ever ballistic and half the link cost model never runs. 12 samples is 40 ms
#: at 300 Hz, about the boundary blur the refinement removes.
LINK_FLIGHT_MARGIN = 12

#: Fastest a hand is assumed to move, m/s, when testing whether a gap could have
#: been spent in a hand rather than in flight. A juggling hand peaks at roughly
#: 2–3 m/s during a throw; 3.0 is deliberately permissive, because the purpose is
#: to *admit* the carry hypothesis for scoring, not to decide anything on its own.
CARRY_MAX_HAND_SPEED = 3.0

#: Cost of leaving a trajectory without a successor, i.e. of starting a new lane.
#:
#: Large on purpose, so the solver always prefers any *feasible* link and the
#: result is a minimum path cover: the lane count then equals the maximum number of
#: simultaneously-active trajectories, which is DESIGN.md §7's own ball-count
#: estimate. The real link costs only break ties among the covers of that size.
#:
#: **A finite value was tried and is worse.** Setting it to 24 — just below the
#: worst link the gates admit (chi-squared 9 + carry penalty 10 + stretch 9) so
#: that the most marginal bridge loses to declaring a new lane — was intended to
#: stop the linker inventing an identity it cannot support. Measured: it did *not*
#: improve any synthetic case (5-ball truth stayed at 0.615 correct) and it broke
#: the one criterion that was passing, taking the real 5-ball clip from 5 lanes to
#: 8. So the trade-off is not where the problem is; see BUILD_LOG.md Phase 4 for
#: what the problem appears to be. Kept as a parameter because the experiment is
#: worth repeating once the link cost itself discriminates better.
LANE_END_COST = 1e6

#: Furthest a ball may move across a bridged gap, m, however long the gap is.
#:
#: `hand_speed × dt` alone is the wrong bound and it caused real linking errors: over
#: a 550 ms gap it permits 1.65 m of travel, which admits almost any pair of
#: trajectories. But the cap must comfortably exceed the 0.4 m hand separation,
#: because a gap long enough to contain a catch and a throw also contains part of a
#: flight — the 5-ball clip's 417 ms bridge covers 722 mm, and it is correct.
#:
#: Swept against both the truth fixtures and the corpus: 0.5 m splits the real
#: 5-ball clip into 6 lanes, while 0.8, 1.0 and 1.5 m all give exactly 5 lanes with
#: identical synthetic scores. 1.0 sits in the middle of that plateau rather than at
#: its edge. The effective bound is `min(hand_speed × dt, CARRY_MAX_TRAVEL)`.
CARRY_MAX_TRAVEL = 1.0

#: Cost added to a carry-hypothesis link so that, all else equal, a ballistic
#: bridge wins. A carry bridge asserts much less — position continuity only, with
#: velocity unconstrained — so it should never beat a ballistic bridge that fits.
CARRY_BRIDGE_PENALTY = 10.0

#: Lower bound on the per-sample 1σ position uncertainty, m.
#:
#: QTM reports a per-sample *residual* — the RMS ray-intersection error of the
#: camera rays that reconstructed the marker — which this project uses as an
#: isotropic 1σ proxy (DESIGN.md §3). It is a proxy, not a calibrated σ: the
#: residual can read as low as a few micrometres, which would give a sample
#: implausibly high weight in a weighted fit. Clamping at 0.1 mm keeps the
#: weighting honest about what a 4-camera mocap volume can actually resolve.
RESIDUAL_SIGMA_FLOOR = 1e-4
