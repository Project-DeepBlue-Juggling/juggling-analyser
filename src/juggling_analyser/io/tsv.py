"""Reader for a Qualisys Track Manager TSV 3D export.

**This module exists only as a validation oracle. The analysis pipeline never
reads TSV.** A one-off TSV export of a recording lets the tests assert that the
raw ``.qtm`` reader (:mod:`juggling_analyser.io.qtm`) reproduces what QTM itself
would export, frame for frame (DESIGN.md §4, §12 layer 1). Nothing in `core/`,
`viewer/`, or `cli` may depend on this module; ingestion goes through the `.qtm`
reader, which needs no QTM installation and no export step.

The format is a ``KEY<TAB>value...`` header, then one ``Frame<TAB>Time<TAB> X...``
column-header line, then ``NO_OF_FRAMES`` data rows::

    <frame (1-based)>  <time (s)>  X1 Y1 Z1  X2 Y2 Z2  ...   (positions in mm)

Two quirks matter:

* A marker with no measurement at a frame is written as exactly
  ``0.000 0.000 0.000``. That is QTM's "no data" sentinel, not a real position at
  the calibration origin, so it is read as ``NaN`` on all three axes.
* ``MARKER_NAMES`` may list only empty strings (it does in the balls-only
  sample). Names are then synthesised as ``M1..Mn`` and
  :attr:`TsvExport.marker_names_present` records that the file had none.

Positions are returned in metres in the **QTM frame** exactly as recorded; the
rotation into the juggling frame (NOTATION.md § Frames of reference) is derived
per recording downstream and is deliberately not applied here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_MM_PER_M = 1000.0

#: Header keys without which the file cannot be parsed at all.
_REQUIRED_KEYS = ("NO_OF_FRAMES", "NO_OF_MARKERS", "FREQUENCY", "TRAJECTORY_TYPES")

#: First field of the column-header line that separates header from data.
_FRAME_COLUMN = "Frame"

#: Leading ``Frame`` and ``Time`` columns that precede the coordinate columns.
_LEADING_COLUMNS = 2

_AXES = 3


@dataclass(frozen=True)
class TsvExport:
    """One QTM TSV 3D export, read verbatim.

    Positions are in metres in the QTM frame, times in seconds, ``f_s`` in Hz.
    ``frames`` holds 1-based absolute frame indices (NOTATION.md § Conventions);
    every other index in this class — ``marker_index``, and the first axis of
    ``positions``/``times`` — is a 0-based array index.
    """

    source: str
    f_s: float  # Hz, from FREQUENCY
    frame_count: int  # from NO_OF_FRAMES
    marker_names: tuple[str, ...]
    marker_names_present: bool  # False when the header's names were all blank
    trajectory_types: tuple[str, ...]  # "Measured" | "Mixed", one per marker
    frames: np.ndarray  # (n_frames,) int64, 1-based absolute frame index
    times: np.ndarray  # (n_frames,) float64 seconds, as written in the file
    positions: np.ndarray  # (n_frames, n_markers, 3) float64 metres, NaN where absent
    header: dict[str, str]  # every raw header key -> the raw rest of its line

    @property
    def n_markers(self) -> int:
        """Number of markers (columns) in the export."""
        return int(self.positions.shape[1])

    @property
    def n_frames(self) -> int:
        """Number of data rows, equal to :attr:`frame_count`."""
        return int(self.positions.shape[0])

    def present(self, marker_index: int) -> np.ndarray:
        """Boolean ``(n_frames,)`` mask of frames where this marker has data.

        ``marker_index`` is a 0-based index into the marker axis, so marker
        ``M1`` is ``marker_index=0``. A frame is absent when QTM wrote the
        ``0,0,0`` sentinel, which this reader stored as ``NaN``.
        """
        if not 0 <= marker_index < self.n_markers:
            raise IndexError(
                f"marker index {marker_index} out of range for {self.n_markers} markers"
            )
        return np.asarray(~np.isnan(self.positions[:, marker_index, 0]), dtype=bool)


def read_tsv(path: str | Path) -> TsvExport:
    """Read a QTM TSV 3D export into a :class:`TsvExport`.

    Positions are converted from millimetres to metres and left in the QTM frame;
    the ``0,0,0`` "no measurement" sentinel becomes ``NaN``. Times are in seconds
    as written in the file (QTM writes 5 decimal places, so they agree with
    ``(frames - 1) / f_s`` only to ~5e-6 s).

    Raises:
        ValueError: on a missing required header key, a marker-name or
            trajectory-type count that disagrees with ``NO_OF_MARKERS``, a
            missing column-header line, a data row whose column count disagrees
            with ``NO_OF_MARKERS``, a non-numeric value, a row count that
            disagrees with ``NO_OF_FRAMES``, or frame indices that do not run
            consecutively from 1.
    """
    source = str(path)
    # utf-8-sig so a BOM-prefixed export reads the same as a bare one.
    text = Path(path).read_text(encoding="utf-8-sig")
    header, column_fields, rows = _split_sections(text.splitlines(), source)

    for key in _REQUIRED_KEYS:
        if key not in header:
            raise ValueError(f"{source}: missing required header key {key!r}")

    frame_count = _header_int(header, "NO_OF_FRAMES", source)
    n_markers = _header_int(header, "NO_OF_MARKERS", source)
    f_s = _header_float(header, "FREQUENCY", source)

    trajectory_types = _per_marker_fields(
        header["TRAJECTORY_TYPES"].split("\t"), n_markers, "TRAJECTORY_TYPES", source
    )
    marker_names, marker_names_present = _marker_names(header, n_markers, source)

    expected_columns = _LEADING_COLUMNS + _AXES * n_markers
    _check_column_header(column_fields, expected_columns, source)
    frames, times, positions = _parse_rows(rows, expected_columns, frame_count, n_markers, source)

    return TsvExport(
        source=source,
        f_s=f_s,
        frame_count=frame_count,
        marker_names=tuple(marker_names),
        marker_names_present=marker_names_present,
        trajectory_types=tuple(trajectory_types),
        frames=frames,
        times=times,
        positions=positions,
        header=header,
    )


# --------------------------------------------------------------------------- #
# header
# --------------------------------------------------------------------------- #


def _split_sections(
    lines: Sequence[str], source: str
) -> tuple[dict[str, str], list[str], list[str]]:
    """Split the file into the header map, the column-header fields, and data rows.

    ``line_no`` counts file lines from 1; because of that, ``lines[line_no:]`` is
    exactly the lines *after* the current one.
    """
    header: dict[str, str] = {}
    for line_no, line in enumerate(lines, start=1):
        fields = line.split("\t")
        if fields[0] == _FRAME_COLUMN:
            return header, fields, _data_rows(lines[line_no:])
        if len(fields) < 2:
            raise ValueError(
                f"{source}: line {line_no}: expected 'KEY<TAB>value' or the "
                f"{_FRAME_COLUMN!r} column-header line, got {line!r}"
            )
        header[fields[0]] = line.split("\t", 1)[1]
    raise ValueError(
        f"{source}: no {_FRAME_COLUMN!r} column-header line found; not a QTM TSV 3D export"
    )


def _data_rows(lines: Sequence[str]) -> list[str]:
    """The data lines, less any blank lines trailing the end of the file."""
    rows = list(lines)
    while rows and not rows[-1].strip():
        rows.pop()
    return rows


def _header_int(header: dict[str, str], key: str, source: str) -> int:
    raw = header[key].split("\t", 1)[0].strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{source}: header {key} is not an integer: {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{source}: header {key} must be positive, got {value}")
    return value


def _header_float(header: dict[str, str], key: str, source: str) -> float:
    raw = header[key].split("\t", 1)[0].strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{source}: header {key} is not a number: {raw!r}") from exc
    if value <= 0.0:
        raise ValueError(f"{source}: header {key} must be positive, got {value}")
    return value


def _per_marker_fields(values: list[str], n_markers: int, key: str, source: str) -> list[str]:
    """Validate a one-value-per-marker header line, tolerating one trailing tab."""
    if len(values) == n_markers + 1 and values[-1] == "":
        values = values[:-1]
    if len(values) != n_markers:
        raise ValueError(
            f"{source}: header {key} lists {len(values)} entries but NO_OF_MARKERS is {n_markers}"
        )
    return values


def _marker_names(header: dict[str, str], n_markers: int, source: str) -> tuple[list[str], bool]:
    """Marker names and whether the file actually supplied any.

    QTM writes an empty field per marker when nothing was labelled (as in the
    balls-only sample). Names are then synthesised as ``M1..Mn``, 1-based to match
    QTM's own marker numbering, and the caller is told they were absent so no
    downstream code mistakes them for real labels (CLAUDE.md rule 3).
    """
    synthesised = [f"M{i + 1}" for i in range(n_markers)]
    if "MARKER_NAMES" not in header:
        return synthesised, False
    names = _per_marker_fields(
        header["MARKER_NAMES"].split("\t"), n_markers, "MARKER_NAMES", source
    )
    if all(not name.strip() for name in names):
        return synthesised, False
    return [name.strip() for name in names], True


def _check_column_header(fields: list[str], expected_columns: int, source: str) -> None:
    columns = _drop_trailing_empty(fields)
    if len(columns) != expected_columns:
        raise ValueError(
            f"{source}: column-header line has {len(columns)} columns, "
            f"expected {expected_columns} (Frame, Time, and 3 axes per marker)"
        )


# --------------------------------------------------------------------------- #
# data rows
# --------------------------------------------------------------------------- #


def _drop_trailing_empty(fields: list[str]) -> list[str]:
    """Drop the empty field a trailing tab leaves behind (QTM writes one)."""
    return fields[:-1] if fields and fields[-1] == "" else fields


def _parse_rows(
    rows: Sequence[str], expected_columns: int, frame_count: int, n_markers: int, source: str
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Parse the data block into ``(frames, times, positions)``.

    ``positions`` is ``(n_frames, n_markers, 3)`` in metres with the ``0,0,0``
    sentinel replaced by ``NaN``.
    """
    if len(rows) != frame_count:
        raise ValueError(f"{source}: found {len(rows)} data rows but NO_OF_FRAMES is {frame_count}")

    values: list[float] = []
    for row_index, row in enumerate(rows):
        fields = _drop_trailing_empty(row.split("\t"))
        if len(fields) != expected_columns:
            raise ValueError(
                f"{source}: data row {row_index + 1} has {len(fields)} columns, "
                f"expected {expected_columns} for NO_OF_MARKERS={n_markers}"
            )
        try:
            values.extend(map(float, fields))
        except ValueError as exc:
            raise ValueError(
                f"{source}: data row {row_index + 1} has a non-numeric value in column "
                f"{_first_non_numeric(fields) + 1}"
            ) from exc

    table = np.asarray(values, dtype=np.float64).reshape(frame_count, expected_columns)

    frames = _frame_indices(table[:, 0], source)
    times = np.ascontiguousarray(table[:, 1])
    positions = _positions_in_metres(table[:, _LEADING_COLUMNS:], n_markers)
    return frames, times, positions


