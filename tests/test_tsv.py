"""Tests for the QTM TSV reader — the validation oracle (DESIGN.md §12 layer 1).

The facts asserted against ``5_ball_juggling_cut_balls_only.tsv`` are pinned to
that export: 4967 frames at 300 Hz, 19 markers from 4 cameras, 3 of the 19
trajectories Mixed, and the five balls the recording starts with. Malformed-input
behaviour is checked against small TSVs built in ``tmp_path``, so it runs even
without the corpus.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

from juggling_analyser.io.tsv import TsvExport, read_tsv

from .conftest import BALLS_ONLY_TSV, sample

# --------------------------------------------------------------------------- #
# the real export
# --------------------------------------------------------------------------- #

#: Trajectories QTM marked Mixed in the balls-only export, as 0-based marker indices.
MIXED_MARKER_INDICES = (7, 9, 11)

#: Markers with data on frame 1 — the five balls the recording opens with.
BALLS_AT_FIRST_FRAME = (0, 1, 2, 3, 4)


@pytest.fixture(scope="module")
def export() -> TsvExport:
    """The balls-only TSV export, read once for the whole module."""
    return read_tsv(sample(BALLS_ONLY_TSV))


def test_header_decodes(export: TsvExport) -> None:
    assert export.frame_count == 4967
    assert export.n_markers == 19
    assert export.f_s == 300.0
    assert export.header["NO_OF_CAMERAS"] == "4"
    assert export.header["FILE_VERSION"] == "2.0.0"
    assert export.header["DATA_INCLUDED"] == "3D"
    # TIME_STAMP carries two tab-separated values; the whole line is kept verbatim.
    assert export.header["TIME_STAMP"].startswith("2024-12-12, 16:25:26.188\t")
    assert export.source.endswith(BALLS_ONLY_TSV)


def test_marker_names_absent_and_synthesised(export: TsvExport) -> None:
    assert export.marker_names_present is False
    assert len(export.marker_names) == 19
    assert export.marker_names[0] == "M1"
    assert export.marker_names[-1] == "M19"
    assert export.marker_names == tuple(f"M{i + 1}" for i in range(19))


def test_trajectory_types(export: TsvExport) -> None:
    types = export.trajectory_types
    assert len(types) == 19
    assert types.count("Mixed") == 3
    assert types.count("Measured") == 16
    assert set(types) == {"Measured", "Mixed"}
    assert tuple(i for i, t in enumerate(types) if t == "Mixed") == MIXED_MARKER_INDICES


def test_positions_shape_and_units(export: TsvExport) -> None:
    assert export.positions.shape == (4967, 19, 3)
    assert export.positions.dtype == np.float64

    finite = export.positions[np.isfinite(export.positions).all(axis=2)]
    assert len(finite) == 24502, "expected 24502 measured samples across all markers"
    # Metres, not millimetres: a juggling volume is a couple of metres across.
    assert np.all(np.linalg.norm(finite, axis=1) < 10.0)
    assert np.max(np.linalg.norm(finite, axis=1)) > 1.0


def test_five_balls_present_at_first_frame(export: TsvExport) -> None:
    present_first = np.array([export.present(m)[0] for m in range(export.n_markers)])
    assert present_first.sum() == 5
    assert tuple(int(i) for i in np.flatnonzero(present_first)) == BALLS_AT_FIRST_FRAME


def test_sentinel_became_nan(export: TsvExport) -> None:
    # Marker index 5 has no measurement on frame 1: QTM wrote "0.000 0.000 0.000".
    assert np.all(np.isnan(export.positions[0, 5]))
    assert not export.present(5)[0]
    # It is genuinely tracked later, so the sentinel is per-sample, not per-marker.
    assert export.present(5).any()
    # No absent sample survives as a position at the origin.
    absent = np.isnan(export.positions).any(axis=2)
    assert np.all(np.isnan(export.positions[absent])), "partial NaN triples"
    assert not np.any(np.all(export.positions == 0.0, axis=2))


def test_present_mask(export: TsvExport) -> None:
    mask = export.present(2)
    assert mask.shape == (4967,)
    assert mask.dtype == np.bool_
    assert mask.all(), "marker index 2 is tracked for the whole recording"
    assert export.present(0).sum() == 2666
    with pytest.raises(IndexError, match="marker index 19 out of range"):
        export.present(19)
    with pytest.raises(IndexError, match="marker index -1 out of range"):
        export.present(-1)


def test_frames_are_one_based(export: TsvExport) -> None:
    assert export.frames[0] == 1
    assert export.frames[-1] == 4967
    assert export.frames.shape == (4967,)
    assert np.issubdtype(export.frames.dtype, np.integer)
    assert np.array_equal(export.frames, np.arange(1, 4968))


def test_times_match_frame_index(export: TsvExport) -> None:
    # t = (k - 1) / f_s with frame 1 at t = 0 (NOTATION.md § Conventions); the file
    # writes 5 decimal places, so agreement is only to ~5e-6 s.
    assert export.times.shape == (4967,)
    assert export.times[0] == 0.0
    expected = (export.frames - 1) / export.f_s
    assert np.allclose(export.times, expected, atol=1e-5, rtol=0.0)
    assert export.n_frames == export.frame_count


# --------------------------------------------------------------------------- #
# malformed input
# --------------------------------------------------------------------------- #

_HEADER_LINES = (
    "FILE_VERSION\t2.0.0",
    "NO_OF_FRAMES\t3",
    "NO_OF_CAMERAS\t4",
    "NO_OF_MARKERS\t2",
    "FREQUENCY\t300",
    "DESCRIPTION\t--",
    "TIME_STAMP\t2024-12-12, 16:25:26.188\t56373.87175403",
    "DATA_INCLUDED\t3D",
    "MARKER_NAMES\tball_a\tball_b",
    "TRAJECTORY_TYPES\tMeasured\tMixed",
    "Frame\tTime\t X\t Y\t Z\t X\t Y\t Z\t",  # QTM leaves a trailing tab here
)

_DATA_LINES = (
    "1\t0.00000\t100.000\t200.000\t300.000\t0.000\t0.000\t0.000",
    "2\t0.00333\t101.000\t201.000\t301.000\t400.000\t500.000\t600.000",
    "3\t0.00667\t102.000\t202.000\t302.000\t401.000\t501.000\t601.000",
)


def _tsv(tmp_path: Path, *, header: tuple[str, ...] = (), data: tuple[str, ...] = ()) -> Path:
    """Write a small CRLF-terminated TSV, as QTM does, and return its path."""
    lines = (header or _HEADER_LINES) + (data or _DATA_LINES)
    path = tmp_path / "tiny.tsv"
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8", newline="")
    return path


def test_minimal_tsv_round_trips(tmp_path: Path) -> None:
    result = read_tsv(_tsv(tmp_path))

    assert result.frame_count == 3
    assert result.n_markers == 2
    assert result.f_s == 300.0
    assert result.marker_names_present is True
    assert result.marker_names == ("ball_a", "ball_b")
    assert result.trajectory_types == ("Measured", "Mixed")
    assert np.array_equal(result.frames, [1, 2, 3])
    # mm -> m, and the 0,0,0 sentinel on marker 1 of frame 1 became NaN.
    assert result.positions[0, 0] == pytest.approx([0.1, 0.2, 0.3])
    assert np.all(np.isnan(result.positions[0, 1]))
    assert result.positions[1, 1] == pytest.approx([0.4, 0.5, 0.6])
    assert result.present(0).all()
    assert list(result.present(1)) == [False, True, True]


def test_read_tsv_accepts_a_path_or_a_str(tmp_path: Path) -> None:
    path = _tsv(tmp_path)
    assert read_tsv(str(path)).frame_count == read_tsv(path).frame_count == 3


def test_blank_marker_names_are_synthesised(tmp_path: Path) -> None:
    header = tuple(
        "MARKER_NAMES\t\t" if line.startswith("MARKER_NAMES") else line for line in _HEADER_LINES
    )
    result = read_tsv(_tsv(tmp_path, header=header))
    assert result.marker_names_present is False
    assert result.marker_names == ("M1", "M2")


def test_missing_required_header_key(tmp_path: Path) -> None:
    header = tuple(line for line in _HEADER_LINES if not line.startswith("NO_OF_MARKERS"))
    with pytest.raises(ValueError, match="missing required header key 'NO_OF_MARKERS'"):
        read_tsv(_tsv(tmp_path, header=header))


def test_missing_trajectory_types(tmp_path: Path) -> None:
    header = tuple(line for line in _HEADER_LINES if not line.startswith("TRAJECTORY_TYPES"))
    with pytest.raises(ValueError, match="missing required header key 'TRAJECTORY_TYPES'"):
        read_tsv(_tsv(tmp_path, header=header))


def test_trajectory_type_count_mismatch(tmp_path: Path) -> None:
    header = tuple(
        "TRAJECTORY_TYPES\tMeasured" if line.startswith("TRAJECTORY_TYPES") else line
        for line in _HEADER_LINES
    )
    with pytest.raises(
        ValueError,
        match=re.escape("header TRAJECTORY_TYPES lists 1 entries but NO_OF_MARKERS is 2"),
    ):
        read_tsv(_tsv(tmp_path, header=header))


def test_marker_name_count_mismatch(tmp_path: Path) -> None:
    header = tuple(
        "MARKER_NAMES\ta\tb\tc" if line.startswith("MARKER_NAMES") else line
        for line in _HEADER_LINES
    )
    with pytest.raises(
        ValueError, match=re.escape("header MARKER_NAMES lists 3 entries but NO_OF_MARKERS is 2")
    ):
        read_tsv(_tsv(tmp_path, header=header))


def test_row_column_count_mismatch(tmp_path: Path) -> None:
    data = (*_DATA_LINES[:1], "2\t0.00333\t101.000\t201.000", _DATA_LINES[2])
    with pytest.raises(
        ValueError,
        match=re.escape("data row 2 has 4 columns, expected 8 for NO_OF_MARKERS=2"),
    ):
        read_tsv(_tsv(tmp_path, data=data))


def test_column_header_count_mismatch(tmp_path: Path) -> None:
    header = (*_HEADER_LINES[:-1], "Frame\tTime\t X\t Y\t Z")
    with pytest.raises(ValueError, match=re.escape("column-header line has 5 columns, expected 8")):
        read_tsv(_tsv(tmp_path, header=header))


def test_non_consecutive_frame_index(tmp_path: Path) -> None:
    data = (
        _DATA_LINES[0],
        _DATA_LINES[1].replace("2\t0.00333", "5\t0.00333", 1),
        _DATA_LINES[2],
    )
    with pytest.raises(
        ValueError,
        match=re.escape("frame index on data row 2 is 5, expected 2"),
    ):
        read_tsv(_tsv(tmp_path, data=data))


def test_frame_index_must_start_at_one(tmp_path: Path) -> None:
    data = tuple(line.replace(f"{i + 1}\t", f"{i}\t", 1) for i, line in enumerate(_DATA_LINES))
    with pytest.raises(ValueError, match="frame index on data row 1 is 0, expected 1"):
        read_tsv(_tsv(tmp_path, data=data))


def test_row_count_disagrees_with_no_of_frames(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="found 2 data rows but NO_OF_FRAMES is 3"):
        read_tsv(_tsv(tmp_path, data=_DATA_LINES[:2]))


def test_non_numeric_value(tmp_path: Path) -> None:
    # Y of the first marker is column 4 (Frame, Time, X, Y, ...), counted from 1.
    data = (_DATA_LINES[0], _DATA_LINES[1].replace("201.000", "n/a", 1), _DATA_LINES[2])
    with pytest.raises(
        ValueError, match=re.escape("data row 2 has a non-numeric value in column 4")
    ):
        read_tsv(_tsv(tmp_path, data=data))


def test_no_column_header_line(tmp_path: Path) -> None:
    header = tuple(line for line in _HEADER_LINES if not line.startswith("Frame"))
    with pytest.raises(ValueError, match="no 'Frame' column-header line found"):
        read_tsv(_tsv(tmp_path, header=header))


def test_malformed_header_line(tmp_path: Path) -> None:
    header = (*_HEADER_LINES[:5], "this line has no tab", *_HEADER_LINES[5:])
    with pytest.raises(ValueError, match="line 6: expected 'KEY<TAB>value'"):
        read_tsv(_tsv(tmp_path, header=header))


def test_non_numeric_header_count(tmp_path: Path) -> None:
    header = tuple(
        "NO_OF_FRAMES\tmany" if line.startswith("NO_OF_FRAMES") else line for line in _HEADER_LINES
    )
    with pytest.raises(ValueError, match="header NO_OF_FRAMES is not an integer"):
        read_tsv(_tsv(tmp_path, header=header))
