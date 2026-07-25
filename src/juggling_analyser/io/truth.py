"""Reader for an Airtime truth-export JSON (DESIGN.md §12 layer 2).

Airtime is the shared source of physics truth and the closed-form simulator, so it
— not a reimplementation here — generates the clean labelled data:
`pattern → exact per-ball positions sampled at f_s → every event`. This module
turns one of those exports into a :class:`~juggling_analyser.core.synth.Truth`,
which `core.synth.degrade` then degrades into realistic mocap. It lives in `io/`
because it touches the filesystem; nothing in `core/` may import it.

The schema, ``airtime-truth-export/1``::

    {"schema": "airtime-truth-export/1", "pattern": "531", "ball_count": 3,
     "hand_count": 2, "beat_period": 0.4, "dwell_ratio": 0.7,
     "gravity": 9.80665, "f_s": 300, "frame_count": 3000, "frame": "juggling",
     "generator": {"repo": "airtime", "commit": "...", "params": {}},
     "balls":  [{"id": 0, "positions": [[x, y, z], ...]}],
     "events": [{"kind": "throw", "ball": 0, "hand": "right", "frame": 1,
                 "time": 0.0, "position": [x, y, z], "throw_value": 5}]}

**Validation is strict and it raises.** A fixture is committed data that the whole
synthetic-truth layer rests on; a wrong `frame`, a short `positions` array or a
throw with no value would silently poison every downstream measurement, so each is
a ``ValueError`` naming exactly what is wrong and where.

**Two cross-checks are results, not errors** (CLAUDE.md rule 3 — a discrepancy is
reported, never snapped away):

* the **average theorem** `b = mean(h)`. It holds over a whole period, but a clip
  that starts and ends mid-cycle need not satisfy it: `423.json` reads 3.52 against
  3 balls over its 27 throws. That is a finding about the *window*, not a parse
  error, so it comes back as :class:`~juggling_analyser.core.synth.AverageTheorem`.
* the **frame ↔ time consistency** of every event. Airtime computes
  ``frame = floor(time · f_s + 1.5)`` — round half **up**, which Python's `round`
  is not — and records that formula in ``generator.params.frame_rounding``.
  Recomputing it from `time` should reproduce `frame` exactly; any event where it
  does not is listed in :attr:`TruthDocument.frame_mismatches`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from juggling_analyser.core.synth import EVENT_KINDS, AverageTheorem, Truth, TruthEvent

#: The only schema this reader accepts. A new schema gets a new constant and an
#: explicit branch, never a silent best-effort parse.
TRUTH_SCHEMA = "airtime-truth-export/1"

#: The only coordinate frame a truth export may be in. Everything downstream of
#: `core.frame` assumes X = left → right hand, Y = forward, Z = up
#: (NOTATION.md § Frames of reference); a `sim`-frame export would be silently
#: rotated wrong, so it is rejected rather than converted here.
TRUTH_FRAME = "juggling"

_AXES = 3

#: How many mismatching events :class:`TruthDocument` reports individually before
#: it summarises. The point is to name the problem, not to dump the file.
_MAX_REPORTED_MISMATCHES = 5


@dataclass(frozen=True)
class GeneratorInfo:
    """Provenance of a truth export — which simulator, at which commit.

    Kept out of :class:`~juggling_analyser.core.synth.Truth` because `core/` is
    source-agnostic (DESIGN.md §2): the analysis must not be able to condition on
    where its input came from. It belongs in the session JSON (§10) instead.
    """

    repo: str
    commit: str
    #: The simulator's own parameters, verbatim and unvalidated. Airtime records
    #: `values`, `dwell_time`, `effective_dwell_by_value`, `hands`,
    #: `frame_rounding` and more here; this reader reads none of it, so a new key
    #: appearing upstream cannot break ingestion.
    params: Mapping[str, Any]


@dataclass(frozen=True, eq=False)
class TruthDocument:
    """One truth file: the :class:`Truth` plus what checking it revealed.

    ``eq=False`` because :attr:`truth` holds numpy arrays.
    """

    source: str
    truth: Truth
    generator: GeneratorInfo
    #: `b` against `mean(h)` over the throws in the window — data, not a raise.
    average_theorem: AverageTheorem
    #: One line per event whose ``frame`` disagrees with ``floor(time·f_s + 1.5)``.
    #: Empty for a self-consistent export.
    frame_mismatches: tuple[str, ...]

    @property
    def frames_consistent(self) -> bool:
        """True when every event's ``frame`` matches its ``time``."""
        return not self.frame_mismatches


def read_truth(path: str | Path) -> Truth:
    """Read an Airtime truth export into a :class:`Truth`.

    Positions come back in metres in the juggling frame, times in seconds, frames
    absolute and 1-based. Raises ``ValueError`` on any malformed field; use
    :func:`read_truth_document` when the average-theorem and frame-consistency
    findings are wanted as well.
    """
    return read_truth_document(path).truth


