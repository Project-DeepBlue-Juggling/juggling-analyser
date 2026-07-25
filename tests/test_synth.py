"""Synthetic ground truth: the truth reader, and the mocap degradation model.

Three groups of tests, in the order the data flows:

1. **The truth types and the reader** (`io.truth`) — strict validation, plus the
   two cross-checks that are reported as *data* rather than raised.
2. **The degradation model** (`core.synth`) — determinism, the P1 data-model
   round-trip, the answer key, and each degradation isolated by zeroing the others.
3. **Calibration** — the degraded output against the *measured* statistics of both
   real clips, within a factor of two (PLAN.md P3). Every assertion message carries
   the number actually achieved, so a failure says how far off it is.

`cascade_truth` below stands in for Airtime when a clip of a specific length is
needed: the committed fixtures are all 3000 frames, and the calibration has to be
done on a 5-ball 4967-frame clip and a 3-ball 9101-frame one because fragmentation
per ball depends on the clip's duration as well as its quality.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from juggling_analyser.core.clean import classify_session, classify_trajectory, refine_with_flights
from juggling_analyser.core.flight import check_gravity, segment_session, segment_trajectory
from juggling_analyser.core.params import GRAVITY
from juggling_analyser.core.synth import (
    CLEAN_PRESET,
    NOISY_PRESET,
    DegradationParams,
    Truth,
    TruthEvent,
    crossing_events,
    degrade,
)
from juggling_analyser.core.trajectory import Session
from juggling_analyser.io.truth import TRUTH_SCHEMA, frame_of, read_truth, read_truth_document

from .conftest import data_dir

F_S = 300.0

#: Depth of the dip the hand makes while carrying a ball, m — enough that a carry's
#: acceleration is nowhere near `−g` and the flight boundary is unambiguous.
HOLD_DEPTH = 0.20


# --------------------------------------------------------------------------- #
# a cascade generator, standing in for Airtime
# --------------------------------------------------------------------------- #


def _hand_points(hand: int) -> tuple[np.ndarray, np.ndarray]:
    """`(throw_point, catch_point)` of one hand, matching Airtime's line preset."""
    side = -1.0 if hand == 0 else 1.0
    return np.array([side * 0.1, 0.0, 0.0]), np.array([side * 0.3, 0.0, 0.0])


def cascade_truth(
    ball_count: int,
    frame_count: int,
    *,
    f_s: float = F_S,
    beat_period: float = 0.2,
    dwell_ratio: float = 0.7,
    gravity: float = GRAVITY,
) -> Truth:
    """An exact `n`-ball cascade of any length, with every event labelled.

    Odd ball counts only, so the pattern really is a cascade (an even `h` returns
    the ball to the throwing hand). Flights are exact parabolas under ``gravity``;
    carries are a quintic between the hand's catch and throw points with a dip, so
    their acceleration is tens of m/s² from free fall.
    """
    if ball_count % 2 == 0:
        raise ValueError(f"a cascade needs an odd ball count, got {ball_count}")
    hand_count = 2
    height = ball_count
    dwell_time = dwell_ratio * hand_count * beat_period
    air_time = height * beat_period - dwell_time
    if air_time <= 0.0:
        raise ValueError(f"dwell_ratio {dwell_ratio} leaves no air time at h={height}")
    duration = frame_count / f_s
    times = np.arange(frame_count, dtype=np.float64) / f_s
    positions = np.zeros((ball_count, frame_count, 3), dtype=np.float64)
    events: list[TruthEvent] = []
    down = np.array([0.0, 0.0, 1.0])

    # Enough beats to cover the clip plus the tail of the last ball in the air.
    for beat in range(math.ceil(duration / beat_period) + height + 1):
        ball = beat % ball_count
        hand = beat % hand_count
        catch_hand = (beat + height) % hand_count
        throw_point, _ = _hand_points(hand)
        catch_throw_point, catch_point = _hand_points(catch_hand)
        velocity = (catch_point - throw_point) / air_time
        velocity[2] += 0.5 * gravity * air_time

        throw_time = beat * beat_period
        catch_time = throw_time + air_time
        flight = (times >= throw_time) & (times <= catch_time)
        elapsed = (times[flight] - throw_time)[:, None]
        positions[ball, flight] = (
            throw_point + velocity * elapsed - 0.5 * gravity * elapsed**2 * down
        )

        carry = (times > catch_time) & (times < catch_time + dwell_time)
        positions[ball, carry] = _carry_path(
            (times[carry] - catch_time) / dwell_time, catch_point, catch_throw_point
        )

        if throw_time >= duration:
            continue
        events.append(
            TruthEvent(
                kind="throw",
                ball=ball,
                hand=_hand_name(hand),
                frame=frame_of(throw_time, f_s),
                time=throw_time,
                position=throw_point,
                throw_value=height,
            )
        )
        apex_time = throw_time + velocity[2] / gravity
        if throw_time <= apex_time <= min(catch_time, duration):
            rise = apex_time - throw_time
            apex = throw_point + velocity * rise - 0.5 * gravity * rise**2 * down
            events.append(
                TruthEvent(
                    kind="apex",
                    ball=ball,
                    hand=_hand_name(hand),
                    frame=frame_of(apex_time, f_s),
                    time=apex_time,
                    position=apex,
                )
            )
        if catch_time < duration:
            events.append(
                TruthEvent(
                    kind="catch",
                    ball=ball,
                    hand=_hand_name(catch_hand),
                    frame=frame_of(catch_time, f_s),
                    time=catch_time,
                    position=catch_point,
                )
            )

    _fill_opening_carries(positions, times, ball_count, beat_period, dwell_time)
    events.sort(key=lambda e: (e.time, e.ball, e.kind))
    return Truth(
        pattern=str(ball_count),
        ball_count=ball_count,
        hand_count=hand_count,
        beat_period=beat_period,
        dwell_ratio=dwell_ratio,
        gravity=gravity,
        f_s=f_s,
        frame_count=frame_count,
        positions=positions,
        events=tuple(events),
    )


def _hand_name(hand: int) -> str:
    return "left" if hand == 0 else "right"


