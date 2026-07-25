"""Reader for raw Qualisys Track Manager ``.qtm`` files (DESIGN.md §4).

A ``.qtm`` file is a Microsoft OLE2 compound document. Marker samples live in
``Measurement NBC/Data series/<id>`` as LZO1X-compressed 40-byte records; the
*description* of each trajectory — including its absolute frame ranges — lives in
``Measurement NBC/Data Items`` as a stream of typed objects. Neither is
documented; both were reverse-engineered, and the full write-up is in
``docs/qtm-format.md``. This reader needs no QTM installation and no export step.

Two facts drive it:

* **Trajectories carry absolute frame ranges.** Each *Trajectory* object in
  ``Data Items`` has a ``Parts`` table of ``(start_frame, end_frame, type)``
  triples, 1-based inclusive, whose lengths sum to the decoded sample count.
  There is no "unknown start frame" — that was an early misreading of the format.
* **Not every data series is a trajectory.** A series is one only if a Trajectory
  object references it *and* that object's ``Trajectory Type`` is 1. Without both
  gates the reader invents markers: the 5-ball sample has 25 decodable series but
  only 19 trajectories, confirmed against a QTM TSV export.

Every accepted trajectory is integrity-checked: its ``Parts`` lengths must sum to
the decoded sample count and the per-sample type flags must agree with the part
types. A mismatch raises rather than being papered over, because it would mean
this reader's model of the format is wrong.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import lzo
import numpy as np
import olefile

from juggling_analyser.core.params import RESIDUAL_SIGMA_FLOOR
from juggling_analyser.core.trajectory import Piece, Session, Trajectory, Uncertainty

_SERIES_PREFIX = "Measurement NBC/Data series/"
_DATA_ITEMS = "Measurement NBC/Data Items"
_RECORD_BYTES = 40  # 5 x float64: X, Y, Z, residual, flags
_MM_PER_M = 1000.0
_WORD = 4

#: ``Data Items`` object type for a marker trajectory. The type and field
#: numbering are named by the schema in ``Measurement NBC/Metadata``.
_TYPE_TRAJECTORY = 10

#: Field ids inside a Trajectory object, per that schema.
_F_SERIES_ID = 5
_F_DISPLAY_ORDER = 7
_F_LABEL = 8
_F_COLOUR = 9
_F_TRAJECTORY_TYPE = 12
_F_POINT_TYPE = 13
_F_PARTS = 17

#: The only ``Trajectory Type`` QTM exports as a 3D marker. Trajectories of any
#: other type exist in the project file but are absent from a TSV 3D export; in
#: ``5_ball_juggling_cut_balls_only.qtm`` the five type-2 trajectories are the
#: static markers the owner removed, and excluding them reproduces the export's
#: 19 markers exactly.
_EXPORTED_TRAJECTORY_TYPE = 1

#: Plausibility bounds used only to keep the object scan from locking onto noise.
_MAX_FIELDS_PER_OBJECT = 64
_MAX_FIELD_BYTES = 1 << 16
_MAX_FIELD_ID = 200


# --------------------------------------------------------------------------- #
# series decoding
# --------------------------------------------------------------------------- #


def _lzo_decompress_block(comp: bytes, uncompressed_size: int) -> bytes:
    """Decompress one series block.

    Two framings are used. Large blocks are split into ``N`` sub-streams for
    parallel (de)compression, marked by a leading ``0x55`` byte; everything else
    is a single raw LZO1X stream.
    """
    if comp and comp[0] == 0x55:
        n = comp[1]
        if (comp[1] ^ 0x55) & 0xFF != comp[2]:
            raise ValueError("malformed multi-stream block header")
        sizes = struct.unpack_from(f"<{n}I", comp, 3)
        pos = 3 + n * _WORD
        chunk = uncompressed_size // n
        parts = []
        for i in range(n):
            out_len = chunk if i < n - 1 else uncompressed_size - chunk * (n - 1)
            parts.append(bytes(lzo.decompress(comp[pos : pos + sizes[i]], False, out_len)))
            pos += sizes[i]
        return b"".join(parts)
    return bytes(lzo.decompress(comp, False, uncompressed_size))


def _read_raw_series(ole: olefile.OleFileIO, series_id: int) -> np.ndarray | None:
    """An ``(n, 5)`` float64 array for a data series, or ``None`` if it is not one.

    ``None`` means the stream is missing, empty, or not a multiple of 40 bytes —
    the latter being 2D per-camera data rather than an XYZR trajectory.
    """
    base = f"{_SERIES_PREFIX}{series_id}"
    try:
        data = ole.openstream(base).read()
        index = ole.openstream(base + "Index").read()
    except OSError:
        return None
    if len(index) < _WORD:
        return None
    words = struct.unpack_from(f"<{len(index) // _WORD}I", index)
    n_blocks = words[0]
    if n_blocks == 0:
        return None
    out = bytearray()
    for i in range(n_blocks):
        offset, _offset_hi, comp_size, uncomp_size = words[1 + i * 4 : 5 + i * 4]
        out += _lzo_decompress_block(data[offset : offset + comp_size], uncomp_size)
    if len(out) % _RECORD_BYTES != 0:
        return None
    return np.frombuffer(bytes(out), dtype="<f8").reshape(-1, 5)


def _list_series_ids(ole: olefile.OleFileIO) -> list[int]:
    ids = []
    for entry in ole.listdir(streams=True, storages=False):
        path = "/".join(entry)
        if path.startswith(_SERIES_PREFIX):
            name = path[len(_SERIES_PREFIX) :]
            if name.isdigit():
                ids.append(int(name))
    return sorted(set(ids))


# --------------------------------------------------------------------------- #
# capture metadata
# --------------------------------------------------------------------------- #


def _tlv_fields(blob: bytes) -> dict[int, bytes]:
    """Parse a QTM ``<u32 preamble>{<u32 id><u32 len><len bytes>}`` block.

    Only the first field of each id is kept, which is fine for the flat settings
    streams this is used on. ``Data Items`` repeats ids per object and is parsed
    by :func:`_trajectory_objects` instead.
    """
    fields: dict[int, bytes] = {}
    pos = _WORD
    while pos + 2 * _WORD <= len(blob):
        field_id, length = struct.unpack_from("<II", blob, pos)
        pos += 2 * _WORD
        if pos + length > len(blob):
            break
        fields.setdefault(field_id, blob[pos : pos + length])
        pos += length
    return fields


def _settings_u32(ole: olefile.OleFileIO, stream: str, field_id: int) -> int | None:
    try:
        blob = ole.openstream(stream).read()
    except OSError:
        return None
    payload = _tlv_fields(blob).get(field_id)
    if payload is None or len(payload) != _WORD:
        return None
    return int(struct.unpack("<I", payload)[0])


# --------------------------------------------------------------------------- #
# Data Items: the trajectory descriptions
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TrajectoryObject:
    """One *Trajectory* object from ``Data Items``, decoded but not yet validated."""

    object_id: int
    series_id: int
    trajectory_type: int
    point_type: int
    display_order: int
    colour: int
    label: str
    pieces: tuple[Piece, ...]

    @property
    def exported(self) -> bool:
        """True when QTM would include this trajectory in a 3D export."""
        return self.trajectory_type == _EXPORTED_TRAJECTORY_TYPE

    @property
    def piece_samples(self) -> int:
        return sum(piece.length for piece in self.pieces)


def _u32_at(blob: bytes, offset: int) -> int:
    return int(struct.unpack_from("<I", blob, offset)[0])


def _parse_parts(raw: bytes) -> tuple[Piece, ...] | None:
    """Parse a ``Parts`` payload: ``u32 count`` then ``count × (start, end, type)``.

    Returns ``None`` when the payload length disagrees with its own count, which
    is how the object scan rejects a false positive.
    """
    if len(raw) < _WORD:
        return None
    count = _u32_at(raw, 0)
    if len(raw) != _WORD + 3 * _WORD * count:
        return None
    pieces = []
    previous_end = 0
    for i in range(count):
        base = _WORD + 3 * _WORD * i
        start, end, sample_type = (_u32_at(raw, base + 4 * j) for j in range(3))
        if start < 1 or end < start or start <= previous_end:
            return None
        previous_end = end
        pieces.append(Piece(start_frame=start, end_frame=end, sample_type=sample_type))
    return tuple(pieces)


def _decode_label(raw: bytes) -> str:
    """A ``Trajectory Label``: ``u32 char_count`` then UTF-16-LE plus a NUL."""
    if len(raw) < _WORD:
        return ""
    count = _u32_at(raw, 0)
    end = _WORD + 2 * count
    if count == 0 or end > len(raw):
        return ""
    return raw[_WORD:end].decode("utf-16-le", errors="replace")


def _scalar(fields: dict[int, bytes], field_id: int, default: int = 0) -> int:
    raw = fields.get(field_id)
    if raw is None or len(raw) not in (1, 2, 4):
        return default
    return int.from_bytes(raw, "little")


def _trajectory_objects(blob: bytes) -> tuple[TrajectoryObject, ...]:
    """Scan ``Data Items`` for Trajectory objects.

    The stream is a sequence of objects, each
    ``<u32 type><u32 object_id><u32 field_count>`` followed by ``field_count``
    ``<u32 field_id><u32 length><length bytes>`` fields. Object types other than
    a trajectory are not modelled, so this scans for the trajectory type rather
    than walking the whole grammar, and accepts a candidate only if it parses
    cleanly *and* carries both a series id and a ``Parts`` table. Every accepted
    object is then cross-validated against its data series by
    :func:`read_qtm`, which is what makes the scan trustworthy: a false positive
    would have to invent a series id whose decoded length happens to equal the
    sum of an invented parts table.
    """
    objects: list[TrajectoryObject] = []
    n = len(blob)
    offset = 0
    while offset + 3 * _WORD <= n:
        if _u32_at(blob, offset) != _TYPE_TRAJECTORY:
            offset += _WORD
            continue
        object_id = _u32_at(blob, offset + _WORD)
        field_count = _u32_at(blob, offset + 2 * _WORD)
        if not 1 <= field_count <= _MAX_FIELDS_PER_OBJECT:
            offset += _WORD
            continue
        fields, end = _read_object_fields(blob, offset + 3 * _WORD, field_count)
        parts = _parse_parts(fields[_F_PARTS]) if fields and _F_PARTS in fields else None
        if fields is None or parts is None or _F_SERIES_ID not in fields:
            offset += _WORD
            continue
        objects.append(
            TrajectoryObject(
                object_id=object_id,
                series_id=_scalar(fields, _F_SERIES_ID),
                trajectory_type=_scalar(fields, _F_TRAJECTORY_TYPE),
                point_type=_scalar(fields, _F_POINT_TYPE),
                display_order=_scalar(fields, _F_DISPLAY_ORDER),
                colour=_scalar(fields, _F_COLOUR),
                label=_decode_label(fields.get(_F_LABEL, b"")),
                pieces=parts,
            )
        )
        offset = end
    return tuple(objects)


def _read_object_fields(
    blob: bytes, offset: int, field_count: int
) -> tuple[dict[int, bytes] | None, int]:
    """Read ``field_count`` TLV fields; ``(None, offset)`` if they do not parse."""
    fields: dict[int, bytes] = {}
    n = len(blob)
    for _ in range(field_count):
        if offset + 2 * _WORD > n:
            return None, offset
        field_id, length = struct.unpack_from("<II", blob, offset)
        offset += 2 * _WORD
        if not 1 <= field_id <= _MAX_FIELD_ID or length > _MAX_FIELD_BYTES or offset + length > n:
            return None, offset
        fields[field_id] = blob[offset : offset + length]
        offset += length
    return fields, offset


# --------------------------------------------------------------------------- #
# the reader
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class QtmScan:
    """What the reader found in a file, before trajectories were built.

    Exposed so that ``info`` and the tests can assert *what was skipped and why*
    instead of only seeing the survivors.
    """

    source: str
    f_s: float
    frame_count: int
    series_ids: tuple[int, ...]
    #: Series that decode as 40-byte XYZR records.
    decodable_series: tuple[int, ...]
    objects: tuple[TrajectoryObject, ...]

    @property
    def exported_objects(self) -> tuple[TrajectoryObject, ...]:
        return tuple(o for o in self.objects if o.exported and o.pieces)

    @property
    def empty_objects(self) -> tuple[TrajectoryObject, ...]:
        """Trajectory objects with no parts at all — nothing was ever tracked."""
        return tuple(o for o in self.objects if not o.pieces)

    @property
    def unexported_objects(self) -> tuple[TrajectoryObject, ...]:
        return tuple(o for o in self.objects if not o.exported)

    @property
    def orphan_series(self) -> tuple[int, ...]:
        """Decodable series that no Trajectory object references.

        These are the phantom markers that a reader without the object gate
        invents — one of them claims 18 422 samples in a 4 967-frame recording.
        """
        referenced = {o.series_id for o in self.objects}
        return tuple(s for s in self.decodable_series if s not in referenced)


def scan_qtm(path: str | Path) -> QtmScan:
    """Read a ``.qtm`` file's structure without decoding every sample twice.

    Cheap relative to :func:`read_qtm` in the parts that matter for diagnostics,
    but it does decode each series to decide whether it is a trajectory at all.
    """
    source = str(path)
    ole = olefile.OleFileIO(source)
    try:
        f_s = _settings_u32(ole, "Measurement/Capture", 1)
        frame_count = _settings_u32(ole, "Measurement/Frames", 1)
        if f_s is None or f_s <= 0:
            raise ValueError(f"{source}: no capture frame rate in Measurement/Capture")
        if frame_count is None:
            raise ValueError(f"{source}: no frame count in Measurement/Frames")
        series_ids = _list_series_ids(ole)
        decodable = tuple(s for s in series_ids if _read_raw_series(ole, s) is not None)
        try:
            items = ole.openstream(_DATA_ITEMS).read()
        except OSError as exc:
            raise ValueError(f"{source}: no {_DATA_ITEMS} stream; not a QTM measurement") from exc
        objects = _trajectory_objects(items)
    finally:
        ole.close()
    return QtmScan(
        source=source,
        f_s=float(f_s),
        frame_count=int(frame_count),
        series_ids=tuple(series_ids),
        decodable_series=decodable,
        objects=objects,
    )


def read_qtm(path: str | Path, *, include_unexported: bool = False) -> Session:
    """Read a ``.qtm`` recording into a :class:`Session` of trajectories.

    Positions are metres in the **QTM frame** as recorded; the rotation into the
    juggling frame is derived per recording downstream (DESIGN.md §5). Each
    sample keeps QTM's per-sample type code and its residual, the latter as an
    isotropic 1σ uncertainty (DESIGN.md §3).

    Trajectories come back ``kind="unknown"``; call
    :func:`juggling_analyser.core.clean.classify_session` to label them.

    Args:
        path: the ``.qtm`` file.
        include_unexported: also return trajectories QTM would omit from a 3D
            export (``Trajectory Type != 1``). Off by default so that the reader
            reproduces a QTM export exactly; on, it is a diagnostic for seeing
            everything the project file holds.

    Raises:
        ValueError: if the file is not a QTM measurement, or if a trajectory's
            ``Parts`` table disagrees with its data series in length or sample
            type — either would mean this reader's model of the format is wrong,
            and continuing would silently corrupt every downstream result.
    """
    source = str(path)
    ole = olefile.OleFileIO(source)
    try:
        f_s = _settings_u32(ole, "Measurement/Capture", 1)
        frame_count = _settings_u32(ole, "Measurement/Frames", 1)
        if f_s is None or f_s <= 0:
            raise ValueError(f"{source}: no capture frame rate in Measurement/Capture")
        if frame_count is None:
            raise ValueError(f"{source}: no frame count in Measurement/Frames")
        try:
            items = ole.openstream(_DATA_ITEMS).read()
        except OSError as exc:
            raise ValueError(f"{source}: no {_DATA_ITEMS} stream; not a QTM measurement") from exc

        trajectories: list[Trajectory] = []
        skipped: list[str] = []
        for obj in _trajectory_objects(items):
            if not obj.pieces:
                skipped.append(f"{obj.series_id} (no parts)")
                continue
            if not obj.exported and not include_unexported:
                skipped.append(f"{obj.series_id} (trajectory type {obj.trajectory_type})")
                continue
            trajectories.append(_build_trajectory(ole, source, obj, frame_count))
    finally:
        ole.close()

    return Session(
        source=source,
        f_s=float(f_s),
        frame_count=int(frame_count),
        trajectories=tuple(trajectories),
        frame="qtm",
        skipped_series=tuple(skipped),
    )


def _build_trajectory(
    ole: olefile.OleFileIO, source: str, obj: TrajectoryObject, frame_count: int
) -> Trajectory:
    """Turn one validated Trajectory object plus its series into a Trajectory."""
    raw = _read_raw_series(ole, obj.series_id)
    if raw is None:
        raise ValueError(
            f"{source}: trajectory object {obj.object_id} references series "
            f"{obj.series_id}, which holds no 40-byte XYZR records"
        )
    if len(raw) != obj.piece_samples:
        raise ValueError(
            f"{source}: series {obj.series_id} decodes to {len(raw)} samples but its "
            f"Parts table covers {obj.piece_samples} — the piece table and the data "
            "series disagree"
        )
    last_frame = obj.pieces[-1].end_frame
    if last_frame > frame_count:
        raise ValueError(
            f"{source}: series {obj.series_id} has a part ending at frame {last_frame}, "
            f"beyond the recording's {frame_count} frames"
        )

    frames = np.concatenate(
        [np.arange(p.start_frame, p.end_frame + 1, dtype=np.int64) for p in obj.pieces]
    )
    part_type = np.concatenate(
        [np.full(p.length, p.sample_type, dtype=np.uint8) for p in obj.pieces]
    )
    flags = np.ascontiguousarray(raw[:, 4]).view(np.uint64) & 0xFFFFFFFF
    sample_type = flags.astype(np.uint8)
    if not np.array_equal(sample_type, part_type):
        differing = int(np.flatnonzero(sample_type != part_type)[0])
        raise ValueError(
            f"{source}: series {obj.series_id} sample {differing} has type "
            f"{sample_type[differing]} but its part says {part_type[differing]} — "
            "the per-sample flags and the piece table disagree"
        )

    residual = raw[:, 3] / _MM_PER_M
    return Trajectory(
        id=str(obj.series_id),
        frames=frames,
        positions=np.ascontiguousarray(raw[:, :3]) / _MM_PER_M,
        uncertainty=Uncertainty.isotropic(np.maximum(residual, RESIDUAL_SIGMA_FLOOR)),
        sample_type=sample_type,
        pieces=obj.pieces,
        label=obj.label,
    )