def read_truth_document(path: str | Path) -> TruthDocument:
    """Read a truth export, its provenance, and the two cross-check results."""
    source = Path(path)
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{source.name}: truth export must be a JSON object, got {type(raw)}")
    document: Mapping[str, Any] = raw

    schema = document.get("schema")
    if schema != TRUTH_SCHEMA:
        raise ValueError(
            f"{source.name}: expected schema {TRUTH_SCHEMA!r}, got {schema!r} — this reader "
            "does not guess at other schema versions"
        )
    frame = _string(document, "frame", source.name)
    if frame != TRUTH_FRAME:
        raise ValueError(
            f"{source.name}: positions must be in the {TRUTH_FRAME!r} frame, got {frame!r}"
        )

    ball_count = _integer(document, "ball_count", source.name, minimum=1)
    frame_count = _integer(document, "frame_count", source.name, minimum=1)
    f_s = _number(document, "f_s", source.name, minimum=0.0, inclusive=False)
    truth = Truth(
        pattern=_string(document, "pattern", source.name),
        ball_count=ball_count,
        hand_count=_integer(document, "hand_count", source.name, minimum=1),
        beat_period=_number(document, "beat_period", source.name, minimum=0.0, inclusive=False),
        dwell_ratio=_number(document, "dwell_ratio", source.name, minimum=0.0),
        gravity=_number(document, "gravity", source.name, minimum=0.0, inclusive=False),
        f_s=f_s,
        frame_count=frame_count,
        positions=_positions(document, source.name, ball_count, frame_count),
        events=_events(document, source.name, ball_count, frame_count),
    )
    return TruthDocument(
        source=str(source),
        truth=truth,
        generator=_generator(document, source.name),
        average_theorem=truth.average_theorem(),
        frame_mismatches=_frame_mismatches(truth),
    )


# --------------------------------------------------------------------------- #
# field extraction — every failure names the file, the field and the value
# --------------------------------------------------------------------------- #


def _string(document: Mapping[str, Any], key: str, name: str) -> str:
    value = document.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{name}: {key!r} must be a string, got {value!r}")
    return value


def _integer(document: Mapping[str, Any], key: str, name: str, *, minimum: int) -> int:
    value = document.get(key)
    # `bool` is an `int` in Python and would sail through; JSON floats that happen
    # to be integral (300.0) are accepted because the exporter writes both forms.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: {key!r} must be a number, got {value!r}")
    if float(value) != int(value):
        raise ValueError(f"{name}: {key!r} must be a whole number, got {value!r}")
    if int(value) < minimum:
        raise ValueError(f"{name}: {key!r} must be at least {minimum}, got {value!r}")
    return int(value)


def _number(
    document: Mapping[str, Any],
    key: str,
    name: str,
    *,
    minimum: float,
    inclusive: bool = True,
) -> float:
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: {key!r} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name}: {key!r} must be finite, got {value!r}")
    if number < minimum or (not inclusive and number == minimum):
        bound = "greater than" if not inclusive else "at least"
        raise ValueError(f"{name}: {key!r} must be {bound} {minimum}, got {value!r}")
    return number


def _sequence(container: Mapping[str, Any], key: str, name: str, context: str) -> Sequence[Any]:
    value = container.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{name}: {context}{key!r} must be a list, got {value!r}")
    return value


def _vector(container: Mapping[str, Any], key: str, name: str, context: str) -> np.ndarray:
    raw = _sequence(container, key, name, context)
    if len(raw) != _AXES:
        raise ValueError(f"{name}: {context}{key!r} must have 3 coordinates, got {len(raw)}")
    point = np.empty(_AXES, dtype=np.float64)
    for axis, value in enumerate(raw):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name}: {context}{key!r}[{axis}] must be a number, got {value!r}")
        point[axis] = float(value)
    if not np.all(np.isfinite(point)):
        raise ValueError(f"{name}: {context}{key!r} has a non-finite coordinate: {list(raw)}")
    return point