def _carry_path(fraction: np.ndarray, start: np.ndarray, end: np.ndarray) -> np.ndarray:
    """Quintic smoothstep from ``start`` to ``end`` with a dip of ``HOLD_DEPTH``."""
    s = fraction[:, None]
    smooth = 10.0 * s**3 - 15.0 * s**4 + 6.0 * s**5
    dip = np.array([0.0, 0.0, HOLD_DEPTH]) * np.sin(math.pi * s)
    return np.asarray(start + (end - start) * smooth - dip)


def _fill_opening_carries(
    positions: np.ndarray,
    times: np.ndarray,
    ball_count: int,
    beat_period: float,
    dwell_time: float,
) -> None:
    """Before its first throw, each ball waits in the hand and carries into it."""
    for ball in range(ball_count):
        hand = ball % 2
        throw_point, catch_point = _hand_points(hand)
        throw_time = ball * beat_period
        before = times < throw_time
        fraction = np.clip((times[before] - (throw_time - dwell_time)) / dwell_time, 0.0, 1.0)
        positions[ball, before] = _carry_path(fraction, catch_point, throw_point)


# The two clips the calibration targets were measured on.
def clean_calibration_truth() -> Truth:
    """5 balls, 4967 frames — the shape of `5_ball_juggling_cut_balls_only`.

    `τ_b = 0.2 s` gives `t_air = 0.72 s` and a 0.64 m apex, matching BUILD_LOG
    Phase 2's measured 0.57–0.72 m for that clip's best-determined flights.
    """
    return cascade_truth(5, 4967, beat_period=0.2)


def noisy_calibration_truth() -> Truth:
    """3 balls, 9101 frames — the shape of `3_ball_juggling_cut`.

    `τ_b = 0.3 s` gives `t_air = 0.48 s` and a 0.28 m apex, against that clip's
    measured median `t_air` of 0.463 s and median apex height of 0.287 m.
    """
    return cascade_truth(3, 9101, beat_period=0.3)


# --------------------------------------------------------------------------- #
# 1a. the truth types
# --------------------------------------------------------------------------- #


def test_cascade_truth_is_internally_consistent() -> None:
    truth = cascade_truth(3, 900, beat_period=0.3)
    assert truth.n_balls == truth.ball_count == 3
    assert truth.positions.shape == (3, 900, 3)
    assert truth.duration == pytest.approx(3.0)
    assert truth.dwell_time == pytest.approx(0.7 * 2 * 0.3)
    assert truth.average_theorem().holds()
    for event in truth.events:
        assert 1 <= event.frame <= truth.frame_count
        assert event.frame == frame_of(event.time, truth.f_s)


def test_truth_helpers_partition_the_events() -> None:
    truth = cascade_truth(3, 900, beat_period=0.3)
    kinds = len(truth.throws()) + len(truth.catches()) + len(truth.apexes())
    assert kinds == len(truth.events)
    for ball in range(truth.ball_count):
        mine = truth.events_for(ball)
        assert mine, "every ball is thrown at least once in 3 s"
        assert all(e.ball == ball for e in mine)
        times = [e.time for e in mine]
        assert times == sorted(times)
    assert "3 balls" in truth.summary()


def test_truth_position_at_is_one_based() -> None:
    truth = cascade_truth(3, 900, beat_period=0.3)
    assert np.array_equal(truth.position_at(1, 1), truth.positions[1, 0])
    assert np.array_equal(truth.position_at(1, 900), truth.positions[1, 899])
    with pytest.raises(ValueError, match=r"outside 1\.\.900"):
        truth.position_at(1, 0)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"kind": "drop"}, "unknown event kind"),
        ({"hand": "third"}, "unknown hand"),
        ({"frame": 0}, "frames are 1-based"),
        ({"ball": -1}, "non-negative"),
        ({"position": np.zeros(2)}, r"must be \(3,\)"),
        ({"position": np.array([0.0, np.nan, 0.0])}, "non-finite position"),
        ({"time": math.inf}, "non-finite time"),
        ({"throw_value": None}, "no throw_value"),
    ],
)
def test_truth_event_rejects_malformed_input(kwargs: dict[str, Any], match: str) -> None:
    good: dict[str, Any] = {
        "kind": "throw",
        "ball": 0,
        "hand": "left",
        "frame": 5,
        "time": 0.0133,
        "position": np.zeros(3),
        "throw_value": 3,
    }
    with pytest.raises(ValueError, match=match):
        TruthEvent(**{**good, **kwargs})


def test_truth_rejects_events_outside_the_recording() -> None:
    positions = np.zeros((1, 10, 3))
    late = TruthEvent("throw", 0, "left", 11, 0.033, np.zeros(3), 3)
    with pytest.raises(ValueError, match="past the last frame 10"):
        _truth_with(positions, (late,))
    absent = TruthEvent("throw", 3, "left", 5, 0.013, np.zeros(3), 3)
    with pytest.raises(ValueError, match="only 1 balls"):
        _truth_with(positions, (absent,))


def test_truth_rejects_a_bad_position_block() -> None:
    with pytest.raises(ValueError, match=r"positions must be \(1, 10, 3\)"):
        _truth_with(np.zeros((1, 9, 3)), ())
    bad = np.zeros((1, 10, 3))
    bad[0, 4, 2] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        _truth_with(bad, ())


def _truth_with(positions: np.ndarray, events: tuple[TruthEvent, ...]) -> Truth:
    return Truth(
        pattern="3",
        ball_count=1,
        hand_count=2,
        beat_period=0.25,
        dwell_ratio=0.6,
        gravity=GRAVITY,
        f_s=F_S,
        frame_count=10,
        positions=positions,
        events=events,
    )


def test_frame_of_rounds_half_up_not_half_to_even() -> None:
    """Airtime rounds half up; `round` rounds half to even. They differ often."""
    assert frame_of(0.0, F_S) == 1
    # t = 1.5 samples: half up gives frame 3, banker's rounding would give 2.
    assert frame_of(1.5 / F_S, F_S) == 3
    assert frame_of(2.5 / F_S, F_S) == 4
    assert round(2.5) + 1 == 3  # the wrong answer, pinned so the contrast is explicit


