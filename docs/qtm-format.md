# The `.qtm` file format (reverse-engineered)

Qualisys Track Manager's `.qtm` project files are an undocumented binary format.
This note records what was needed to read the marker trajectories directly, so the
analyser never depends on a QTM install or an export step. Verified against QTM
2024.3 (build 14360) recordings.

## Container

A `.qtm` file is a **Microsoft OLE2 / Compound File Binary** document (magic
`D0 CF 11 E0 A1 B1 1A E1`). Open it with [`olefile`](https://pypi.org/project/olefile/)
and enumerate its streams. The interesting ones:

| Stream | Contents |
| --- | --- |
| `Measurement/Capture` | TLV settings; **field 1 = capture frame rate (Hz)** |
| `Measurement/Frames` | TLV; **field 1 = total frame count** |
| `Measurement NBC/Data series/<id>` | one marker trajectory, compressed |
| `Measurement NBC/Data series/<id>Index` | block table for that series |

TLV blocks are `<u32 preamble>` then repeating `<u32 id><u32 len><len bytes>`.

## Series compression: LZO1X

Each `Data series/<id>` stream is a sequence of **LZO1X**-compressed blocks. The
codec is not documented; it was identified by disassembling `NBC.dll`
(`?ReadBlock@NBC_CCompressedStream@@...` → the decompressor at RVA `0x1010`/`0x1500`,
whose literal-run/​match token structure is unmistakably LZO1X). It decompresses
correctly with [`python-lzo`](https://pypi.org/project/python-lzo/).

The `<id>Index` stream gives the block layout:

```
u32 n_blocks
repeat n_blocks: u32 comp_offset, u32 offset_hi, u32 comp_size, u32 uncomp_size
```

Concatenate the decompressed blocks. Two framings appear:

- **Single stream** — the block is one raw LZO1X stream.
- **Multi stream** — first byte `0x55`, then `u8 N`, `u8 (N ^ 0x55)`, `N × u32`
  sub-stream sizes, then `N` LZO1X streams. The output is split into `N`
  near-equal chunks (parallel compression). Decompress each sub-stream to its
  chunk length and concatenate.

## Trajectory record

The concatenated output is a flat array of **40-byte records**, five little-endian
`float64` each:

| Offset | Field | Units |
| --- | --- | --- |
| 0 | X | mm |
| 8 | Y | mm |
| 16 | Z (vertical) | mm |
| 24 | residual | mm |
| 32 | flags | two `uint32` packed into the float64 |

The low `uint32` of `flags` is QTM's per-sample **type code**: `1` measured,
`2` gap-filled, `3` virtual, `4` edited. In the sample juggling data every ball
sample is `1` (measured) with sub-millimetre residuals.

Series whose byte length is **not** a multiple of 40 are not XYZR trajectories
(e.g. 2D per-camera marker data); the reader skips them.

## Absolute timing: the piece table

Tracking breaks wherever the system loses a marker — for juggling, at throw apexes
and ball crossings — so a trajectory is not necessarily one contiguous run. QTM
calls a trajectory with internal gaps **Mixed**.

The absolute frame ranges are stored in `Measurement NBC/Data Items`, which is a
TLV stream in the same `<u32 id><u32 len><len bytes>` form as above. **Field id 17
is the per-trajectory piece table**:

```
u32 count
repeat count: u32 start_frame, u32 end_frame, u32 type
```

Frames are **1-based and inclusive**; `type` matches the per-sample type code
(1 = measured, 2 = gap-filled). A trajectory's `Data series` payload is the
**concatenation of its pieces**, so the piece lengths sum exactly to the decoded
sample count — that is how a piece table is matched to its series.

Worked example from `5_ball_juggling_cut_balls_only.qtm`, the trajectory QTM shows
as three pieces `[1681-1691], [1692-1693], [1694-4967]`:

```
count=3  (1681, 1691, 1)  (1692, 1693, 2)  (1694, 4967, 1)
         11 samples       + 2 samples      + 3274 samples   = 3287
```

3287 is exactly the decoded length of that series. Across the whole file the piece
tables account for 24 502 samples, matching the decode sample-for-sample, and
exactly five trajectories are active at frame 1 — the five balls.

## Not every data series is a trajectory

The 5-ball sample has **25** data series but only **19** trajectories (confirmed by
a QTM TSV export: `NO_OF_MARKERS 19`). Six series decode as 40-byte-aligned data
but are not marker paths — they read as a constant z ≈ −0.66 m with near-zero
residual, and one reports 18 422 samples in a 4 967-frame recording.

**A series is a trajectory only if a piece table matches it.** Reading series
without that gate invents phantom markers.

## Identity is still not free

Absolute timing is solved, but a trajectory is still not a *ball*: one ball spans
several trajectories, and deciding which trajectory continues which ball across a
gap remains an inference problem. It is a well-posed one — in the 5-ball clip the
ball trajectories already cover 98.5% of all ball-frames — and is handled by the
linker (`DESIGN.md` §7), not the reader.

## Sample data facts

| | 3-ball clip | 5-ball clip |
| --- | --- | --- |
| Frames @ 300 Hz | 9101 (30.3 s) | 4967 (16.6 s) |
| Ground truth | 22 catches → drop → 2 collection catches (24 in run 1) → 31 catches in run 2 | — |

The 3-ball counts are the owner's hand-labelled target for the event detector
(DESIGN.md §12). The two catches after the drop are the balls that were already
airborne when it happened; they belong to run 1 — see the collection-catch rule in
DESIGN.md §6.
