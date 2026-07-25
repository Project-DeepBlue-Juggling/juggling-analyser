"""Reader for raw Qualisys Track Manager ``.qtm`` files.

A ``.qtm`` file is a Microsoft OLE2 compound document. Marker trajectories are
stored under ``Measurement NBC/Data series/<id>`` as LZO1X-compressed blocks,
with a sibling ``<id>Index`` stream describing the block layout. The compression
scheme is undocumented; it was recovered by disassembling Qualisys's ``NBC.dll``
(see ``docs/qtm-format.md``). Each decompressed record is five little-endian
float64 values::

    X, Y, Z (mm), residual (mm), flags (two uint32 packed into a float64)

The low uint32 of ``flags`` is QTM's per-sample type code
(1=measured, 2=gap-filled, 3=virtual, ...).

This reader needs neither a QTM installation nor an export step: it turns a
recording straight into numpy arrays.

Important limitation (see ``docs/qtm-format.md``): a juggled ball is stored as one
or more *contiguous* trajectory fragments, and this export does **not** record
each fragment's absolute start frame. Fragments therefore carry
``start_frame=None`` until a downstream tracker recovers their absolute timing.
"""

from __future__ import annotations

import struct

import lzo
import numpy as np
import olefile

from juggling_analyser.core.trajectory import Fragment, Session

_SERIES_PREFIX = "Measurement NBC/Data series/"
_RECORD_BYTES = 40  # 5 x float64
_MM_PER_M = 1000.0


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
        pos = 3 + n * 4
        chunk = uncompressed_size // n
        parts = []
        for i in range(n):
            out_len = chunk if i < n - 1 else uncompressed_size - chunk * (n - 1)
            parts.append(bytes(lzo.decompress(comp[pos : pos + sizes[i]], False, out_len)))
            pos += sizes[i]
        return b"".join(parts)
    return bytes(lzo.decompress(comp, False, uncompressed_size))


def _read_raw_series(ole: olefile.OleFileIO, sid: int) -> np.ndarray | None:
    """Return an ``(n, 5)`` float64 array for a data series, or ``None`` if empty."""
    base = f"{_SERIES_PREFIX}{sid}"
    try:
        data = ole.openstream(base).read()
        index = ole.openstream(base + "Index").read()
    except OSError:
        return None
    if len(index) < 4:
        return None
    words = struct.unpack_from(f"<{len(index) // 4}I", index)
    n_blocks = words[0]
    if n_blocks == 0:
        return None
    out = bytearray()
    for i in range(n_blocks):
        offset, _hi, comp_size, uncomp_size = words[1 + i * 4 : 5 + i * 4]
        out += _lzo_decompress_block(data[offset : offset + comp_size], uncomp_size)
    if len(out) % _RECORD_BYTES != 0:
        # not an XYZR trajectory series (e.g. 2D camera data) -> skip
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


def _tlv_fields(blob: bytes) -> dict[int, bytes]:
    """Parse a QTM ``<u32 preamble>{<u32 id><u32 len><len bytes>}`` block."""
    fields: dict[int, bytes] = {}
    pos = 4
    while pos + 8 <= len(blob):
        fid, flen = struct.unpack_from("<II", blob, pos)
        pos += 8
        if pos + flen > len(blob):
            break
        fields.setdefault(fid, blob[pos : pos + flen])
        pos += flen
    return fields


def _frame_rate(ole: olefile.OleFileIO) -> float | None:
    try:
        cap = ole.openstream("Measurement/Capture").read()
    except OSError:
        return None
    field = _tlv_fields(cap).get(1)
    if field and len(field) == 4:
        return float(struct.unpack("<I", field)[0])
    return None


def _frame_count(ole: olefile.OleFileIO) -> int | None:
    try:
        frames = ole.openstream("Measurement/Frames").read()
    except OSError:
        return None
    field = _tlv_fields(frames).get(1)
    if field and len(field) == 4:
        return int(struct.unpack("<I", field)[0])
    return None


def read_qtm(path: str) -> Session:
    """Read a ``.qtm`` file into a :class:`Session` of raw trajectory fragments.

    The fragments are returned untyped (``kind='unknown'``); call
    :func:`juggling_analyser.core.clean.classify_session` to label them as
    balls, static references, or ghosts.
    """
    ole = olefile.OleFileIO(path)
    try:
        frame_rate = _frame_rate(ole) or 0.0
        frame_count = _frame_count(ole)
        fragments: list[Fragment] = []
        for sid in _list_series_ids(ole):
            arr = _read_raw_series(ole, sid)
            if arr is None or len(arr) == 0:
                continue
            positions = arr[:, :3] / _MM_PER_M  # mm -> m
            residual = arr[:, 3] / _MM_PER_M  # mm -> m
            flags = arr[:, 4].view(np.uint64).astype(np.uint32)  # low word = type code
            fragments.append(
                Fragment(
                    id=str(sid),
                    positions=positions.astype(np.float64),
                    residual=residual.astype(np.float64),
                    flags=flags,
                    frame_rate=frame_rate,
                )
            )
    finally:
        ole.close()

    return Session(
        source=path,
        frame_rate=frame_rate,
        frame_count=frame_count,
        fragments=fragments,
    )
