"""Identity linking (DESIGN.md §7, PLAN.md P4).

The synthetic cases build fragmented ball paths whose true identity is known, so
they measure *correctness*. The corpus tests check PLAN.md P4's stated criterion
on the real 5-ball clip, and record what actually happens on the much harder
3-ball clip rather than leaving it untested.

The synthetic generator here is deliberately self-contained: linking must be
testable without `core.synth`, so that a change to the degradation model cannot
quietly change what "100% correct linking" means.
"""

from __future__ import annotations

import json
from itertools import pairwise

import numpy as np
import pytest

from juggling_analyser.core.clean import classify_session, refine_with_flights
from juggling_analyser.core.flight import segment_session
from juggling_analyser.core.link import (
    Linking,
    estimate_ball_count,
    link_trajectories,
    score_linking,
)
from juggling_analyser.core.params import BALL_DIAMETER, MAX_LINK_GAP
from juggling_analyser.core.trajectory import Piece, Session, Trajectory, Uncertainty
from juggling_analyser.io.qtm import read_qtm

from .conftest import BALLS_ONLY_QTM, THREE_BALL_QTM, data_dir, sample

F_S = 300.0


# --------------------------------------------------------------------------- #
# synthetic cascades with known identity
# --------------------------------------------------------------------------- #


def truth_positions(name: str) -> np.ndarray:
    """`(n_balls, n_frames, 3)` exact ball positions from an Airtime truth fixture.

    Read with `json` rather than through `io.truth`, deliberately: linking must be
    testable independently of the truth reader, so a change there cannot quietly
    change what "100% correct linking" means here.

    These fixtures are far better truth than a hand-rolled cascade. Their minimum
    inter-ball distance is 138-199 mm against a 74 mm ball, so no pair is ever
    close enough for the identity to be genuinely ambiguous - which means a linking
    error is a real error rather than an artefact of impossible geometry.
    """
    path = data_dir() / "truth" / name
    if not path.exists():
        pytest.skip(f"truth fixture {name} not present")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == "airtime-truth-export/1"
    assert payload["frame"] == "juggling"
    assert float(payload["f_s"]) == F_S
    return np.asarray([ball["positions"] for ball in payload["balls"]], dtype=np.float64)


def fragment(
    positions: np.ndarray,
    *,
    cuts_per_ball: int,
    gap_frames: int,
    sigma: float = 5e-4,
    seed: int = 11,
) -> tuple[Session, dict[str, int]]:
    """Cut each ball's path into fragments separated by `gap_frames`, with truth.

    Returns a session of `ball`-classified trajectories and the answer key mapping
    trajectory id -> true ball index. Noise is added here rather than in the fixture
    so the fixture stays exact truth.
    """
    rng = np.random.default_rng(seed)
    n_balls, n_frames, _ = positions.shape
    noisy = positions + rng.normal(0.0, sigma, size=positions.shape)
    trajectories: list[Trajectory] = []
    truth: dict[str, int] = {}
    for ball in range(n_balls):
        margin = 300
        cut_points = sorted(
            int(c)
            for c in rng.choice(
                np.arange(margin, n_frames - margin), size=cuts_per_ball, replace=False
            )
        )
        bounds = [0, *cut_points, n_frames]
        for piece_index, (start, stop) in enumerate(pairwise(bounds)):
            first = start if piece_index == 0 else start + gap_frames
            if stop - first < 60:
                continue
            frames = np.arange(first + 1, stop + 1, dtype=np.int64)
            identifier = f"b{ball}p{piece_index}"
            trajectories.append(
                Trajectory(
                    id=identifier,
                    frames=frames,
                    positions=noisy[ball, first:stop],
                    uncertainty=Uncertainty.isotropic(np.full(stop - first, sigma)),
                    sample_type=np.ones(stop - first, dtype=np.uint8),
                    pieces=(Piece(int(frames[0]), int(frames[-1]), 1),),
                    kind="ball",
                )
            )
            truth[identifier] = ball
    session = Session(
        source="synthetic",
        f_s=F_S,
        frame_count=n_frames,
        trajectories=tuple(trajectories),
        frame="juggling",
    )
    return session, truth


def identity_purity(linking: Linking, truth: dict[str, int]) -> float:
    """Fraction of lanes containing samples from exactly one true ball.

    Distinct from `score_linking`, and both matter. A ball split across two lanes is
    a *coverage* failure - every lane is still pure - whereas a lane holding two
    balls is an *identity* failure, which is far worse: it corrupts dwell times and
    the siteswap. Reporting them separately says which kind occurred.
    """
    if not linking.balls:
        return 0.0
    pure = sum(
        1 for lane in linking.balls if len({truth[s.trajectory_id] for s in lane.spans}) == 1
    )
    return pure / len(linking.balls)


