"""Source-agnostic trajectory data model.

Everything downstream of ingestion consumes these types, never QTM structures, so
a real-time stream or (later) a video front-end can populate the same model. All
positions are in metres and all times in seconds; the vertical axis is +Z.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

GRAVITY = 9.80665  # m/s^2

# QTM per-sample type codes (low word of the flags field)
FLAG_MEASURED = 1
FLAG_GAP_FILLED = 2
FLAG_VIRTUAL = 3


@dataclass
class Fragment:
    """A contiguous run of samples for a single (as-yet-unidentified) marker.

    One physical juggling ball may be split across several fragments wherever the
    capture system lost and re-acquired it (typically at throw apexes and ball
    crossings). ``start_frame`` is the fragment's absolute frame in the recording;
    it is ``None`` when unknown — the raw QTM export does not store it, so it must
    be recovered by the tracker.
    """

    id: str
    positions: np.ndarray  # (N, 3) metres, NaN where invalid
    residual: np.ndarray  # (N,) metres, marker fit residual
    flags: np.ndarray  # (N,) uint32 QTM type code per sample
    frame_rate: float  # Hz
    kind: str = "unknown"  # "ball" | "static" | "ghost" | "unknown"
    start_frame: int | None = None

    @property
    def n_samples(self) -> int:
        return len(self.positions)

    @property
    def valid(self) -> np.ndarray:
        """Boolean mask of samples with finite, in-volume positions."""
        p = self.positions
        mask = np.isfinite(p).all(axis=1) & (np.abs(p) < 1e3).all(axis=1)
        return np.asarray(mask, dtype=bool)

    @property
    def duration(self) -> float:
        return self.n_samples / self.frame_rate if self.frame_rate else 0.0

    @property
    def z(self) -> np.ndarray:
        return self.positions[:, 2]

    @property
    def height_span(self) -> float:
        v = self.positions[self.valid]
        return float(v[:, 2].max() - v[:, 2].min()) if len(v) else 0.0

    def bounds(self) -> tuple[np.ndarray, np.ndarray] | None:
        v = self.positions[self.valid]
        if not len(v):
            return None
        return v.min(axis=0), v.max(axis=0)

    @property
    def mean_residual(self) -> float:
        r = self.residual[self.valid]
        return float(np.nanmean(r)) if len(r) else float("nan")


@dataclass
class Session:
    """A whole recording: metadata plus the trajectory fragments it contains."""

    source: str
    frame_rate: float
    fragments: list[Fragment] = field(default_factory=list)
    frame_count: int | None = None
    up_axis: str = "z"
    units: str = "m"
    gravity: float = GRAVITY

    @property
    def duration(self) -> float:
        if self.frame_count and self.frame_rate:
            return self.frame_count / self.frame_rate
        return max((f.duration for f in self.fragments), default=0.0)

    def of_kind(self, kind: str) -> list[Fragment]:
        return [f for f in self.fragments if f.kind == kind]

    @property
    def balls(self) -> list[Fragment]:
        return self.of_kind("ball")

    @property
    def statics(self) -> list[Fragment]:
        return self.of_kind("static")

    @property
    def ghosts(self) -> list[Fragment]:
        return self.of_kind("ghost")

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for f in self.fragments:
            counts[f.kind] = counts.get(f.kind, 0) + 1
        parts = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
        return (
            f"{self.source}\n"
            f"  {self.frame_rate:g} Hz, {self.duration:.1f} s "
            f"({self.frame_count} frames)\n"
            f"  {len(self.fragments)} fragments: {parts}"
        )