def _first_non_numeric(fields: Sequence[str]) -> int:
    """0-based column of the first field that will not parse as a float."""
    for column, text in enumerate(fields):
        try:
            float(text)
        except ValueError:
            return column
    return 0  # pragma: no cover - only called after a conversion already failed


def _frame_indices(column: np.ndarray, source: str) -> np.ndarray:
    """The frame column as int64, checked to run consecutively from 1."""
    expected = np.arange(1, len(column) + 1, dtype=np.float64)
    mismatched = np.flatnonzero(column != expected)
    if mismatched.size:
        row_index = int(mismatched[0])
        raise ValueError(
            f"{source}: frame index on data row {row_index + 1} is {column[row_index]:g}, "
            f"expected {int(expected[row_index])} "
            "(frame indices must run consecutively from 1)"
        )
    return expected.astype(np.int64)


def _positions_in_metres(coordinates: np.ndarray, n_markers: int) -> np.ndarray:
    """Reshape the coordinate columns to ``(n_frames, n_markers, 3)`` in metres.

    QTM's ``0,0,0`` "no measurement" sentinel becomes ``NaN`` on all three axes,
    so an absent sample can never be mistaken for a position at the calibration
    origin.
    """
    positions = coordinates.reshape(-1, n_markers, _AXES) / _MM_PER_M
    absent = np.all(positions == 0.0, axis=2)
    positions[absent] = np.nan
    return positions