@pytest.mark.parametrize(
    ("fixture", "n_balls", "expected_score", "expected_purity", "expected_lanes"),
    [
        ("3.json", 3, 1.000, 1.0, 3),
        ("4.json", 4, 1.000, 1.0, 4),
        ("5.json", 5, 0.615, 0.571, 7),
        ("7.json", 7, 0.952, 1.0, 8),
        ("441.json", 3, 0.889, 1.0, 4),
        ("531.json", 3, 0.778, 1.0, 5),
        ("552.json", 4, 0.583, 0.50, 6),
        ("423.json", 3, 0.778, 1.0, 5),
    ],
)
def test_synthetic_linking_measured(
    fixture: str,
    n_balls: int,
    expected_score: float,
    expected_purity: float,
    expected_lanes: int,
) -> None:
    """Pin what the linker actually achieves on each labelled truth fixture.

    These are *measurements*, not the acceptance targets — PLAN.md P4 asks for 100%
    on 3 and 5 balls and >= 95% on 7, and the 5-ball case does not meet it. That
    shortfall is asserted separately below as a strict xfail so it cannot be lost,
    and analysed in BUILD_LOG.md Phase 4. Pinning the achieved numbers here means an
    algorithm change has to acknowledge moving them in either direction.

    `ball_count` is the overlap estimate and is correct in every case; the lane
    count is what differs, which locates the failure in *bridging*, not counting.
    """
    positions = truth_positions(fixture)
    assert positions.shape[0] == n_balls
    session, truth = fragment(positions, cuts_per_ball=2, gap_frames=5)
    linking = link_trajectories(session, segment_session(session, calibrate=False).flights)
    assert linking.ball_count == n_balls, "the overlap estimator must get the count right"
    assert score_linking(linking, truth) == pytest.approx(expected_score, abs=0.02)
    assert identity_purity(linking, truth) == pytest.approx(expected_purity, abs=0.02)
    assert len(linking.balls) == expected_lanes


@pytest.mark.parametrize(("fixture", "n_balls"), [("3.json", 3), ("4.json", 4)])
def test_synthetic_linking_is_exact_where_it_can_be(fixture: str, n_balls: int) -> None:
    """The cases that do meet PLAN.md P4: 100% correct, every lane pure."""
    positions = truth_positions(fixture)
    session, truth = fragment(positions, cuts_per_ball=2, gap_frames=5)
    linking = link_trajectories(session, segment_session(session, calibrate=False).flights)
    assert score_linking(linking, truth) == 1.0
    assert identity_purity(linking, truth) == 1.0
    assert len(linking.balls) == n_balls


@pytest.mark.xfail(
    strict=True,
    reason=(
        "PLAN.md P4 requires 100% linking on the synthetic 5-ball case; measured "
        "0.615 with 7 lanes for 5 balls. Recorded, not weakened — see BUILD_LOG.md "
        "Phase 4. Strict, so this test fails the suite if the linker improves and "
        "the shortfall is silently left on the record."
    ),
)
def test_five_ball_synthetic_meets_the_acceptance_criterion() -> None:
    positions = truth_positions("5.json")
    session, truth = fragment(positions, cuts_per_ball=2, gap_frames=5)
    linking = link_trajectories(session, segment_session(session, calibrate=False).flights)
    assert score_linking(linking, truth) == 1.0
    assert len(linking.balls) == 5


def test_seven_ball_synthetic_meets_the_acceptance_criterion() -> None:
    """PLAN.md P4: >= 95% on 7 balls. Measured 0.952, with every lane pure."""
    positions = truth_positions("7.json")
    session, truth = fragment(positions, cuts_per_ball=2, gap_frames=5)
    linking = link_trajectories(session, segment_session(session, calibrate=False).flights)
    assert score_linking(linking, truth) >= 0.95
    assert identity_purity(linking, truth) == 1.0


