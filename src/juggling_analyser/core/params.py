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

#: Lower bound on the per-sample 1σ position uncertainty, m.
#:
#: QTM reports a per-sample *residual* — the RMS ray-intersection error of the
#: camera rays that reconstructed the marker — which this project uses as an
#: isotropic 1σ proxy (DESIGN.md §3). It is a proxy, not a calibrated σ: the
#: residual can read as low as a few micrometres, which would give a sample
#: implausibly high weight in a weighted fit. Clamping at 0.1 mm keeps the
#: weighting honest about what a 4-camera mocap volume can actually resolve.
RESIDUAL_SIGMA_FLOOR = 1e-4
