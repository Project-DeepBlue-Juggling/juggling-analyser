"""The source-agnostic data model (DESIGN.md §3).

Everything downstream of ingestion consumes these types, never QTM structures, so
that a real-time stream or a 2D-video front end can populate the same pipeline.

Two conventions are load-bearing and easy to get wrong:

* **Positions are metres, times are seconds** (NOTATION.md § Conventions).
* **``frames`` holds absolute 1-based frame indices**, matching QTM and the
  ``.qtm`` piece tables, while numpy indices are 0-based. A variable holding a
  frame index is named ``frame``/``k``; an array index is ``i``/``idx``. They are
  never mixed in one variable.

There is no "unknown start frame": ``.qtm`` stores per-trajectory absolute frame
ranges and the reader parses them (DESIGN.md §4, ``docs/qtm-format.md``).

Positions are in the QTM frame immediately after reading and in the juggling
frame after ``core.frame`` has been applied (DESIGN.md §5). Which one a
:class:`Session` currently holds is recorded on the session itself, not inferred.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

import numpy as np

Form = Literal["isotropic", "diagonal", "full"]
Kind = Literal["ball", "spurious", "unknown"]
Frame = Literal["qtm", "juggling"]

_AXES = 3


@dataclass(frozen=True, eq=False)
class Uncertainty:
    """Per-sample position uncertainty, in whichever form the source provides.

    Mocap gives one σ per sample (``isotropic``, derived from QTM's residual).
    2D video will give a full covariance — precise in the image plane, vague in
    depth — so consumers only ever go through :meth:`cov` / :meth:`inv_cov` /
    :meth:`variances` and adding that source changes no consumer (DESIGN.md §3).

    Storing isotropic mocap data as one float per sample rather than nine matters:
    a 10-minute recording at 300 Hz is 180 000 samples per trajectory.

    ``eq=False`` because the payload is a numpy array: a generated ``__eq__``
    would return an array and silently poison any ``==`` comparison.
    """

    form: Form
    #: ``(N,)`` σ in metres, ``(N, 3)`` per-axis σ, or ``(N, 3, 3)`` covariance in m².
    values: np.ndarray

    def __post_init__(self) -> None:
        values = np.asarray(self.values, dtype=np.float64)
        object.__setattr__(self, "values", values)
        expected = {"isotropic": 1, "diagonal": 2, "full": 3}[self.form]
        if values.ndim != expected:
            raise ValueError(
                f"{self.form} uncertainty needs a {expected}-D array, got {values.ndim}-D"
            )
        if self.form == "diagonal" and values.shape[1] != _AXES:
            raise ValueError(f"diagonal uncertainty needs shape (N, 3), got {values.shape}")
        if self.form == "full" and values.shape[1:] != (_AXES, _AXES):
            raise ValueError(f"full uncertainty needs shape (N, 3, 3), got {values.shape}")
        if not np.all(np.isfinite(values)):
            raise ValueError("uncertainty contains non-finite values")
        if self.form != "full" and np.any(values < 0.0):
            raise ValueError("σ must be non-negative")

    # -- constructors ----------------------------------------------------- #

    @classmethod
    def isotropic(cls, sigma: np.ndarray) -> Uncertainty:
        """One σ per sample, in metres."""
        return cls("isotropic", np.asarray(sigma, dtype=np.float64))

    @classmethod
    def diagonal(cls, sigma: np.ndarray) -> Uncertainty:
        """Per-axis σ, ``(N, 3)`` in metres."""
        return cls("diagonal", np.asarray(sigma, dtype=np.float64))

    @classmethod
    def full(cls, covariance: np.ndarray) -> Uncertainty:
        """Full ``(N, 3, 3)`` covariance in m²."""
        return cls("full", np.asarray(covariance, dtype=np.float64))

    # -- access ----------------------------------------------------------- #

    def __len__(self) -> int:
        return int(self.values.shape[0])

    def variances(self) -> np.ndarray:
        """``(N, 3)`` per-axis variance in m² — the vectorised accessor.

        Equivalent to stacking ``diag(cov(i))``, but without a Python loop, which
        matters when weighting 300 Hz data sample by sample.
        """
        if self.form == "isotropic":
            return np.repeat((self.values**2)[:, None], _AXES, axis=1)
        if self.form == "diagonal":
            return np.asarray(self.values**2)
        return np.asarray(np.diagonal(self.values, axis1=1, axis2=2))

    def sigma(self) -> np.ndarray:
        """``(N,)`` scalar-equivalent 1σ in metres, for scalar weighting.

        For anisotropic forms this is the RMS of the per-axis σ, i.e.
        ``sqrt(trace(Σ)/3)`` — the isotropic uncertainty with the same total
        variance. Use :meth:`variances` or :meth:`inv_cov` when direction matters.
        """
        return np.asarray(np.sqrt(self.variances().mean(axis=1)))

    def cov(self, i: int) -> np.ndarray:
        """The ``(3, 3)`` covariance of sample ``i`` (a 0-based array index)."""
        if self.form == "full":
            return np.asarray(self.values[i], dtype=np.float64)
        return np.diag(self.variances()[i])

    def inv_cov(self, i: int) -> np.ndarray:
        """The inverse covariance (information matrix) of sample ``i``.

        Raises ``numpy.linalg.LinAlgError`` on a singular covariance rather than
        returning a pseudo-inverse: a zero-variance axis means the caller's
        uncertainty model is wrong, and hiding that would silently over-weight
        the sample.
        """
        return np.asarray(np.linalg.inv(self.cov(i)))

    # -- derivation ------------------------------------------------------- #

    def take(self, index: np.ndarray | slice) -> Uncertainty:
        """The uncertainty of a subset of samples, selected by array index."""
        return Uncertainty(self.form, self.values[index])

    def scaled(self, factor: float) -> Uncertainty:
        """Uncertainty inflated by ``factor`` (σ, not variance).

        Used when a value is inferred rather than measured — a gap bridged by the
        linker carries its own inflated uncertainty (DESIGN.md §7).
        """
        if factor < 0.0:
            raise ValueError(f"scale factor must be non-negative, got {factor}")
        power = 2.0 if self.form == "full" else 1.0
        return Uncertainty(self.form, self.values * factor**power)


@dataclass(frozen=True)
class Piece:
    """One contiguous run of samples inside a trajectory, as stored in the file.

    Frames are **1-based inclusive** (NOTATION.md § Conventions). ``sample_type``
    is QTM's per-sample type code: 1 measured, 2 gap-filled, 3 virtual, 4 edited.
    """

    start_frame: int
    end_frame: int
    sample_type: int

    def __post_init__(self) -> None:
        if self.start_frame < 1:
            raise ValueError(f"frames are 1-based; got start_frame={self.start_frame}")
        if self.end_frame < self.start_frame:
            raise ValueError(f"empty piece [{self.start_frame}, {self.end_frame}]")

    @property
    def length(self) -> int:
        """Number of samples, inclusive of both endpoints."""
        return self.end_frame - self.start_frame + 1


@dataclass(frozen=True, eq=False)
class Trajectory:
    """What the capture system tracked — a marker path, **not a ball**.

    One physical ball spans several trajectories, because tracking breaks at
    throw apexes and ball crossings; deciding which trajectory continues which
    ball is the linker's job (DESIGN.md §7), not the reader's.

    A trajectory may have internal gaps (QTM calls that *Mixed*), which is why
    ``frames`` is explicit rather than implied by a start offset.
    """

    #: Source identifier — the ``.qtm`` data-series id, as a string.
    id: str
    #: ``(N,)`` int64 absolute **1-based** frame index per sample, strictly increasing.
    frames: np.ndarray
    #: ``(N, 3)`` positions in metres.
    positions: np.ndarray
    uncertainty: Uncertainty
    #: ``(N,)`` uint8 QTM per-sample type code (1 measured, 2 gap-filled, ...).
    sample_type: np.ndarray
    #: The contiguous runs this trajectory is made of, in frame order.
    pieces: tuple[Piece, ...]
    kind: Kind = "unknown"
    #: Source label if the capture system had one; ``""`` when unlabelled.
    label: str = ""

    def __post_init__(self) -> None:
        frames = np.asarray(self.frames, dtype=np.int64)
        positions = np.asarray(self.positions, dtype=np.float64)
        sample_type = np.asarray(self.sample_type, dtype=np.uint8)
        object.__setattr__(self, "frames", frames)
        object.__setattr__(self, "positions", positions)
        object.__setattr__(self, "sample_type", sample_type)

        n = frames.shape[0]
        if positions.shape != (n, _AXES):
            raise ValueError(f"positions must be (N, 3) with N={n}, got {positions.shape}")
        if sample_type.shape != (n,):
            raise ValueError(f"sample_type must be (N,) with N={n}, got {sample_type.shape}")
        if len(self.uncertainty) != n:
            raise ValueError(
                f"uncertainty covers {len(self.uncertainty)} samples, positions have {n}"
            )
        if n and frames[0] < 1:
            raise ValueError(f"frames are 1-based; got {frames[0]}")
        if n > 1 and not np.all(np.diff(frames) > 0):
            raise ValueError("frames must be strictly increasing")
        piece_samples = sum(piece.length for piece in self.pieces)
        if piece_samples != n:
            raise ValueError(f"pieces cover {piece_samples} samples but the trajectory has {n}")

    @property
    def n_samples(self) -> int:
        return int(self.frames.shape[0])

    @property
    def first_frame(self) -> int:
        """First absolute frame index (1-based)."""
        return int(self.frames[0])

    @property
    def last_frame(self) -> int:
        """Last absolute frame index (1-based), inclusive."""
        return int(self.frames[-1])

    @property
    def frame_span(self) -> int:
        """Frames from first to last inclusive — ``n_samples`` plus any gaps."""
        return self.last_frame - self.first_frame + 1

    @property
    def is_contiguous(self) -> bool:
        """True when the trajectory has no internal gap (QTM *Measured*)."""
        return self.frame_span == self.n_samples

    @property
    def has_gap_filled_samples(self) -> bool:
        """True when any sample was gap-filled rather than measured."""
        return bool(np.any(self.sample_type == 2))

    def gaps(self) -> tuple[tuple[int, int], ...]:
        """Internal gaps as ``(first_missing_frame, last_missing_frame)`` pairs."""
        if len(self.pieces) < 2:
            return ()
        return tuple(
            (before.end_frame + 1, after.start_frame - 1)
            # Deliberately offset by one, so the lengths differ: not `strict`.
            for before, after in zip(self.pieces, self.pieces[1:], strict=False)
            if after.start_frame > before.end_frame + 1
        )

    def times(self, f_s: float) -> np.ndarray:
        """Sample times in seconds. ``t = (k - 1) / f_s``, so frame 1 is ``t = 0``."""
        return (self.frames - 1).astype(np.float64) / f_s

    def active_at(self, frame: int) -> bool:
        """True when this trajectory has a sample at absolute frame ``frame``."""
        idx = int(np.searchsorted(self.frames, frame))
        return idx < self.n_samples and int(self.frames[idx]) == frame

    @property
    def height_span(self) -> float:
        """Vertical extent in metres. Z is up in both frames (NOTATION.md)."""
        if not self.n_samples:
            return 0.0
        z = self.positions[:, 2]
        return float(z.max() - z.min())

    def bounds(self) -> tuple[np.ndarray, np.ndarray] | None:
        """Axis-aligned ``(min, max)`` of the positions, or ``None`` if empty."""
        if not self.n_samples:
            return None
        return self.positions.min(axis=0), self.positions.max(axis=0)

    def with_kind(self, kind: Kind) -> Trajectory:
        """A copy classified as ``kind`` (the model is immutable)."""
        return replace(self, kind=kind)

    def with_positions(self, positions: np.ndarray) -> Trajectory:
        """A copy with transformed positions — used by the frame transform (§5)."""
        return replace(self, positions=positions)


@dataclass(frozen=True, eq=False)
class Session:
    """One recording: metadata plus everything the pipeline has derived from it.

    Later phases add balls, events, runs and metrics as separate fields; nothing
    here is a placeholder for work that has not been done, so a missing field
    means "not computed", never "computed as empty".
    """

    source: str
    #: Capture sample rate `f_s` in Hz.
    f_s: float
    #: Total frames in the recording, 1-based inclusive, from the file header.
    frame_count: int
    trajectories: tuple[Trajectory, ...] = ()
    #: Which frame ``trajectories`` positions are currently expressed in (§5).
    frame: Frame = "qtm"
    #: Source series that decoded but were not exported as marker trajectories,
    #: kept as a count so `info` can report what was skipped rather than hiding it.
    skipped_series: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.f_s <= 0.0:
            raise ValueError(f"f_s must be positive, got {self.f_s}")
        if self.frame_count < 0:
            raise ValueError(f"frame_count must be non-negative, got {self.frame_count}")

    @property
    def duration(self) -> float:
        """Recording length in seconds. Frame 1 is ``t = 0``, so this is ``N/f_s``."""
        return self.frame_count / self.f_s

    @property
    def n_trajectories(self) -> int:
        return len(self.trajectories)

    def of_kind(self, kind: Kind) -> tuple[Trajectory, ...]:
        return tuple(t for t in self.trajectories if t.kind == kind)

    @property
    def balls(self) -> tuple[Trajectory, ...]:
        """Trajectories classified as ball paths (``core.clean``)."""
        return self.of_kind("ball")

    def active_at(self, frame: int) -> tuple[Trajectory, ...]:
        """Trajectories with a sample at absolute frame ``frame`` (1-based)."""
        return tuple(t for t in self.trajectories if t.active_at(frame))

    def with_trajectories(self, trajectories: tuple[Trajectory, ...]) -> Session:
        return replace(self, trajectories=trajectories)

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for t in self.trajectories:
            counts[t.kind] = counts.get(t.kind, 0) + 1
        parts = ", ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "none"
        mixed = sum(1 for t in self.trajectories if not t.is_contiguous)
        return (
            f"{self.source}\n"
            f"  {self.f_s:g} Hz, {self.duration:.1f} s ({self.frame_count} frames), "
            f"{self.frame} frame\n"
            f"  {self.n_trajectories} trajectories ({mixed} with internal gaps): {parts}"
        )