# --------------------------------------------------------------------------- #
# 1b. the reader
# --------------------------------------------------------------------------- #


def _document(**overrides: Any) -> dict[str, Any]:
    """A minimal but valid truth export: 2 balls, 4 frames, one throw."""
    document: dict[str, Any] = {
        "schema": TRUTH_SCHEMA,
        "pattern": "3",
        "ball_count": 2,
        "hand_count": 2,
        "beat_period": 0.25,
        "dwell_ratio": 0.6,
        "gravity": GRAVITY,
        "f_s": 300,
        "frame_count": 4,
        "frame": "juggling",
        "generator": {"repo": "airtime", "commit": "deadbeef", "params": {"pattern": "3"}},
        "balls": [
            {"id": 0, "positions": [[0.0, 0.0, 0.0]] * 4},
            {"id": 1, "positions": [[0.1, 0.0, 0.2]] * 4},
        ],
        "events": [
            {
                "kind": "throw",
                "ball": 0,
                "hand": "right",
                "frame": 1,
                "time": 0.0,
                "position": [0.0, 0.0, 0.0],
                "throw_value": 3,
            }
        ],
    }
    document.update(overrides)
    return document


def _write(tmp_path: Path, document: dict[str, Any], name: str = "t.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_reader_round_trips_a_hand_built_export(tmp_path: Path) -> None:
    document = read_truth_document(_write(tmp_path, _document()))
    truth = document.truth
    assert truth.pattern == "3"
    assert (truth.ball_count, truth.hand_count, truth.frame_count) == (2, 2, 4)
    assert truth.f_s == 300.0
    assert truth.gravity == pytest.approx(GRAVITY)
    assert truth.positions.shape == (2, 4, 3)
    assert np.allclose(truth.positions[1, 3], [0.1, 0.0, 0.2])
    assert len(truth.events) == 1
    assert truth.throws()[0].throw_value == 3
    assert document.generator.repo == "airtime"
    assert document.generator.commit == "deadbeef"
    assert document.generator.params["pattern"] == "3"
    assert document.frames_consistent
    # read_truth is the same parse, without the findings.
    assert np.array_equal(read_truth(_write(tmp_path, _document())).positions, truth.positions)


def test_reader_orders_positions_by_ball_id_not_file_order(tmp_path: Path) -> None:
    document = _document(
        balls=[
            {"id": 1, "positions": [[0.1, 0.0, 0.2]] * 4},
            {"id": 0, "positions": [[0.0, 0.0, 0.0]] * 4},
        ]
    )
    truth = read_truth(_write(tmp_path, document))
    assert np.allclose(truth.positions[0], 0.0)
    assert np.allclose(truth.positions[1, 0], [0.1, 0.0, 0.2])


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"schema": "airtime-truth-export/2"}, "expected schema"),
        ({"schema": None}, "expected schema"),
        ({"frame": "sim"}, "must be in the 'juggling' frame"),
        ({"frame": 3}, "'frame' must be a string"),
        ({"ball_count": 3}, "ball_count is 3 but 'balls' has 2"),
        ({"ball_count": 0}, "'ball_count' must be at least 1"),
        ({"f_s": 0}, "'f_s' must be greater than 0"),
        ({"frame_count": 0}, "'frame_count' must be at least 1"),
        ({"gravity": "9.8"}, "'gravity' must be a number"),
        ({"balls": {}}, "'balls' must be a list"),
        ({"events": {}}, "'events' must be a list"),
        ({"generator": []}, "'generator' must be an object"),
    ],
)
def test_reader_rejects_bad_headers(tmp_path: Path, overrides: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        read_truth(_write(tmp_path, _document(**overrides)))


def test_reader_rejects_a_short_position_array(tmp_path: Path) -> None:
    document = _document()
    document["balls"][1]["positions"] = [[0.0, 0.0, 0.0]] * 3
    with pytest.raises(ValueError, match="ball 1 has 3 positions but frame_count is 4"):
        read_truth(_write(tmp_path, document))


def test_reader_rejects_a_ball_id_outside_the_range(tmp_path: Path) -> None:
    document = _document()
    document["balls"][1]["id"] = 2
    with pytest.raises(ValueError, match=r"has id 2, outside 0\.\.1"):
        read_truth(_write(tmp_path, document))


def test_reader_rejects_a_duplicate_ball_id(tmp_path: Path) -> None:
    document = _document()
    document["balls"][1]["id"] = 0
    with pytest.raises(ValueError, match="ball id 0 appears more than once"):
        read_truth(_write(tmp_path, document))


def test_reader_rejects_a_non_finite_coordinate(tmp_path: Path) -> None:
    document = _document()
    document["balls"][0]["positions"][2] = [0.0, 0.0, None]
    with pytest.raises(ValueError, match=r"non-numeric position sample|non-finite"):
        read_truth(_write(tmp_path, document))
    document = _document()
    document["balls"][0]["positions"][2] = [0.0, 0.0, 1e400]  # JSON Infinity
    with pytest.raises(ValueError, match="non-finite coordinate at frame 3"):
        read_truth(_write(tmp_path, document))


def test_reader_rejects_a_ragged_position_sample(tmp_path: Path) -> None:
    document = _document()
    document["balls"][0]["positions"][1] = [0.0, 0.0]
    with pytest.raises(ValueError, match=r"non-numeric position sample|must be"):
        read_truth(_write(tmp_path, document))


def test_reader_rejects_an_event_frame_outside_the_recording(tmp_path: Path) -> None:
    document = _document()
    document["events"][0]["frame"] = 5
    with pytest.raises(ValueError, match=r"at frame 5, outside 1\.\.4"):
        read_truth(_write(tmp_path, document))
    document = _document()
    document["events"][0]["frame"] = 0
    with pytest.raises(ValueError, match="'frame' must be at least 1"):
        read_truth(_write(tmp_path, document))


def test_reader_rejects_an_unknown_event_kind(tmp_path: Path) -> None:
    document = _document()
    document["events"][0]["kind"] = "collision"
    with pytest.raises(ValueError, match="unknown kind 'collision'"):
        read_truth(_write(tmp_path, document))


def test_reader_rejects_a_throw_without_a_value(tmp_path: Path) -> None:
    document = _document()
    del document["events"][0]["throw_value"]
    with pytest.raises(ValueError, match="is a throw of ball 0 at frame 1 with no throw_value"):
        read_truth(_write(tmp_path, document))


def test_reader_rejects_an_event_naming_a_ball_that_does_not_exist(tmp_path: Path) -> None:
    document = _document()
    document["events"][0]["ball"] = 2
    with pytest.raises(ValueError, match=r"names ball 2, outside 0\.\.1"):
        read_truth(_write(tmp_path, document))


def test_reader_rejects_a_non_object_document(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        read_truth(path)


def test_average_theorem_is_data_not_an_error(tmp_path: Path) -> None:
    """A `mean(h) != b` mismatch is a finding about the generator, never a raise."""
    document = _document()
    document["events"][0]["throw_value"] = 7
    result = read_truth_document(_write(tmp_path, document)).average_theorem
    assert result.ball_count == 2
    assert result.n_throws == 1
    assert result.mean_throw_value == pytest.approx(7.0)
    assert result.error == pytest.approx(5.0)
    assert not result.holds()


def test_frame_mismatch_is_data_not_an_error(tmp_path: Path) -> None:
    document = _document()
    document["events"][0]["frame"] = 4  # t = 0.0 is frame 1
    result = read_truth_document(_write(tmp_path, document))
    assert not result.frames_consistent
    assert len(result.frame_mismatches) == 1
    assert "file says frame 4" in result.frame_mismatches[0]
    assert "is 1" in result.frame_mismatches[0]


# -- the committed Airtime fixtures ----------------------------------------- #

TRUTH_FIXTURES = sorted((data_dir() / "truth").glob("*.json"))


def _fixture(name: str) -> Path:
    path = data_dir() / "truth" / name
    if not path.exists():
        pytest.skip(f"truth fixture {name} not present in {data_dir() / 'truth'}")
    return path


@pytest.mark.parametrize("path", TRUTH_FIXTURES, ids=lambda p: p.stem)
def test_committed_fixtures_read_and_are_self_consistent(path: Path) -> None:
    """Every fixture parses, and its `frame` matches its `time` exactly.

    The frame cross-check is genuine: Airtime computes the frame in JavaScript with
    round-half-up and about a fifth of the events land exactly halfway between
    samples, so an off-by-one rounding rule would show up here immediately.
    """
    document = read_truth_document(path)
    truth = document.truth
    assert truth.f_s == 300.0
    assert truth.frame_count == 3000
    assert truth.gravity == pytest.approx(GRAVITY)
    assert truth.positions.shape == (truth.ball_count, 3000, 3)
    assert document.generator.repo == "airtime"
    assert document.generator.commit
    assert document.frames_consistent, document.frame_mismatches


def test_fixture_average_theorem_holds_for_the_plain_cascades() -> None:
    for name in ("3.json", "4.json", "5.json", "7.json"):
        result = read_truth_document(_fixture(name)).average_theorem
        assert result.holds(1e-9), f"{name}: mean(h) = {result.mean_throw_value}"


def test_fixture_average_theorem_reports_the_held_two_as_a_finding() -> None:
    """`552` and `423` hold a `2`, which emits no throw event, so `mean(h) != b`.

    Not a bug in the reader or the generator: a `2` rides the hand with no flight,
    so it is absent from the event list and the mean is taken over the throws that
    *are* recorded. Exactly why the check is exposed as data (DESIGN.md §12).
    """
    held = read_truth_document(_fixture("552.json")).average_theorem
    assert held.ball_count == 4
    assert held.n_throws == 27
    assert held.mean_throw_value == pytest.approx(5.0)
    assert not held.holds()

    partial = read_truth_document(_fixture("423.json")).average_theorem
    assert partial.mean_throw_value == pytest.approx(3.5185185, abs=1e-6)
    assert not partial.holds()


def test_fixtures_degrade_end_to_end() -> None:
    """Every committed fixture survives the degrader and the P1 model."""
    for path in TRUTH_FIXTURES:
        truth = read_truth(path)
        session, key = degrade(truth, np.random.default_rng(3), CLEAN_PRESET)
        assert session.frame == "juggling"
        assert session.frame_count == truth.frame_count
        assert session.n_trajectories >= truth.ball_count
        _assert_model_invariants(session, key.ball_of_trajectory.keys())


# --------------------------------------------------------------------------- #
# 2. the degradation model
# --------------------------------------------------------------------------- #


def _assert_model_invariants(session: Session, expected_ids: Any) -> None:
    """Every emitted trajectory is a valid P1 `Trajectory` (PLAN.md P3)."""
    assert {t.id for t in session.trajectories} == set(expected_ids)
    for trajectory in session.trajectories:
        n = trajectory.n_samples
        assert n >= 1
        assert trajectory.frames.dtype == np.int64
        assert trajectory.first_frame >= 1, "frames are 1-based"
        assert trajectory.last_frame <= session.frame_count
        assert np.all(np.diff(trajectory.frames) > 0), "frames must be strictly increasing"
        assert trajectory.positions.shape == (n, 3)
        assert np.all(np.isfinite(trajectory.positions))
        sigma = trajectory.uncertainty.sigma()
        assert sigma.shape == (n,)
        assert np.all(np.isfinite(sigma))
        assert np.all(sigma > 0.0)
        # `pieces` must account for every sample and match the actual gaps.
        assert sum(piece.length for piece in trajectory.pieces) == n
        assert trajectory.pieces[0].start_frame == trajectory.first_frame
        assert trajectory.pieces[-1].end_frame == trajectory.last_frame
        assert len(trajectory.gaps()) == len(trajectory.pieces) - 1
        assert np.all(trajectory.sample_type == 1), "nothing is gap-filled"
        assert trajectory.kind == "unknown", "classification is core.clean's job"


def test_degrade_is_deterministic_for_one_seed() -> None:
    truth = cascade_truth(5, 1200, beat_period=0.2)
    first, first_key = degrade(truth, np.random.default_rng(11), CLEAN_PRESET)
    second, second_key = degrade(truth, np.random.default_rng(11), CLEAN_PRESET)

    assert [t.id for t in first.trajectories] == [t.id for t in second.trajectories]
    for a, b in zip(first.trajectories, second.trajectories, strict=True):
        assert np.array_equal(a.frames, b.frames)
        assert np.array_equal(a.positions, b.positions)
        assert np.array_equal(a.uncertainty.values, b.uncertainty.values)
        assert a.pieces == b.pieces
    assert dict(first_key.ball_of_trajectory) == dict(second_key.ball_of_trajectory)
    assert first_key.spurious_ids == second_key.spurious_ids
    assert first_key.swapped_ids == second_key.swapped_ids
    assert dict(first_key.introduced_gaps) == dict(second_key.introduced_gaps)


def test_degrade_differs_between_seeds() -> None:
    truth = cascade_truth(5, 1200, beat_period=0.2)
    first, _ = degrade(truth, np.random.default_rng(11), CLEAN_PRESET)
    other, _ = degrade(truth, np.random.default_rng(12), CLEAN_PRESET)
    same = first.trajectories[0].positions
    different = other.trajectories[0].positions
    if same.shape == different.shape:
        assert not np.array_equal(same, different)
    assert not np.array_equal(
        np.concatenate([t.uncertainty.sigma() for t in first.trajectories]),
        np.concatenate([t.uncertainty.sigma() for t in other.trajectories]),
    )


def test_degrade_round_trips_through_the_phase_one_model() -> None:
    truth = cascade_truth(5, 2400, beat_period=0.2)
    session, key = degrade(truth, np.random.default_rng(5), NOISY_PRESET)
    _assert_model_invariants(session, key.ball_of_trajectory.keys())
    assert session.f_s == truth.f_s
    assert session.source == "synth:5"


def test_answer_key_is_complete_and_consistent() -> None:
    truth = cascade_truth(5, 2400, beat_period=0.2)
    session, key = degrade(truth, np.random.default_rng(7), NOISY_PRESET)
    ids = {t.id for t in session.trajectories}
    assert set(key.ball_of_trajectory) == ids, "every emitted trajectory needs an entry"
    assert key.spurious_ids <= ids
    assert key.swapped_ids <= ids
    assert not (key.spurious_ids & key.swapped_ids)
    assert key.n_trajectories == len(ids)
    for identifier in ids:
        ball = key.true_ball(identifier)
        if identifier in key.spurious_ids:
            assert ball == -1
        else:
            assert 0 <= ball < truth.ball_count
    with pytest.raises(KeyError, match="no answer-key entry"):
        key.true_ball("not-a-trajectory")
    # Gaps are recorded per ball, inside the recording, and in order.
    assert set(key.introduced_gaps) == set(range(truth.ball_count))
    for ball, gaps in key.introduced_gaps.items():
        for first, last in gaps:
            assert 1 <= first <= last <= truth.frame_count
        assert list(gaps) == sorted(gaps)
        assert key.missing_frames(ball) == sum(last - first + 1 for first, last in gaps)
    assert 0.0 < key.coverage(truth.frame_count) <= 1.0


def test_answer_key_ball_trajectories_tile_without_overlap() -> None:
    """One ball's trajectories may not claim the same frame twice."""
    truth = cascade_truth(5, 2400, beat_period=0.2)
    session, key = degrade(truth, np.random.default_rng(2), NOISY_PRESET)
    by_id = {t.id: t for t in session.trajectories}
    for ball in range(truth.ball_count):
        claimed: list[int] = []
        for identifier in key.ids_of_ball(ball):
            if identifier in key.swapped_ids:
                continue  # a swapped trajectory legitimately holds another ball too
            claimed.extend(int(f) for f in by_id[identifier].frames)
        assert len(claimed) == len(set(claimed)), f"ball {ball} claimed a frame twice"


def test_truth_is_preserved_for_unswapped_ball_trajectories() -> None:
    """Every sample of a clean trajectory sits within a few σ of its true ball.

    The bound is in *true* σ (`sigma_report_factor` × the reported σ), plus the
    modelled forward spread, which is a deliberate constant per-ball offset in `y`
    and is far larger than σ by design — see `DegradationParams.forward_spread`.
    """
    truth = cascade_truth(5, 2400, beat_period=0.2)
    params = NOISY_PRESET
    session, key = degrade(truth, np.random.default_rng(4), params)
    worst = 0.0
    checked = 0
    for trajectory in session.trajectories:
        if trajectory.id in key.spurious_ids or trajectory.id in key.swapped_ids:
            continue
        ball = key.true_ball(trajectory.id)
        expected = truth.positions[ball, trajectory.frames - 1]
        deviation = np.linalg.norm(trajectory.positions - expected, axis=1)
        allowed = (
            6.0 * params.sigma_report_factor * trajectory.uncertainty.sigma()
            + 5.0 * params.forward_spread
        )
        worst = max(worst, float(np.max(deviation / allowed)))
        assert np.all(deviation <= allowed), (
            f"{trajectory.id}: worst deviation {np.max(deviation) * 1e3:.2f} mm "
            f"against an allowance of {np.max(allowed) * 1e3:.2f} mm"
        )
        checked += trajectory.n_samples
    assert checked > 0
    assert worst < 1.0, f"tightest margin used {worst:.2f} of the allowance"


def test_swapped_trajectories_really_hold_two_balls() -> None:
    truth = cascade_truth(5, 2400, beat_period=0.2)
    session, key = degrade(truth, np.random.default_rng(4), NOISY_PRESET)
    if not key.swapped_ids:
        pytest.skip("this seed produced no swap")
    by_id = {t.id: t for t in session.trajectories}
    for identifier in key.swapped_ids:
        trajectory = by_id[identifier]
        majority = key.true_ball(identifier)
        expected = truth.positions[majority, trajectory.frames - 1]
        deviation = np.linalg.norm(trajectory.positions - expected, axis=1)
        # The majority ball explains part of the trajectory and not the rest.
        assert float(np.max(deviation)) > 0.05, (
            "a swapped trajectory must contain samples the majority ball cannot explain"
        )


# -- each degradation, isolated -------------------------------------------- #

#: Every degradation off. Individual tests switch exactly one back on, so a test
#: that passes cannot be passing because of a different mechanism.
INERT = DegradationParams(
    apex_dropout_probability=0.0,
    crossing_dropout_probability=0.0,
    background_dropout_rate=0.0,
    swap_probability=0.0,
    spurious_rate=0.0,
    forward_spread=0.0,
)


def test_noise_only_changes_positions_but_not_the_trajectory_count() -> None:
    truth = cascade_truth(5, 1500, beat_period=0.2)
    session, key = degrade(truth, np.random.default_rng(1), INERT)
    assert session.n_trajectories == truth.ball_count
    assert not key.spurious_ids
    assert not key.swapped_ids
    assert all(not gaps for gaps in key.introduced_gaps.values())
    assert key.coverage(truth.frame_count) == 1.0
    for trajectory in session.trajectories:
        assert trajectory.n_samples == truth.frame_count
        assert trajectory.is_contiguous
        ball = key.true_ball(trajectory.id)
        assert not np.array_equal(trajectory.positions, truth.positions[ball])
        residual = trajectory.positions - truth.positions[ball]
        assert np.all(np.abs(residual) > 0.0), "every sample is perturbed"


def test_reported_sigma_deliberately_understates_the_true_error() -> None:
    """The stored σ is optimistic by construction, matching QTM (BUILD_LOG P2)."""
    truth = cascade_truth(5, 3000, beat_period=0.2)
    params = DegradationParams(
        apex_dropout_probability=0.0,
        crossing_dropout_probability=0.0,
        swap_probability=0.0,
        spurious_rate=0.0,
        forward_spread=0.0,
        # White only, so the injected error is directly comparable to σ sample
        # by sample without the smooth component's correlation getting in the way.
        white_error_fraction=1.0,
        error_sigma_coupling=1.0,
        sigma_sample_log_spread=0.0,
        sigma_base_log_spread=0.0,
    )
    session, key = degrade(truth, np.random.default_rng(9), params)
    ratios = []
    for trajectory in session.trajectories:
        ball = key.true_ball(trajectory.id)
        error = trajectory.positions - truth.positions[ball, trajectory.frames - 1]
        reported = trajectory.uncertainty.sigma()
        ratios.append(float(np.std(error) / np.mean(reported)))
    achieved = float(np.mean(ratios))
    assert achieved == pytest.approx(params.sigma_report_factor, rel=0.05), (
        f"true error is {achieved:.2f}× the reported σ, expected {params.sigma_report_factor}×"
    )


def test_dropout_only_fragments_and_leaves_gaps() -> None:
    truth = cascade_truth(5, 3000, beat_period=0.2)
    params = DegradationParams(
        apex_dropout_probability=0.5,
        crossing_dropout_probability=0.0,
        background_dropout_rate=0.0,
        swap_probability=0.0,
        spurious_rate=0.0,
        forward_spread=0.0,
        internal_gap_fraction=0.0,
    )
    session, key = degrade(truth, np.random.default_rng(1), params)
    assert session.n_trajectories > truth.ball_count, "dropouts must fragment"
    assert not key.swapped_ids
    assert key.coverage(truth.frame_count) < 1.0
    assert any(gaps for gaps in key.introduced_gaps.values())
    # Splits only, so no trajectory has an internal hole.
    assert all(t.is_contiguous for t in session.trajectories)


def test_dropouts_concentrate_at_apexes() -> None:
    """The losses land near flight apexes, not uniformly (DESIGN.md §12)."""
    truth = cascade_truth(5, 4967, beat_period=0.2)
    params = DegradationParams(
        apex_dropout_probability=0.6,
        crossing_dropout_probability=0.0,
        swap_probability=0.0,
        spurious_rate=0.0,
        forward_spread=0.0,
    )
    _session, key = degrade(truth, np.random.default_rng(1), params)
    apex_frames = np.array([e.frame for e in truth.apexes()])
    centres = [
        (first + last) / 2.0
        for gaps in key.introduced_gaps.values()
        for first, last in gaps
        if first > 1 and last < truth.frame_count
    ]
    assert len(centres) > 10
    distance = np.array([np.min(np.abs(apex_frames - c)) for c in centres])
    # Apex jitter is ±0.06 s = ±18 frames; a uniform placement in a 4967-frame
    # clip with 81 apexes would average ~15 frames, so require better than that.
    median = float(np.median(distance))
    assert median <= 12.0, f"median distance from a gap centre to an apex is {median:.1f} frames"


def test_internal_gaps_are_modelled_as_well_as_splits() -> None:
    """A dropout can leave a hole inside one trajectory — QTM's *Mixed*."""
    truth = cascade_truth(5, 3000, beat_period=0.2)
    params = DegradationParams(
        apex_dropout_probability=0.5,
        crossing_dropout_probability=0.0,
        swap_probability=0.0,
        spurious_rate=0.0,
        forward_spread=0.0,
        internal_gap_fraction=1.0,
    )
    session, key = degrade(truth, np.random.default_rng(1), params)
    assert session.n_trajectories == truth.ball_count, "internal gaps must not split"
    mixed = [t for t in session.trajectories if not t.is_contiguous]
    assert mixed, "no internal gap was produced"
    for trajectory in mixed:
        assert len(trajectory.pieces) > 1
        assert sum(p.length for p in trajectory.pieces) == trajectory.n_samples
    assert key.coverage(truth.frame_count) < 1.0


def test_swap_only_produces_a_trajectory_from_two_balls() -> None:
    truth = cascade_truth(5, 3000, beat_period=0.2)
    params = DegradationParams(
        apex_dropout_probability=0.0,
        crossing_dropout_probability=0.0,
        background_dropout_rate=0.0,
        swap_probability=1.0,
        spurious_rate=0.0,
        forward_spread=0.0,
    )
    session, key = degrade(truth, np.random.default_rng(1), params)
    assert session.n_trajectories == truth.ball_count, "a swap does not fragment"
    assert key.swapped_ids, "no swap happened"
    assert key.coverage(truth.frame_count) == 1.0, "a swap loses no frames"

    by_id = {t.id: t for t in session.trajectories}
    identifier = sorted(key.swapped_ids)[0]
    trajectory = by_id[identifier]
    # Reconstruct which ball explains each sample; more than one must.
    explains = np.full(trajectory.n_samples, -1)
    for ball in range(truth.ball_count):
        expected = truth.positions[ball, trajectory.frames - 1]
        close = np.linalg.norm(trajectory.positions - expected, axis=1) < 0.02
        explains[close] = ball
    assert len(set(explains.tolist()) - {-1}) >= 2, "samples must come from two balls"


def test_swaps_need_two_balls_close_together() -> None:
    """No crossing within `swap_distance` means no swap, however high the odds."""
    truth = cascade_truth(5, 3000, beat_period=0.2)
    params = DegradationParams(
        apex_dropout_probability=0.0,
        crossing_dropout_probability=0.0,
        swap_probability=1.0,
        spurious_rate=0.0,
        forward_spread=0.0,
        swap_distance=1e-6,
    )
    _session, key = degrade(truth, np.random.default_rng(1), params)
    assert not key.swapped_ids


def test_spurious_only_adds_short_non_ballistic_trajectories() -> None:
    truth = cascade_truth(5, 3000, beat_period=0.2)
    params = DegradationParams(
        apex_dropout_probability=0.0,
        crossing_dropout_probability=0.0,
        background_dropout_rate=0.0,
        swap_probability=0.0,
        spurious_rate=4.0,
        forward_spread=0.0,
    )
    session, key = degrade(truth, np.random.default_rng(1), params)
    assert key.spurious_ids, "no reflection was produced"
    assert session.n_trajectories == truth.ball_count + len(key.spurious_ids)
    assert key.coverage(truth.frame_count) == 1.0

    by_id = {t.id: t for t in session.trajectories}
    for identifier in sorted(key.spurious_ids):
        trajectory = by_id[identifier]
        assert params.spurious_min_samples <= trajectory.n_samples <= params.spurious_max_samples
        assert classify_trajectory(trajectory) == "spurious"
        flights, _carries = segment_trajectory(trajectory, truth.f_s)
        assert not flights, "a reflection must not look ballistic"
        # Anchored near a real ball, at a distance inside the modelled band: it has
        # to be close enough for the linker to have to consider it.
        first = trajectory.frames[0] - 1
        distances = [
            float(np.linalg.norm(trajectory.positions[0] - truth.positions[b, first]))
            for b in range(truth.ball_count)
        ]
        assert any(
            params.spurious_offset_min * 0.9 <= d <= params.spurious_offset_max * 1.1
            for d in distances
        ), (
            f"{identifier} sits {min(distances) * 1e3:.0f}-{max(distances) * 1e3:.0f} mm "
            "from the nearest and furthest ball"
        )


def test_reflections_survive_the_full_clean_pass() -> None:
    """`core.clean` must reject every reflection and keep the real balls."""
    truth = cascade_truth(5, 3000, beat_period=0.2)
    session, key = degrade(truth, np.random.default_rng(3), CLEAN_PRESET)
    classified, _report = classify_session(session)
    segmentation = segment_session(classified, calibrate=False)
    refined, report = refine_with_flights(classified, segmentation.flights)
    kinds = {t.id: t.kind for t in refined.trajectories}
    assert all(kinds[i] != "ball" for i in key.spurious_ids), report
    real = [i for i in key.ball_of_trajectory if i not in key.spurious_ids]
    assert sum(kinds[i] == "ball" for i in real) >= truth.ball_count


def test_crossing_events_are_one_per_closest_approach() -> None:
    positions = np.zeros((2, 100, 3))
    positions[0, :, 0] = np.linspace(-1.0, 1.0, 100)
    positions[1, :, 0] = np.linspace(1.0, -1.0, 100)
    crossings = crossing_events(positions, 0.2)
    assert len(crossings) == 1
    assert crossings[0].ball_a == 0
    assert crossings[0].ball_b == 1
    assert crossings[0].distance < 0.05
    assert crossing_events(positions, 1e-9) == ()
    with pytest.raises(ValueError, match="must be positive"):
        crossing_events(positions, 0.0)
    with pytest.raises(ValueError, match=r"must be \(n_balls, n_frames, 3\)"):
        crossing_events(np.zeros((2, 100)), 0.2)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"sigma_base_median": 0.0}, "sigma_base_median must be positive"),
        ({"sigma_base_log_spread": -0.1}, "must be non-negative"),
        ({"swap_probability": 1.5}, "must be a probability"),
        ({"error_sigma_coupling": -0.1}, "must be a probability"),
        ({"sigma_ceiling": 1e-9}, "below sigma_floor"),
        ({"min_trajectory_samples": 0}, "min_trajectory_samples must be positive"),
        ({"spurious_min_samples": 0}, "spurious_min_samples must be positive"),
        ({"spurious_max_samples": 1}, "below spurious_min_samples"),
        ({"spurious_offset_max": 0.0}, "below spurious_offset_min"),
    ],
)
def test_params_validate_their_knobs(kwargs: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        DegradationParams(**kwargs)


# --------------------------------------------------------------------------- #
# 3. calibration against the real clips (PLAN.md P3)
# --------------------------------------------------------------------------- #

#: Seeds the calibration is measured over. Every statistic is compared as its
#: *median across seeds*: the trajectory-count and minimum-length rows are extreme
#: order statistics over a handful of trajectories, so a single draw of either is
#: dominated by chance and pinning one seed would be fishing rather than measuring.
CALIBRATION_SEEDS = (0, 1, 2, 3, 4)

#: Measured on `5_ball_juggling_cut_balls_only.qtm` (4967 frames, 5 balls):
#: 10 ball trajectories, lengths median 2422 / min 285, 24 452 of 24 835 ball-frames
#: covered, no internal gaps, reported σ median 0.441 / p90 1.655 / max 5.743 mm.
CLEAN_TARGETS = {
    "trajectories_per_ball": 10 / 5,
    "length_median": 2422.0,
    "length_min": 285.0,
    "coverage_percent": 100.0 * 24452 / 24835,
    "sigma_median_mm": 0.441,
    "sigma_p90_mm": 1.655,
    "sigma_max_mm": 5.743,
}

#: Measured on `3_ball_juggling_cut.qtm` (9101 frames, 3 balls): 19 ball
#: trajectories, lengths median 1015 / min 166, 25 013 of 27 303 ball-frames
#: covered, 5 internal gaps of median 16 and max 27 samples, reported σ median
#: 0.559 / p90 2.414 / max 5.890 mm.
NOISY_TARGETS = {
    "trajectories_per_ball": 19 / 3,
    "length_median": 1015.0,
    "length_min": 166.0,
    "coverage_percent": 100.0 * 25013 / 27303,
    "internal_gaps": 5.0,
    "internal_gap_median": 16.0,
    "internal_gap_max": 27.0,
    "sigma_median_mm": 0.559,
    "sigma_p90_mm": 2.414,
    "sigma_max_mm": 5.890,
}


def _calibration_statistics(truth: Truth, params: DegradationParams) -> dict[str, float]:
    """The corpus statistics of a degraded clip, median over `CALIBRATION_SEEDS`.

    Measured over the answer key's **ball** trajectories, which is the population
    the real numbers were measured over (`core.clean`'s `ball` bucket on the real
    clips), so the two sides are like for like.
    """
    rows: list[dict[str, float]] = []
    for seed in CALIBRATION_SEEDS:
        session, key = degrade(truth, np.random.default_rng(seed), params)
        by_id = {t.id: t for t in session.trajectories}
        balls = [by_id[i] for i in key.ball_of_trajectory if i not in key.spurious_ids]
        lengths = np.array([t.n_samples for t in balls], dtype=np.float64)
        gaps = np.array(
            [last - first + 1 for t in balls for first, last in t.gaps()], dtype=np.float64
        )
        sigma = np.concatenate([t.uncertainty.sigma() for t in balls]) * 1e3
        rows.append(
            {
                "trajectories_per_ball": len(balls) / truth.ball_count,
                "length_median": float(np.median(lengths)),
                "length_min": float(lengths.min()),
                "coverage_percent": 100.0
                * float(lengths.sum())
                / (truth.ball_count * truth.frame_count),
                "internal_gaps": float(gaps.size),
                "internal_gap_median": float(np.median(gaps)) if gaps.size else 0.0,
                "internal_gap_max": float(gaps.max()) if gaps.size else 0.0,
                "sigma_median_mm": float(np.median(sigma)),
                "sigma_p90_mm": float(np.percentile(sigma, 90)),
                "sigma_max_mm": float(sigma.max()),
            }
        )
    return {key: float(np.median([row[key] for row in rows])) for key in rows[0]}


def _assert_within_factor_of_two(
    achieved: dict[str, float], targets: dict[str, float], label: str
) -> None:
    for name, target in targets.items():
        value = achieved[name]
        ratio = value / target
        assert 0.5 <= ratio <= 2.0, (
            f"{label} {name}: achieved {value:.3f} against a measured {target:.3f} "
            f"(ratio {ratio:.2f}, outside the factor of two PLAN.md P3 allows)"
        )


def test_clean_preset_reproduces_the_five_ball_clip() -> None:
    achieved = _calibration_statistics(clean_calibration_truth(), CLEAN_PRESET)
    _assert_within_factor_of_two(achieved, CLEAN_TARGETS, "CLEAN")
    # The 5-ball clip has no internal gaps at all, and the preset must not invent any.
    assert achieved["internal_gaps"] == 0.0, (
        f"CLEAN internal_gaps: achieved {achieved['internal_gaps']:.1f} against a measured 0"
    )


def test_noisy_preset_reproduces_the_three_ball_clip() -> None:
    achieved = _calibration_statistics(noisy_calibration_truth(), NOISY_PRESET)
    _assert_within_factor_of_two(achieved, NOISY_TARGETS, "NOISY")


def test_noisy_preset_is_actually_worse_than_clean() -> None:
    """The two presets must span the corpus's quality range, not duplicate it."""
    clean = _calibration_statistics(clean_calibration_truth(), CLEAN_PRESET)
    noisy = _calibration_statistics(noisy_calibration_truth(), NOISY_PRESET)
    assert noisy["trajectories_per_ball"] > clean["trajectories_per_ball"]
    assert noisy["coverage_percent"] < clean["coverage_percent"]
    assert noisy["sigma_median_mm"] > clean["sigma_median_mm"]
    assert noisy["internal_gaps"] > clean["internal_gaps"]


def test_flight_segmentation_survives_the_clean_preset() -> None:
    """Degraded data must still be *analysable*: realistic, not destructive.

    Synthetic truth obeys `g` exactly, so this is a genuine end-to-end check —
    `calibrate=False` because there is no instrument offset to measure here, and
    letting the two-pass calibration absorb one would make the check circular.
    """
    truth = clean_calibration_truth()
    session, key = degrade(truth, np.random.default_rng(0), CLEAN_PRESET)
    segmentation = segment_session(session, calibrate=False)
    real = {i for i in key.ball_of_trajectory if i not in key.spurious_ids}
    flights = [f for f in segmentation.flights if f.trajectory_id in real]
    expected = len(truth.throws())
    assert len(flights) >= expected * 0.5, (
        f"only {len(flights)} flights found against {expected} true throws"
    )
    check = check_gravity(tuple(flights))
    assert check.within <= 0.02, (
        f"fitted g = {check.median:.4f} m/s² over {check.n_flights} flights, "
        f"{check.relative_error * 100:+.2f}% from {GRAVITY} — outside 2%"
    )
    assert not any(f.is_suspect() for f in flights), "no flight should read as suspect"
