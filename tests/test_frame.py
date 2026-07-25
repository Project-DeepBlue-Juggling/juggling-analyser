"""Unit tests for the juggling-frame derivation and transform (DESIGN.md §5).

No recording is needed: a throw/catch cloud is cheap to synthesise and, unlike a
real one, its true hand axis is known exactly. That is the point — these tests
pin the *conventions* (NOTATION.md § Frames of reference), which is where this
module could be silently wrong in a way no real recording would reveal.

Two Phase 2 acceptance criteria live here (PLAN.md P2): the round trip through
the transform is identity to 1e-12, and the derived hand axis is compared with
the nominal frame in degrees, which is the number the 15° gate reads.

Seeding a ``np.random.Generator`` in a test is fine; only ``core/`` is forbidden
from creating one (CLAUDE.md rule 1).
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from juggling_analyser.core.frame import (
    MIN_ANISOTROPY,
    NOMINAL_HAND_AXIS_QTM,
    FrameDiagnostics,
    FrameTransform,
    derive_frame,
    to_juggling_frame,
)
from juggling_analyser.core.trajectory import Piece, Session, Trajectory, Uncertainty

RIGHT_J = np.array([1.0, 0.0, 0.0])
FORWARD_J = np.array([0.0, 1.0, 0.0])
UP_J = np.array([0.0, 0.0, 1.0])

#: Metres, for the hypothesis round-trip property — a mocap volume is a few
#: metres across, and 1e-12 m is an absolute tolerance, so the coordinate range
#: has to be bounded for it to mean anything.
COORDINATE = st.floats(min_value=-10.0, max_value=10.0)


def two_hand_cloud(
    rng: np.random.Generator,
    *,
    yaw: float = 0.0,
    separation: float = 0.4,
    scatter: float = 0.02,
    height: float = 1.1,
    n: int = 200,
) -> np.ndarray:
    """A synthetic QTM-frame throw/catch cloud: two hand clusters plus scatter.

    ``yaw`` rotates the hand axis about Z away from nominal `−y_Q`, in radians,
    so the true angle to the nominal axis is ``abs(yaw)``. ``separation`` is the
    distance between the two clusters. Returns ``(n, 3)`` in metres.
    """
    direction = np.array([math.sin(yaw), -math.cos(yaw), 0.0])  # yaw = 0 -> −y_Q
    offsets = np.where(np.arange(n) % 2 == 0, -separation / 2.0, separation / 2.0)
    centre = np.array([0.0, 0.0, height])
    noise = np.asarray(rng.normal(scale=scatter, size=(n, 3)))
    return np.asarray(centre + offsets[:, None] * direction + noise)


def make_session(*, n_trajectories: int = 3) -> Session:
    """A tiny QTM-frame session of straight lines, for ``to_juggling_frame``."""
    n = 5
    trajectories = tuple(
        Trajectory(
            id=str(i),
            frames=np.arange(1, n + 1, dtype=np.int64),
            positions=np.stack(
                [np.full(n, float(i)), np.linspace(0.0, 1.0, n), np.linspace(1.0, 2.0, n)], axis=1
            ),
            uncertainty=Uncertainty.isotropic(np.full(n, 1e-3)),
            sample_type=np.ones(n, dtype=np.uint8),
            pieces=(Piece(1, n, 1),),
        )
        for i in range(n_trajectories)
    )
    return Session(source="synthetic", f_s=300.0, frame_count=n, trajectories=trajectories)


# --------------------------------------------------------------------------- #
# the nominal mapping — the thing most likely to be built backwards
# --------------------------------------------------------------------------- #


def test_a_cloud_along_minus_y_qtm_reproduces_the_nominal_mapping() -> None:
    """NOTATION.md: ``x_J = −y_Q``, ``y_J = x_Q``, ``z_J = z_Q``."""
    rng = np.random.default_rng(0)
    # Scatter well under a millimetre, so the tolerances below test the
    # convention rather than the PCA's sampling error (which the yaw test does).
    transform = derive_frame(two_hand_cloud(rng, yaw=0.0, scatter=1e-4))

    assert transform.angle_to_nominal_hand_axis() == pytest.approx(0.0, abs=1e-3)
    # The three mappings, asserted explicitly on vectors (no translation).
    assert np.allclose(transform.apply_vector(NOMINAL_HAND_AXIS_QTM), RIGHT_J, atol=1e-3)
    assert np.allclose(transform.apply_vector(np.array([1.0, 0.0, 0.0])), FORWARD_J, atol=1e-3)
    assert np.allclose(transform.apply_vector(np.array([0.0, 0.0, 1.0])), UP_J, atol=0.0)
    # The derived basis is the nominal one to within the cloud's own scatter.
    assert np.allclose(transform.hand_axis_qtm, [0.0, -1.0, 0.0], atol=1e-3)
    assert np.allclose(transform.forward_axis_qtm, [1.0, 0.0, 0.0], atol=1e-3)
    assert np.allclose(transform.up_axis_qtm, [0.0, 0.0, 1.0], atol=0.0)


def test_forward_is_up_cross_hand_not_the_other_way_round() -> None:
    """`z × x = y`: the opposite order gives `−y_J` and a left-handed frame."""
    rng = np.random.default_rng(1)
    transform = derive_frame(two_hand_cloud(rng, yaw=0.3))
    hand, forward, up = transform.hand_axis_qtm, transform.forward_axis_qtm, transform.up_axis_qtm
    assert np.allclose(np.cross(up, hand), forward, atol=1e-12)
    assert np.allclose(np.cross(hand, forward), up, atol=1e-12)


@pytest.mark.parametrize("yaw_degrees", [-30.0, -14.0, -5.0, 0.0, 5.0, 14.0, 30.0])
def test_angle_to_nominal_hand_axis_reports_the_true_yaw(yaw_degrees: float) -> None:
    rng = np.random.default_rng(2)
    transform = derive_frame(two_hand_cloud(rng, yaw=math.radians(yaw_degrees), scatter=0.005))
    measured = math.degrees(transform.angle_to_nominal_hand_axis())
    assert measured == pytest.approx(abs(yaw_degrees), abs=0.5)


def test_sign_resolution_picks_the_same_end_from_either_direction() -> None:
    """PCA yields a line; the nominal projection picks which end is the right hand."""
    rng = np.random.default_rng(3)
    cloud = two_hand_cloud(rng, yaw=0.0, scatter=0.005)
    centre = cloud.mean(axis=0)
    mirrored = centre - (cloud - centre)  # the same line, pointing the other way

    forwards = derive_frame(cloud)
    backwards = derive_frame(mirrored)
    assert np.allclose(forwards.hand_axis_qtm, backwards.hand_axis_qtm, atol=1e-12)
    assert np.allclose(forwards.rotation, backwards.rotation, atol=1e-12)
    assert forwards.hand_axis_qtm @ NOMINAL_HAND_AXIS_QTM > 0.0


def test_a_hand_axis_at_exactly_ninety_degrees_is_resolved_deterministically() -> None:
    """The nominal projection is zero here, so the documented `+x_Q` tie-break decides.

    A frame this far from nominal is not usable — which is the point:
    ``angle_to_nominal_hand_axis`` reports 90° and the caller sees it, rather
    than the derivation flipping a coin over which hand is which.
    """
    along_x = np.array([[0.2, 0.0, 1.1], [-0.2, 0.0, 1.1]])
    transform = derive_frame(along_x)
    assert np.allclose(transform.hand_axis_qtm, [1.0, 0.0, 0.0])
    assert np.allclose(derive_frame(along_x[::-1]).hand_axis_qtm, transform.hand_axis_qtm)
    assert math.degrees(transform.angle_to_nominal_hand_axis()) == pytest.approx(90.0)
    assert np.linalg.det(transform.rotation) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# derivation from a realistic cloud
# --------------------------------------------------------------------------- #


def test_two_clusters_give_the_cluster_axis_the_midpoint_and_x_at_plus_minus_half() -> None:
    """Hands 0.4 m apart land at `x = ±0.2, y ≈ 0` in the juggling frame."""
    rng = np.random.default_rng(4)
    separation, scatter = 0.4, 0.02
    left = np.array([0.5, 0.2, 1.1]) + rng.normal(scale=scatter, size=(150, 3))
    right = np.array([0.5, 0.2 - separation, 1.1]) + rng.normal(scale=scatter, size=(150, 3))
    cloud = np.concatenate([left, right])

    transform = derive_frame(cloud)

    # Cluster-to-cluster is left -> right, which is −y_Q for this layout. The
    # hand axis is horizontal by definition, so it is the *horizontal* part of
    # the cluster-to-cluster direction that it has to match — the 8 mm of Z
    # between the two cluster means is exactly what must not tilt it.
    expected = right.mean(axis=0) - left.mean(axis=0)
    expected[2] = 0.0
    expected = expected / np.linalg.norm(expected)
    assert np.allclose(transform.hand_axis_qtm, expected, atol=5e-3)
    assert np.allclose(transform.origin, cloud.mean(axis=0), atol=0.0)
    assert np.allclose(transform.origin, [0.5, 0.0, 1.1], atol=0.01)

    left_j = transform.apply(left).mean(axis=0)
    right_j = transform.apply(right).mean(axis=0)
    assert left_j[0] == pytest.approx(-separation / 2.0, abs=0.01)
    assert right_j[0] == pytest.approx(+separation / 2.0, abs=0.01)
    assert left_j[1] == pytest.approx(0.0, abs=0.01)
    assert right_j[1] == pytest.approx(0.0, abs=0.01)


def test_the_origin_is_the_mean_of_the_cloud() -> None:
    cloud = np.array([[0.0, 1.0, 2.0], [0.0, -1.0, 4.0], [0.0, 3.0, 0.0], [0.0, -3.0, 6.0]])
    transform = derive_frame(cloud)
    assert np.allclose(transform.origin, [0.0, 0.0, 3.0])
    assert np.allclose(transform.apply(cloud).mean(axis=0), 0.0, atol=1e-12)


def test_z_is_untouched_apart_from_the_origin_shift() -> None:
    """Z is up in both frames, so the transform only subtracts the mean height."""
    rng = np.random.default_rng(5)
    transform = derive_frame(two_hand_cloud(rng, yaw=0.4, height=1.2))
    probe = np.array([[0.3, -0.7, 1.55], [-2.0, 4.0, 0.0]])
    assert np.allclose(transform.apply(probe)[:, 2], probe[:, 2] - transform.origin[2], atol=1e-12)
    # Vectors keep Z outright — no shift at all.
    assert np.allclose(transform.apply_vector(probe)[:, 2], probe[:, 2], atol=1e-12)


def test_a_purely_vertical_spread_does_not_tilt_the_hand_axis() -> None:
    """Z must not enter the PCA: a huge height spread must leave the axis flat."""
    rng = np.random.default_rng(6)
    cloud = two_hand_cloud(rng, yaw=0.0, scatter=0.005)
    cloud[:, 2] += np.linspace(0.0, 3.0, len(cloud))  # 3 m of vertical spread
    transform = derive_frame(cloud)
    assert transform.hand_axis_qtm[2] == 0.0
    assert transform.forward_axis_qtm[2] == 0.0
    assert transform.angle_to_nominal_hand_axis() == pytest.approx(0.0, abs=1e-3)


# --------------------------------------------------------------------------- #
# the round trip — Phase 2 acceptance criterion
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seed", range(8))
def test_round_trip_is_identity_to_1e_12(seed: int) -> None:
    rng = np.random.default_rng(seed)
    transform = derive_frame(two_hand_cloud(rng, yaw=float(rng.uniform(-1.0, 1.0))))
    points = rng.normal(scale=2.0, size=(500, 3))

    assert np.allclose(transform.invert(transform.apply(points)), points, rtol=0.0, atol=1e-12)
    assert np.allclose(transform.apply(transform.invert(points)), points, rtol=0.0, atol=1e-12)
    assert np.allclose(
        transform.invert_vector(transform.apply_vector(points)), points, rtol=0.0, atol=1e-12
    )


def test_round_trip_holds_for_a_single_point_too() -> None:
    transform = derive_frame(two_hand_cloud(np.random.default_rng(7)))
    point = np.array([1.234, -5.678, 0.9])
    assert transform.apply(point).shape == (3,)
    assert np.allclose(transform.invert(transform.apply(point)), point, rtol=0.0, atol=1e-12)


@given(
    st.lists(st.tuples(COORDINATE, COORDINATE, COORDINATE), min_size=1, max_size=24),
    st.floats(min_value=-1.4, max_value=1.4),
)
@settings(max_examples=50, deadline=None)
def test_round_trip_is_identity_for_any_point(
    points: list[tuple[float, float, float]], yaw: float
) -> None:
    transform = derive_frame(two_hand_cloud(np.random.default_rng(11), yaw=yaw, scatter=0.01))
    original = np.array(points, dtype=np.float64)
    assert np.allclose(transform.invert(transform.apply(original)), original, atol=1e-12)


# --------------------------------------------------------------------------- #
# FrameTransform validation
# --------------------------------------------------------------------------- #


def test_derived_rotation_is_orthonormal_and_right_handed() -> None:
    rng = np.random.default_rng(8)
    for yaw in (-1.2, -0.3, 0.0, 0.3, 1.2):
        rotation = derive_frame(two_hand_cloud(rng, yaw=yaw)).rotation
        assert np.allclose(rotation @ rotation.T, np.eye(3), rtol=0.0, atol=1e-12)
        assert np.linalg.det(rotation) == pytest.approx(1.0, abs=1e-12)


def test_apply_vector_ignores_the_translation_but_apply_does_not() -> None:
    transform = FrameTransform(origin=np.array([1.0, 2.0, 3.0]), rotation=np.eye(3))
    v = np.array([0.5, 0.5, 0.5])
    assert np.allclose(transform.apply(v), v - [1.0, 2.0, 3.0])
    assert np.allclose(transform.apply_vector(v), v)
    assert not np.allclose(transform.apply(v), transform.apply_vector(v))


def test_transform_rejects_a_non_orthonormal_rotation() -> None:
    sheared = np.array([[1.0, 0.1, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    with pytest.raises(ValueError, match="orthonormal"):
        FrameTransform(origin=np.zeros(3), rotation=np.eye(3) * 1.001)
    with pytest.raises(ValueError, match="orthonormal"):
        FrameTransform(origin=np.zeros(3), rotation=sheared)


def test_transform_rejects_a_left_handed_rotation() -> None:
    """A reflection would swap the hands, and every chirality-dependent result."""
    mirror = np.diag([1.0, -1.0, 1.0])
    assert np.allclose(mirror @ mirror.T, np.eye(3))  # orthonormal, but det = −1
    with pytest.raises(ValueError, match="right-handed"):
        FrameTransform(origin=np.zeros(3), rotation=mirror)


@pytest.mark.parametrize(
    ("origin", "rotation", "message"),
    [
        (np.zeros(2), np.eye(3), r"origin must be \(3,\)"),
        (np.zeros((3, 1)), np.eye(3), r"origin must be \(3,\)"),
        (np.zeros(3), np.eye(4), r"rotation must be \(3, 3\)"),
        (np.zeros(3), np.zeros(3), r"rotation must be \(3, 3\)"),
    ],
)
def test_transform_rejects_wrong_shapes(
    origin: np.ndarray, rotation: np.ndarray, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        FrameTransform(origin=origin, rotation=rotation)


def test_transform_rejects_non_finite_input() -> None:
    """NaN has to be caught explicitly: every other check is a comparison."""
    with pytest.raises(ValueError, match="origin contains non-finite"):
        FrameTransform(origin=np.array([0.0, np.nan, 0.0]), rotation=np.eye(3))
    rotation = np.eye(3)
    rotation[1, 1] = np.nan
    with pytest.raises(ValueError, match="rotation contains non-finite"):
        FrameTransform(origin=np.zeros(3), rotation=rotation)
    rotation[1, 1] = np.inf
    with pytest.raises(ValueError, match="rotation contains non-finite"):
        FrameTransform(origin=np.zeros(3), rotation=rotation)


def test_transform_payload_is_read_only() -> None:
    """``frozen=True`` has to reach the arrays: one transform is shared widely."""
    transform = derive_frame(two_hand_cloud(np.random.default_rng(9)))
    with pytest.raises(ValueError, match="read-only"):
        transform.rotation[0, 0] = 5.0
    with pytest.raises(ValueError, match="read-only"):
        transform.hand_axis_qtm[0] = 5.0


def test_the_transform_does_not_capture_the_callers_arrays() -> None:
    origin = np.array([1.0, 2.0, 3.0])
    transform = FrameTransform(origin=origin, rotation=np.eye(3))
    origin[0] = 99.0  # a copy was taken, so this must not move the frame
    assert transform.origin[0] == 1.0


def test_apply_rejects_misshaped_points() -> None:
    transform = FrameTransform(origin=np.zeros(3), rotation=np.eye(3))
    for bad in (np.zeros(2), np.zeros((4, 2)), np.zeros((2, 3, 3)), np.asarray(1.0)):
        with pytest.raises(ValueError, match=r"must be \(3,\) or \(N, 3\)"):
            transform.apply(bad)
    with pytest.raises(ValueError, match="vectors must be"):
        transform.apply_vector(np.zeros((4, 2)))


def test_apply_handles_an_empty_cloud() -> None:
    """A trajectory can legitimately be empty; (0, 3) must survive the transform."""
    transform = FrameTransform(origin=np.ones(3), rotation=np.eye(3))
    assert transform.apply(np.zeros((0, 3))).shape == (0, 3)


# --------------------------------------------------------------------------- #
# degeneracy — refusing is the honest answer
# --------------------------------------------------------------------------- #


def test_derive_frame_rejects_fewer_than_two_points() -> None:
    with pytest.raises(ValueError, match="at least 2 throw/catch positions"):
        derive_frame(np.zeros((1, 3)))
    with pytest.raises(ValueError, match="at least 2 throw/catch positions"):
        derive_frame(np.zeros((0, 3)))


def test_derive_frame_rejects_non_finite_values() -> None:
    cloud = two_hand_cloud(np.random.default_rng(10))
    cloud[17, 1] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        derive_frame(cloud)
    cloud[17, 1] = np.inf
    with pytest.raises(ValueError, match="non-finite"):
        derive_frame(cloud)


def test_derive_frame_rejects_a_misshaped_cloud() -> None:
    for bad in (np.zeros(3), np.zeros((5, 2)), np.zeros((5, 3, 1))):
        with pytest.raises(ValueError, match=r"cloud must be \(N, 3\)"):
            derive_frame(bad)


def test_derive_frame_rejects_an_isotropic_cloud() -> None:
    """A circular cloud has no principal axis, so it has no hand axis."""
    circular = np.random.default_rng(12).normal(scale=0.1, size=(400, 3))
    with pytest.raises(ValueError, match="horizontally degenerate"):
        derive_frame(circular)


def test_derive_frame_rejects_a_cloud_with_no_horizontal_extent() -> None:
    """All points on one vertical line — what feeding in a static marker looks like."""
    cloud = np.stack([np.full(50, 0.5), np.full(50, -0.3), np.linspace(0.0, 2.0, 50)], axis=1)
    with pytest.raises(ValueError, match="no horizontal extent"):
        derive_frame(cloud)


def test_min_anisotropy_is_a_knob_in_both_directions() -> None:
    rng = np.random.default_rng(13)
    circular = rng.normal(scale=0.1, size=(400, 3))
    relaxed = derive_frame(circular, min_anisotropy=1.0)
    assert relaxed.diagnostics is not None
    assert relaxed.diagnostics.anisotropy < MIN_ANISOTROPY  # the default would refuse it

    mildly_elongated = two_hand_cloud(rng, separation=0.06, scatter=0.02, n=400)
    accepted = derive_frame(mildly_elongated)
    assert accepted.diagnostics is not None
    ratio = accepted.diagnostics.anisotropy
    assert MIN_ANISOTROPY <= ratio < 100.0
    with pytest.raises(ValueError, match="horizontally degenerate"):
        derive_frame(mildly_elongated, min_anisotropy=ratio + 1.0)


def test_min_anisotropy_below_one_is_meaningless() -> None:
    with pytest.raises(ValueError, match="ratio ≥ 1"):
        derive_frame(np.zeros((5, 3)), min_anisotropy=0.5)


def test_two_distinct_points_are_enough() -> None:
    """Two points define a line exactly: λ₂ = 0, anisotropy infinite."""
    transform = derive_frame(np.array([[0.0, 0.2, 1.0], [0.0, -0.2, 1.0]]))
    assert np.allclose(transform.hand_axis_qtm, [0.0, -1.0, 0.0])
    assert transform.diagnostics is not None
    assert transform.diagnostics.anisotropy == math.inf
    assert transform.diagnostics.axis_angle_sigma == 0.0


# --------------------------------------------------------------------------- #
# diagnostics
# --------------------------------------------------------------------------- #


def test_diagnostics_describe_the_cloud_they_came_from() -> None:
    cloud = two_hand_cloud(np.random.default_rng(14), separation=0.4, scatter=0.02, n=300)
    transform = derive_frame(cloud)
    diagnostics = transform.diagnostics
    assert diagnostics is not None

    assert diagnostics.n_points == 300
    # λ₁ is the variance along the hand axis: clusters at ±0.2 m give ≈ 0.04 m².
    assert diagnostics.eigenvalues[0] == pytest.approx(0.04, rel=0.15)
    assert diagnostics.eigenvalues[1] == pytest.approx(0.02**2, rel=0.3)
    assert diagnostics.anisotropy > 50.0
    assert math.degrees(diagnostics.axis_angle_sigma) < 1.0  # well-determined axis
    assert "anisotropy" in str(diagnostics)
    assert "from nominal" in str(transform)


def test_a_hand_built_transform_has_unknown_quality() -> None:
    """``None`` means unknown, never perfect."""
    assert FrameTransform(origin=np.zeros(3), rotation=np.eye(3)).diagnostics is None


def test_anisotropy_and_axis_sigma_at_the_limits() -> None:
    collinear = FrameDiagnostics(n_points=10, eigenvalues=np.array([0.04, 0.0]))
    assert collinear.anisotropy == math.inf
    assert collinear.axis_angle_sigma == 0.0

    isotropic = FrameDiagnostics(n_points=10, eigenvalues=np.array([0.04, 0.04]))
    assert isotropic.anisotropy == pytest.approx(1.0)
    assert isotropic.axis_angle_sigma == math.inf

    # σ_θ = sqrt(λ₁λ₂ / (n (λ₁ − λ₂)²)), against the closed form.
    pair = FrameDiagnostics(n_points=100, eigenvalues=np.array([2.0, 1.0]))
    assert pair.anisotropy == pytest.approx(2.0)
    assert pair.axis_angle_sigma == pytest.approx(math.sqrt(2.0 / 100.0))


@pytest.mark.parametrize(
    ("n_points", "eigenvalues", "message"),
    [
        (10, np.array([1.0, 2.0]), "descending"),
        (10, np.array([1.0, -1.0]), "non-negative"),
        (10, np.array([1.0, 2.0, 3.0]), "2 eigenvalues"),
        (10, np.array([np.nan, 0.0]), "non-finite"),
        (1, np.array([1.0, 0.5]), "at least 2 points"),
    ],
)
def test_diagnostics_reject_impossible_values(
    n_points: int, eigenvalues: np.ndarray, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        FrameDiagnostics(n_points=n_points, eigenvalues=eigenvalues)


# --------------------------------------------------------------------------- #
# to_juggling_frame
# --------------------------------------------------------------------------- #


def test_to_juggling_frame_transforms_every_trajectory() -> None:
    session = make_session(n_trajectories=3)
    transform = derive_frame(two_hand_cloud(np.random.default_rng(15)))

    moved = to_juggling_frame(session, transform)

    assert moved.frame == "juggling"
    assert moved.n_trajectories == 3
    for before, after in zip(session.trajectories, moved.trajectories, strict=True):
        assert np.allclose(after.positions, transform.apply(before.positions))
        assert after.id == before.id
        assert np.array_equal(after.frames, before.frames)
        assert after.pieces == before.pieces
        assert after.uncertainty is before.uncertainty


def test_to_juggling_frame_leaves_the_input_session_untouched() -> None:
    session = make_session()
    original = [t.positions.copy() for t in session.trajectories]
    transform = derive_frame(two_hand_cloud(np.random.default_rng(16)))

    to_juggling_frame(session, transform)

    assert session.frame == "qtm"
    for trajectory, before in zip(session.trajectories, original, strict=True):
        assert np.array_equal(trajectory.positions, before)


def test_to_juggling_frame_refuses_a_second_application() -> None:
    """Transforming twice would corrupt every position and leave no trace."""
    transform = derive_frame(two_hand_cloud(np.random.default_rng(17)))
    once = to_juggling_frame(make_session(), transform)
    with pytest.raises(ValueError, match="already in the juggling frame"):
        to_juggling_frame(once, transform)


def test_to_juggling_frame_accepts_a_session_with_no_trajectories() -> None:
    transform = derive_frame(two_hand_cloud(np.random.default_rng(18)))
    moved = to_juggling_frame(make_session(n_trajectories=0), transform)
    assert moved.frame == "juggling"
    assert moved.trajectories == ()