@pytest.mark.parametrize("gap_frames", [5, 20, 60, 100])
def test_lanes_stay_pure_across_a_range_of_gap_lengths(gap_frames: int) -> None:
    """Identity purity is the property that must not degrade with gap length.

    Coverage does degrade — a longer gap eventually cannot be bridged and the ball
    splits into two lanes — and that is the honest outcome. What must never happen
    is two different balls ending up in one lane, because that corrupts dwell times
    and the siteswap rather than merely fragmenting them.
    """
    positions = truth_positions("3.json")
    session, truth = fragment(positions, cuts_per_ball=2, gap_frames=gap_frames)
    segmentation = segment_session(session, calibrate=False)
    linking = link_trajectories(session, segmentation.flights)
    assert identity_purity(linking, truth) == 1.0, f"gap {gap_frames} frames"


def test_lanes_never_overlap_in_time() -> None:
    """The non-overlap constraint: one trajectory cannot be two balls, and two
    trajectories of one ball cannot coexist (DESIGN.md §7)."""
    positions = truth_positions("5.json")
    session, _truth = fragment(positions, cuts_per_ball=3, gap_frames=15)
    linking = link_trajectories(session, segment_session(session, calibrate=False).flights)
    for ball in linking.balls:
        for before, after in pairwise(ball.spans):
            assert after.first_frame > before.last_frame, f"ball {ball.id} spans overlap"
    # Every trajectory is used exactly once.
    used = [span.trajectory_id for b in linking.balls for span in b.spans]
    assert sorted(used) == sorted(t.id for t in session.balls)
    assert len(used) == len(set(used))


def test_bridged_gaps_are_recorded_as_inferred() -> None:
    positions = truth_positions("3.json")
    session, _truth = fragment(positions, cuts_per_ball=2, gap_frames=30)
    linking = link_trajectories(session, segment_session(session, calibrate=False).flights)
    gaps = [gap for ball in linking.balls for gap in ball.gaps]
    assert gaps, "expected some bridged gaps"
    for gap in gaps:
        assert gap.inferred
        # At least the gap that was cut: the generator drops fragments that end up
        # too short, which merges two cuts into one longer gap.
        assert gap.n_frames >= 30
        assert gap.mode in {"ballistic", "carry"}
        assert gap.confident, "the cut gaps are all well inside the confident bound"


def test_a_gap_too_long_to_bridge_is_not_bridged() -> None:
    """Beyond `MAX_LINK_GAP` the linker adds a lane rather than guessing."""
    positions = truth_positions("3.json")
    session, _truth = fragment(positions, cuts_per_ball=1, gap_frames=250)
    linking = link_trajectories(session, segment_session(session, calibrate=False).flights)
    assert len(linking.balls) > 3
    # Nothing longer than MAX_LINK_GAP is crossed. Shorter gaps still exist, because
    # the generator drops fragments that end up too short and merges their cuts.
    for ball in linking.balls:
        for gap in ball.gaps:
            assert gap.n_frames / F_S <= MAX_LINK_GAP


def test_ball_count_comes_with_its_evidence() -> None:
    positions = truth_positions("5.json")
    session, _truth = fragment(positions, cuts_per_ball=1, gap_frames=10)
    count, histogram = estimate_ball_count(session.balls)
    assert count == 5
    assert histogram[5] > histogram.get(4, 0), "5 active should dominate"
    assert sum(histogram.values()) > 0
    assert estimate_ball_count(()) == (0, {})


def test_linking_is_deterministic() -> None:
    positions = truth_positions("5.json")
    session, _truth = fragment(positions, cuts_per_ball=2, gap_frames=20)
    flights = segment_session(session, calibrate=False).flights
    first = link_trajectories(session, flights)
    second = link_trajectories(session, flights)
    assert [b.trajectory_ids for b in first.balls] == [b.trajectory_ids for b in second.balls]


def test_empty_session_links_to_nothing() -> None:
    session = Session(source="x", f_s=F_S, frame_count=100)
    linking = link_trajectories(session)
    assert linking.balls == ()
    assert linking.ball_count == 0


def test_score_linking_ignores_spurious_trajectories() -> None:
    positions = truth_positions("3.json")
    session, truth = fragment(positions, cuts_per_ball=1, gap_frames=15)
    linking = link_trajectories(session, segment_session(session, calibrate=False).flights)
    # A reflection the linker never saw must not count against it.
    truth_with_junk = {**truth, "reflection": -1}
    assert score_linking(linking, truth_with_junk) == score_linking(linking, truth)
    assert score_linking(linking, {}) == 0.0


