"""The juggling frame: derived from the data, recorded, invertible (DESIGN.md §5).

Every analysis and every output is expressed in the juggling frame — **X = left
hand → right hand, Y = forward, Z = up**, right-handed (NOTATION.md § Frames of
reference). It is *derived per recording* rather than assumed, so that a session
captured at a different yaw relative to the cameras produces the same numbers:

* the **origin** is the mean of the detected throw and catch positions, the point
  between the hands. The QTM calibration origin and the floor are discarded —
  neither is reliably identifiable in this data and no v1 metric needs them;
* the **hand axis** is the principal axis of that cloud in the horizontal plane;
* the **nominal mapping** ``x_J = −y_Q, y_J = x_Q, z_J = z_Q`` contributes only
  the *sign* — which end of the axis is the right hand.

The transform is a value, not a mode: it is returned, recorded, and applied
explicitly, and :func:`to_juggling_frame` refuses a session that is already in
the juggling frame. Applying it twice would silently corrupt every position, and
"which frame is this in" must never be a guess (``Session.frame`` answers it).

Because the axis is *measured*, it can be badly determined: a cloud that is only
mildly elongated has no meaningful principal axis. :class:`FrameDiagnostics`
travels on the transform so that a caller sees the quality instead of receiving a
confident-looking wrong answer (CLAUDE.md rule 3), and :func:`derive_frame`
refuses outright below a documented anisotropy bound.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np

from .trajectory import Session

_AXES = 3
_HORIZONTAL_AXES = 2

#: The nominal hand axis `+x_J`, expressed in QTM coordinates.
#:
#: NOTATION.md § Frames of reference: the QTM frame is X forward, Y **left**,
#: Z up; the juggling frame is X = left hand → right hand. The vector from the
#: left hand to the right hand points *rightwards*, and rightwards is the
#: opposite of QTM's `+y`, hence `x_J = −y_Q` and `(0, −1, 0)` here. Only the
#: sign of the derived hand axis comes from this (DESIGN.md §5 step 5); its
#: direction is measured per recording.
NOMINAL_HAND_AXIS_QTM = np.array([0.0, -1.0, 0.0])
NOMINAL_HAND_AXIS_QTM.setflags(write=False)

#: Up, in QTM coordinates. `z_J = z_Q` exactly — Z is up in both frames
#: (NOTATION.md § Deltas), which is why the hand-axis PCA is a 2-D problem.
UP_AXIS_QTM = np.array([0.0, 0.0, 1.0])
UP_AXIS_QTM.setflags(write=False)

#: How far ``R Rᵀ`` and ``det R`` may stray from the identity and from `+1`.
#:
#: A rotation built from a normalised 2-D eigenvector is orthonormal to a few
#: ulp, so 1e-12 is loose by four orders of magnitude for anything legitimate
#: and still rejects a matrix that merely looks like a rotation. It is also the
#: tolerance Phase 2 asks the round trip to hold to (PLAN.md P2).
ORTHONORMAL_TOL = 1e-12

#: Minimum `λ₁/λ₂` of the horizontal PCA for the hand axis to mean anything.
#:
#: `λ₁`, `λ₂` are the variances (m²) of the throw/catch cloud along its major
#: and minor horizontal axes. For a 2-D cloud of `n` points the standard error
#: of the fitted principal-axis angle is, to first order,
#: ``σ_θ ≈ sqrt(λ₁ λ₂ / (n (λ₁ − λ₂)²))`` — i.e. ``sqrt(r)/((r − 1)·sqrt(n))``
#: for ``r = λ₁/λ₂``. At `r = 2` with 100 points that is 0.14 rad ≈ 8°, inside
#: Phase 2's 15° acceptance band; by `r = 1.5` it is already ≈ 14°, and as
#: `r → 1` the axis is pure noise. So 2.0 is where "the principal axis" stops
#: describing anything and :func:`derive_frame` refuses instead.
#:
#: Real juggling is nowhere near the bound: hands 0.4 m apart put `λ₁ ≈ 0.04 m²`
#: against a forward scatter of a few centimetres, so `r ≈ 25`.
MIN_ANISOTROPY = 2.0

#: Minimum σ of the cloud along its major horizontal axis, m.
#:
#: The anisotropy bound above is a *ratio*, so it says nothing about scale: a
#: cloud collapsed onto a single (x, y) still leaves round-off in the covariance
#: (order 1e-33 m²), and the principal axis of round-off points anywhere at all.
#: This floor catches that. 1 mm is 10× the per-sample uncertainty floor
#: (``params.RESIDUAL_SIGMA_FLOOR``, 0.1 mm) and some 200× below the hand
#: separation it exists to admit, so it only ever rejects a cloud that is a
#: point. Unlike :data:`MIN_ANISOTROPY` it is not a tuning knob but a
#: numerical-degeneracy guard, so it is not exposed as an argument.
MIN_HORIZONTAL_SPREAD = 1e-3


def _as_points(values: np.ndarray, what: str) -> np.ndarray:
    """Coerce to ``(3,)`` or ``(N, 3)`` float64, or raise ``ValueError``."""
    array = np.asarray(values, dtype=np.float64)
    if array.ndim not in (1, 2) or array.shape[-1] != _AXES:
        raise ValueError(f"{what} must be (3,) or (N, 3), got shape {array.shape}")
    return array


def _frozen(values: np.ndarray) -> np.ndarray:
    """A read-only float64 **copy** of ``values``.

    A copy, not a view: making the caller's array read-only in place would be a
    surprising side effect. Read-only, so that ``frozen=True`` extends to the
    payload — a transform is recorded and reused everywhere downstream, and a
    stray in-place write to a row of ``rotation`` would corrupt every position
    silently, which is exactly what CLAUDE.md rule 3 forbids.
    """
    array = np.array(values, dtype=np.float64)
    array.setflags(write=False)
    return array


@dataclass(frozen=True, eq=False)
class FrameDiagnostics:
    """How well the cloud determined the hand axis — the honesty of the frame.

    A cloud elongated 25:1 pins the axis to a fraction of a degree; a cloud
    elongated 2:1 barely pins it at all. Both yield a perfectly valid-looking
    :class:`FrameTransform`, so the quality has to be reported as data rather
    than left for the caller to re-derive (CLAUDE.md rule 3, DESIGN.md §5).

    Carried *on* the transform so it cannot be separated from the thing it
    describes, and so the session output (DESIGN.md §10) records it alongside
    the frame it qualifies.

    ``eq=False`` because the payload is a numpy array: a generated ``__eq__``
    would return an array and silently poison any ``==`` comparison.
    """

    #: Number of throw/catch positions the frame was derived from.
    n_points: int
    #: ``(2,)`` horizontal PCA eigenvalues in m², **descending**: the sample
    #: variance (ddof = 1) of the cloud along the derived hand axis and along
    #: the derived forward axis.
    eigenvalues: np.ndarray

    def __post_init__(self) -> None:
        eigenvalues = _frozen(self.eigenvalues)
        object.__setattr__(self, "eigenvalues", eigenvalues)
        if eigenvalues.shape != (_HORIZONTAL_AXES,):
            raise ValueError(f"horizontal PCA has 2 eigenvalues, got shape {eigenvalues.shape}")
        if not np.all(np.isfinite(eigenvalues)):
            raise ValueError("eigenvalues contain non-finite values")
        if eigenvalues[1] < 0.0:
            raise ValueError(f"variances must be non-negative, got {eigenvalues.tolist()}")
        if eigenvalues[0] < eigenvalues[1]:
            raise ValueError(f"eigenvalues must be descending, got {eigenvalues.tolist()}")
        if self.n_points < _HORIZONTAL_AXES:
            raise ValueError(f"a hand axis needs at least 2 points, got {self.n_points}")

    @property
    def anisotropy(self) -> float:
        """`λ₁/λ₂`, dimensionless and ≥ 1. ``inf`` for a collinear cloud.

        1 means a circular cloud with no principal axis; large means a
        well-determined one. Compare against :data:`MIN_ANISOTROPY`, whose
        docstring derives what the number is worth.
        """
        major, minor = float(self.eigenvalues[0]), float(self.eigenvalues[1])
        return float("inf") if minor == 0.0 else major / minor

    @property
    def axis_angle_sigma(self) -> float:
        """1σ uncertainty of the hand-axis *direction*, in radians.

        The first-order result for a 2-D principal axis,
        ``sqrt(λ₁ λ₂ / (n (λ₁ − λ₂)²))``: ``0.0`` for a perfectly collinear
        cloud, ``inf`` for an isotropic one (no axis exists). Gaussian and
        asymptotic — a quality signal to report, not a calibrated error bar,
        and it says nothing about whether the *catches themselves* were where
        the juggler meant them to be.
        """
        major, minor = float(self.eigenvalues[0]), float(self.eigenvalues[1])
        if minor == 0.0:
            return 0.0
        if major == minor:
            return float("inf")
        return math.sqrt(major * minor / (self.n_points * (major - minor) ** 2))

    def __str__(self) -> str:
        return (
            f"n={self.n_points} λ=({self.eigenvalues[0]:.4g}, {self.eigenvalues[1]:.4g}) m² "
            f"anisotropy={self.anisotropy:.3g} axis σ={math.degrees(self.axis_angle_sigma):.2f}°"
        )


@dataclass(frozen=True, eq=False)
class FrameTransform:
    """The recorded, invertible QTM → juggling-frame transform (DESIGN.md §5).

    ``apply`` is ``R (p − origin)`` and ``invert`` is ``Rᵀ q + origin``; the two
    are exact inverses to within float round-off, which is what lets any result
    be mapped back to raw QTM coordinates (DESIGN.md §5 step 6, §10).

    The rows of ``rotation`` are the juggling-frame basis vectors written in QTM
    coordinates, so ``rotation @ v`` reads a QTM vector's components along the
    hand, forward and up axes. Because the rows are orthonormal and
    right-handed, ``R⁻¹ = Rᵀ`` and no matrix inverse is ever computed.

    ``eq=False`` because the payload is a numpy array: a generated ``__eq__``
    would return an array and silently poison any ``==`` comparison.
    """

    #: ``(3,)`` the QTM-frame point that becomes the juggling-frame origin, m.
    origin: np.ndarray
    #: ``(3, 3)`` rows are the juggling-frame basis vectors in QTM coordinates:
    #: row 0 the hand axis `x_J`, row 1 forward `y_J`, row 2 up `z_J`.
    rotation: np.ndarray
    #: Quality of the derivation, or ``None`` when the transform did not come
    #: from a point cloud. ``None`` means *unknown*, never *perfect*.
    diagnostics: FrameDiagnostics | None = None

    def __post_init__(self) -> None:
        origin = _frozen(self.origin)
        rotation = _frozen(self.rotation)
        object.__setattr__(self, "origin", origin)
        object.__setattr__(self, "rotation", rotation)

        if origin.shape != (_AXES,):
            raise ValueError(f"origin must be (3,), got shape {origin.shape}")
        if rotation.shape != (_AXES, _AXES):
            raise ValueError(f"rotation must be (3, 3), got shape {rotation.shape}")
        # Finiteness first: every check below is a comparison, and a comparison
        # against NaN is False, so a NaN would sail through all of them.
        if not np.all(np.isfinite(origin)):
            raise ValueError("origin contains non-finite values")
        if not np.all(np.isfinite(rotation)):
            raise ValueError("rotation contains non-finite values")

        residual = float(np.max(np.abs(rotation @ rotation.T - np.eye(_AXES))))
        if residual > ORTHONORMAL_TOL:
            raise ValueError(
                f"rotation must be orthonormal to {ORTHONORMAL_TOL:g}; |R Rᵀ − I| is {residual:g}"
            )
        determinant = float(np.linalg.det(rotation))
        if abs(determinant - 1.0) > ORTHONORMAL_TOL:
            # det = −1 is a reflection: it would swap left and right hands, and
            # every chirality-dependent result with them.
            raise ValueError(f"rotation must be right-handed (det = +1), got det = {determinant:g}")

    # -- axes ------------------------------------------------------------- #

    @property
    def hand_axis_qtm(self) -> np.ndarray:
        """``(3,)`` unit `x_J` (left hand → right hand) in QTM coordinates."""
        return np.asarray(self.rotation[0])

    @property
    def forward_axis_qtm(self) -> np.ndarray:
        """``(3,)`` unit `y_J` (forward) in QTM coordinates."""
        return np.asarray(self.rotation[1])

    @property
    def up_axis_qtm(self) -> np.ndarray:
        """``(3,)`` unit `z_J` (up) in QTM coordinates — `(0, 0, 1)` when derived."""
        return np.asarray(self.rotation[2])

    # -- application ------------------------------------------------------ #

    def apply(self, points: np.ndarray) -> np.ndarray:
        """QTM-frame **positions** → juggling-frame positions, metres.

        ``R (p − origin)``. Accepts ``(3,)`` or ``(N, 3)`` and returns the same
        shape. Positions only: velocities and accelerations must not be
        translated, so they go through :meth:`apply_vector`.
        """
        p = _as_points(points, "points")
        return np.asarray((p - self.origin) @ self.rotation.T)

    def apply_vector(self, vectors: np.ndarray) -> np.ndarray:
        """QTM-frame **vectors** → juggling-frame vectors — rotation only.

        ``R v``, with no translation, so units are preserved: m/s in, m/s out;
        m/s² in, m/s² out. Accepts ``(3,)`` or ``(N, 3)``.
        """
        v = _as_points(vectors, "vectors")
        return np.asarray(v @ self.rotation.T)

    def invert(self, points: np.ndarray) -> np.ndarray:
        """Juggling-frame positions → QTM-frame positions, metres.

        ``Rᵀ q + origin``, the exact inverse of :meth:`apply` up to float
        round-off. Accepts ``(3,)`` or ``(N, 3)``.
        """
        q = _as_points(points, "points")
        return np.asarray(q @ self.rotation + self.origin)

    def invert_vector(self, vectors: np.ndarray) -> np.ndarray:
        """Juggling-frame vectors → QTM-frame vectors — rotation only."""
        v = _as_points(vectors, "vectors")
        return np.asarray(v @ self.rotation)

    # -- quality ---------------------------------------------------------- #

    def angle_to_nominal_hand_axis(self) -> float:
        """Angle between the derived and the nominal hand axis, in radians.

        The nominal hand axis is `+x_J` under NOTATION.md's nominal mapping
        ``x_J = −y_Q``, i.e. :data:`NOMINAL_HAND_AXIS_QTM` = `(0, −1, 0)` in QTM
        coordinates — QTM's `+y` is *left*, the juggling frame's `+x` points at
        the *right* hand, so the two differ by exactly a sign.

        A derived frame should be a small yaw away from nominal: the juggler
        faces roughly along QTM `+x` and the hands separate roughly along QTM
        `−y`. Phase 2 accepts ≤ 15° (PLAN.md P2); a much larger angle means the
        juggler stood at an unexpected yaw, or that the cloud fed in was not a
        throw/catch cloud at all. Because :func:`derive_frame` fixes the sign by
        the projection onto this axis, the result is in ``[0, π/2]``.

        Computed as ``atan2(|hand × nominal|, hand · nominal)`` rather than
        ``arccos(dot)``, which loses precision for small angles — where this
        number matters most.
        """
        hand = self.hand_axis_qtm
        sine = float(np.linalg.norm(np.cross(hand, NOMINAL_HAND_AXIS_QTM)))
        cosine = float(hand @ NOMINAL_HAND_AXIS_QTM)
        return math.atan2(sine, cosine)

    def __str__(self) -> str:
        origin = ", ".join(f"{v:.4f}" for v in self.origin)
        angle = math.degrees(self.angle_to_nominal_hand_axis())
        quality = "quality unknown" if self.diagnostics is None else str(self.diagnostics)
        return f"origin ({origin}) m, hand axis {angle:.2f}° from nominal, {quality}"


def derive_frame(points: np.ndarray, *, min_anisotropy: float = MIN_ANISOTROPY) -> FrameTransform:
    """Derive the juggling frame from a cloud of throw and catch positions.

    ``points`` is ``(N, 3)`` **QTM-frame** throw and catch positions in metres —
    the cloud between the hands (DESIGN.md §5 steps 3–5). Returns the
    :class:`FrameTransform` from QTM into the juggling frame, with
    :attr:`FrameTransform.diagnostics` filled in so the caller can see how well
    determined the hand axis is.

    The derivation, in order:

    1. **Origin** = the mean of the cloud, all three components, in metres. The
       QTM calibration origin and the floor are discarded (DESIGN.md §5).
    2. **Hand axis** = the principal axis of the cloud *in the horizontal
       plane*: eigen-decomposition of the 2×2 covariance of (x, y) about the
       origin. Z never enters the PCA — `z_J = z_Q` already, and the hand axis
       is horizontal *by definition*, so letting the vertical spread of the
       cloud tilt it would be a bug, not extra information.
    3. **Sign.** PCA gives a line, not a direction. The end that is the right
       hand is the one with a positive projection onto nominal `+x_J`,
       ``dot(axis, (0, −1, 0)) > 0``. At exactly 90° that projection is zero and
       the convention cannot resolve the sign; the tie is then broken on `+x_Q`
       so the function stays deterministic, and
       :meth:`FrameTransform.angle_to_nominal_hand_axis` reports the 90° that
       makes the frame suspect anyway.
    4. **Up** = `(0, 0, 1)` exactly, not fitted.
    5. **Forward** = ``up × hand``. In any right-handed basis `(x, y, z)`,
       ``z × x = y``; with `x` = hand and `z` = up, forward is therefore
       ``up × hand`` and not ``hand × up`` (which would give `−y_J`, a
       left-handed frame with Y pointing backwards). The nominal case checks
       out: ``(0, 0, 1) × (0, −1, 0) = (1, 0, 0) = +x_Q``, and NOTATION.md's
       nominal mapping is `y_J = x_Q`.

    The rotation's rows are then ``[hand, forward, up]``.

    Raises ``ValueError`` when the cloud cannot yield a frame: not ``(N, 3)``,
    non-finite values, fewer than 2 points, a horizontal extent under
    :data:`MIN_HORIZONTAL_SPREAD`, or a horizontal anisotropy below
    ``min_anisotropy`` (default :data:`MIN_ANISOTROPY`, whose docstring derives
    the value). Refusing is the honest answer: a near-circular cloud has no
    principal axis, and returning one anyway would hand the caller a
    confident-looking wrong frame.
    """
    if min_anisotropy < 1.0:
        raise ValueError(f"min_anisotropy is a ratio ≥ 1, got {min_anisotropy}")

    cloud = np.asarray(points, dtype=np.float64)
    if cloud.ndim != 2 or cloud.shape[1] != _AXES:
        raise ValueError(f"throw/catch cloud must be (N, 3), got shape {cloud.shape}")
    if not np.all(np.isfinite(cloud)):
        raise ValueError("throw/catch cloud contains non-finite values")
    n_points = int(cloud.shape[0])
    if n_points < _HORIZONTAL_AXES:
        raise ValueError(
            f"need at least 2 throw/catch positions to derive a hand axis, got {n_points}"
        )

    origin = cloud.mean(axis=0)

    # Horizontal PCA. `eigh` because the covariance is symmetric: it returns
    # ascending eigenvalues and orthonormal eigenvectors as columns.
    centred = cloud[:, :_HORIZONTAL_AXES] - origin[:_HORIZONTAL_AXES]
    covariance = centred.T @ centred / (n_points - 1)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    # Round-off can make a zero eigenvalue slightly negative; a variance cannot be.
    eigenvalues = np.clip(eigenvalues, 0.0, None)[::-1]
    major = float(eigenvalues[0])
    minor = float(eigenvalues[1])

    if major < MIN_HORIZONTAL_SPREAD**2:
        # An absolute floor, not `== 0.0`: a cloud whose points share one (x, y)
        # still leaves ~1e-33 m² of round-off in the covariance, whose principal
        # axis is pure floating-point noise pointing anywhere at all.
        raise ValueError(
            f"throw/catch cloud has no horizontal extent — its major-axis σ is "
            f"{math.sqrt(major):.3g} m, under the {MIN_HORIZONTAL_SPREAD:g} m floor, so every "
            f"point shares one (x, y) and there is no hand axis to find"
        )
    anisotropy = float("inf") if minor == 0.0 else major / minor
    if anisotropy < min_anisotropy:
        raise ValueError(
            f"throw/catch cloud is horizontally degenerate: anisotropy λ₁/λ₂ = {anisotropy:.3g} "
            f"< {min_anisotropy:g}, so its principal axis is not a hand axis "
            f"(λ = {major:.4g}, {minor:.4g} m² over {n_points} points)"
        )

    # `eigenvalues` is now descending but `eigenvectors` is still in `eigh`'s
    # ascending order, so the major axis is its *last* column.
    axis = eigenvectors[:, -1]
    hand = np.array([axis[0], axis[1], 0.0])
    hand /= np.linalg.norm(hand)
    # Sign: which end is the right hand (step 3). `> 0` keeps the nominal case
    # unflipped; the `+x_Q` tie-break only fires at exactly 90°.
    projection = float(hand @ NOMINAL_HAND_AXIS_QTM)
    if projection < 0.0 or (projection == 0.0 and hand[0] < 0.0):
        hand = -hand

    forward = np.cross(UP_AXIS_QTM, hand)
    rotation = np.stack([hand, forward, UP_AXIS_QTM])
    return FrameTransform(
        origin=origin,
        rotation=rotation,
        diagnostics=FrameDiagnostics(n_points=n_points, eigenvalues=eigenvalues),
    )


def to_juggling_frame(session: Session, transform: FrameTransform) -> Session:
    """A copy of ``session`` with every trajectory in the juggling frame.

    Positions stay in metres; only the frame changes. ``Session.frame`` becomes
    ``"juggling"``, which is what makes a second application detectable rather
    than silent — hence the ``ValueError`` when ``session.frame`` is already
    ``"juggling"``. Transforming twice would rotate and translate the data by
    the frame again and leave no trace of having done so, and DESIGN.md §5
    requires the frame a session is in to be recorded, not guessable.

    The model is immutable, so the input session is untouched and an analysis
    stays a chain of values (DESIGN.md §2).
    """
    if session.frame == "juggling":
        raise ValueError(
            "session is already in the juggling frame; applying the transform twice "
            "would corrupt every position (DESIGN.md §5)"
        )
    trajectories = tuple(
        trajectory.with_positions(transform.apply(trajectory.positions))
        for trajectory in session.trajectories
    )
    return replace(session, trajectories=trajectories, frame="juggling", frame_transform=transform)