def _positions(
    document: Mapping[str, Any], name: str, ball_count: int, frame_count: int
) -> np.ndarray:
    """``(ball_count, frame_count, 3)`` metres, with every ball id accounted for."""
    balls = _sequence(document, "balls", name, "")
    if len(balls) != ball_count:
        raise ValueError(f"{name}: ball_count is {ball_count} but 'balls' has {len(balls)} entries")
    positions = np.full((ball_count, frame_count, _AXES), np.nan, dtype=np.float64)
    seen: set[int] = set()
    for index, entry in enumerate(balls):
        if not isinstance(entry, dict):
            raise ValueError(f"{name}: balls[{index}] must be an object, got {entry!r}")
        identifier = _integer(entry, "id", name, minimum=0)
        if identifier >= ball_count:
            raise ValueError(
                f"{name}: balls[{index}] has id {identifier}, outside 0..{ball_count - 1}"
            )
        if identifier in seen:
            raise ValueError(f"{name}: ball id {identifier} appears more than once")
        seen.add(identifier)

        samples = _sequence(entry, "positions", name, f"balls[{index}].")
        if len(samples) != frame_count:
            raise ValueError(
                f"{name}: ball {identifier} has {len(samples)} positions but frame_count "
                f"is {frame_count}"
            )
        # Vectorised, then checked once: a per-sample Python loop over 3000 frames
        # × 7 balls is the difference between a fast test suite and a slow one, and
        # `np.asarray` on a ragged list raises rather than silently making objects.
        try:
            block = np.asarray(samples, dtype=np.float64)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"{name}: ball {identifier} has a non-numeric position sample ({error})"
            ) from error
        if block.shape != (frame_count, _AXES):
            raise ValueError(
                f"{name}: ball {identifier} positions must be ({frame_count}, 3), got {block.shape}"
            )
        if not np.all(np.isfinite(block)):
            bad = int(np.flatnonzero(~np.isfinite(block).all(axis=1))[0])
            raise ValueError(
                f"{name}: ball {identifier} has a non-finite coordinate at frame {bad + 1}"
            )
        positions[identifier] = block

    missing = sorted(set(range(ball_count)) - seen)
    if missing:
        raise ValueError(f"{name}: no positions for ball id(s) {missing}")
    return positions


def _events(
    document: Mapping[str, Any], name: str, ball_count: int, frame_count: int
) -> tuple[TruthEvent, ...]:
    raw = _sequence(document, "events", name, "")
    events: list[TruthEvent] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"{name}: events[{index}] must be an object, got {entry!r}")
        context = f"events[{index}]."
        kind = _string(entry, "kind", name)
        if kind not in EVENT_KINDS:
            raise ValueError(
                f"{name}: events[{index}] has unknown kind {kind!r}, expected one of "
                f"{sorted(EVENT_KINDS)}"
            )
        ball = _integer(entry, "ball", name, minimum=0)
        if ball >= ball_count:
            raise ValueError(
                f"{name}: events[{index}] names ball {ball}, outside 0..{ball_count - 1}"
            )
        frame = _integer(entry, "frame", name, minimum=1)
        if frame > frame_count:
            raise ValueError(
                f"{name}: events[{index}] is at frame {frame}, outside 1..{frame_count}"
            )
        throw_value = entry.get("throw_value")
        if kind == "throw" and throw_value is None:
            raise ValueError(
                f"{name}: events[{index}] is a throw of ball {ball} at frame {frame} "
                "with no throw_value"
            )
        if throw_value is not None:
            throw_value = _integer(entry, "throw_value", name, minimum=0)
        events.append(
            TruthEvent(
                kind=kind,
                ball=ball,
                hand=_string(entry, "hand", name),
                frame=frame,
                time=_number(entry, "time", name, minimum=-math.inf),
                position=_vector(entry, "position", name, context),
                throw_value=throw_value,
            )
        )
    return tuple(events)


def _generator(document: Mapping[str, Any], name: str) -> GeneratorInfo:
    raw = document.get("generator", {})
    if not isinstance(raw, dict):
        raise ValueError(f"{name}: 'generator' must be an object, got {raw!r}")
    params = raw.get("params", {})
    if not isinstance(params, dict):
        raise ValueError(f"{name}: 'generator.params' must be an object, got {params!r}")
    return GeneratorInfo(
        repo=str(raw.get("repo", "")),
        commit=str(raw.get("commit", "")),
        params=params,
    )


# --------------------------------------------------------------------------- #
# cross-checks reported as data
# --------------------------------------------------------------------------- #


def frame_of(time: float, f_s: float) -> int:
    """Absolute 1-based frame nearest ``time``, rounding half **up**.

    ``floor(t · f_s + 1.5)``, which is what Airtime's exporter computes in
    JavaScript and records in ``generator.params.frame_rounding``. Python's
    built-in `round` rounds half to *even*, and about a fifth of the events in the
    fixture set land exactly halfway between samples, so `round(t · f_s) + 1`
    disagrees on roughly a tenth of them. Using the wrong rule here would put a
    silent one-frame error into every event-detection accuracy measurement.
    """
    return math.floor(time * f_s + 1.5)


def _frame_mismatches(truth: Truth) -> tuple[str, ...]:
    """Events whose ``frame`` disagrees with ``frame_of(time, f_s)``."""
    lines: list[str] = []
    total = 0
    for index, event in enumerate(truth.events):
        expected = frame_of(event.time, truth.f_s)
        if expected == event.frame:
            continue
        total += 1
        if len(lines) < _MAX_REPORTED_MISMATCHES:
            lines.append(
                f"events[{index}] {event.kind} of ball {event.ball} at t={event.time!r}: "
                f"file says frame {event.frame}, floor(t·f_s + 1.5) is {expected}"
            )
    if total > len(lines):
        lines.append(f"... and {total - len(lines)} more of {len(truth.events)} events")
    return tuple(lines)
