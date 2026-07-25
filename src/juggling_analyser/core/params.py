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

#: Lower bound on the per-sample 1σ position uncertainty, m.
#:
#: QTM reports a per-sample *residual* — the RMS ray-intersection error of the
#: camera rays that reconstructed the marker — which this project uses as an
#: isotropic 1σ proxy (DESIGN.md §3). It is a proxy, not a calibrated σ: the
#: residual can read as low as a few micrometres, which would give a sample
#: implausibly high weight in a weighted fit. Clamping at 0.1 mm keeps the
#: weighting honest about what a 4-camera mocap volume can actually resolve.
RESIDUAL_SIGMA_FLOOR = 1e-4