def test_collisions_are_detected_when_two_balls_are_too_close() -> None:
    n = 400
    frames = np.arange(1, n + 1, dtype=np.int64)
    base = np.stack([np.zeros(n), np.zeros(n), np.linspace(1.0, 1.5, n)], axis=1)

    def make(identifier: str, offset: float) -> Trajectory:
        return Trajectory(
            id=identifier,
            frames=frames,
            positions=base + np.array([offset, 0.0, 0.0]),
            uncertainty=Uncertainty.isotropic(np.full(n, 1e-4)),
            sample_type=np.ones(n, dtype=np.uint8),
            pieces=(Piece(1, n, 1),),
            kind="ball",
        )

    close = Session(
        source="x",
        f_s=F_S,
        frame_count=n,
        trajectories=(make("a", 0.0), make("b", BALL_DIAMETER * 0.5)),
    )
    assert len(link_trajectories(close).collisions) == n
    apart = Session(
        source="x",
        f_s=F_S,
        frame_count=n,
        trajectories=(make("a", 0.0), make("b", BALL_DIAMETER * 2)),
    )
    assert link_trajectories(apart).collisions == ()


# --------------------------------------------------------------------------- #
# the corpus
# --------------------------------------------------------------------------- #


def _link_corpus(name: str) -> tuple[Session, object]:
    session, _report = classify_session(read_qtm(sample(name)))
    segmentation = segment_session(session)
    session, _refined = refine_with_flights(session, segmentation.flights)
    return session, link_trajectories(session, segmentation.flights)


def test_five_ball_clip_gives_exactly_five_lanes_tiling_the_recording() -> None:
    """PLAN.md P4's corpus criterion, in full."""
    session, linking = _link_corpus(BALLS_ONLY_QTM)
    assert linking.ball_count == 5  # type: ignore[attr-defined]
    assert len(linking.balls) == 5  # type: ignore[attr-defined]
    for ball in linking.balls:  # type: ignore[attr-defined]
        assert ball.first_frame == 1, f"ball {ball.id} starts at {ball.first_frame}"
        assert ball.last_frame == session.frame_count, f"ball {ball.id} ends early"
    total_gap = linking.total_bridged_frames + linking.total_uncertain_frames  # type: ignore[attr-defined]
    assert total_gap <= 400, f"total gap {total_gap} frames"
    # 383 is the real figure: 24 452 measured of 24 835 ball-frames, i.e. 98.46%.
    assert total_gap == 383
    measured = sum(ball.measured_frames for ball in linking.balls)  # type: ignore[attr-defined]
    assert measured == 24452


def test_five_ball_clip_non_collision() -> None:
    """The one violation is 0.2 mm inside the threshold — and the scale finding explains it.

    PLAN.md P4 asks for no non-collision violation. There is exactly one, at
    73.8 mm against a 74 mm ball diameter. Phase 2 measured this corpus's lengths
    as ~2.9% short (BUILD_LOG.md), and correcting for that puts the pair at 76.0 mm
    — no violation. Asserted as the *measured* numbers rather than waved away,
    because if the scale finding is wrong then this is a real overlap.
    """
    _session, linking = _link_corpus(BALLS_ONLY_QTM)
    collisions = linking.collisions  # type: ignore[attr-defined]
    assert len(collisions) <= 1, f"{len(collisions)} collisions"
    if collisions:
        closest = min(c.distance for c in collisions)
        assert closest == pytest.approx(0.0738, abs=1e-4)
        assert closest > BALL_DIAMETER - 0.001, "within 1 mm of the threshold"
        corrected = closest / (1.0 - 0.0287)
        assert corrected > BALL_DIAMETER, "the measured scale deficit accounts for it"


def test_three_ball_clip_linking_is_recorded_not_asserted_as_good() -> None:
    """The 3-ball clip does *not* link into 3 balls, and this pins why.

    Only 69% of its ball-frames are tracked and its longest untracked stretches run
    to seconds, so no bridge can honestly cross them. PLAN.md P4 sets a criterion
    for the 5-ball clip only; this test exists so the 3-ball behaviour is a recorded
    number rather than an untested unknown (BUILD_LOG.md, Phase 4).
    """
    _session, linking = _link_corpus(THREE_BALL_QTM)
    assert 4 <= len(linking.balls) <= 8, f"{len(linking.balls)} lanes"  # type: ignore[attr-defined]
    # The ball-count estimator over-counts because trajectories briefly overlap.
    assert linking.ball_count == 4  # type: ignore[attr-defined]
    assert linking.active_histogram.get(4, 0) < 100, (  # type: ignore[attr-defined]
        "only a handful of frames should have 4 active trajectories"
    )
