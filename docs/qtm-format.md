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
| `Measurement NBC/Data series/<id>` | one marker trajectory's samples, compressed |
| `Measurement NBC/Data series/<id>Index` | block table for that series |
| `Measurement NBC/Data Items` | the objects that *describe* the trajectories |
| `Measurement NBC/Metadata` | a schema naming every object type and field |

TLV blocks are `<u32 preamble>` then repeating `<u32 id><u32 len><len bytes>`.
`Data Items` is **not** flat TLV — see below.

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

The residual is QTM's RMS ray-intersection error for that sample. This project
uses it as an isotropic 1σ position uncertainty, clamped below at 0.1 mm — it is a
useful proxy, not a calibrated σ (see `core/params.py`).

## `Data Items`: the trajectory descriptions

A `Data series` stream holds *samples* and nothing else — no identity, no timing.
Everything that describes a trajectory lives in `Measurement NBC/Data Items`,
which is a stream of **typed objects**, not flat TLV:

```
u32 preamble
repeat: u32 object_type
        u32 object_id          (unique, ascending through the stream)
        u32 field_count
        repeat field_count: u32 field_id, u32 length, length bytes
        <20-byte trailer>      (see below)
```

**Object type 10 is a Trajectory.** Its fields, all observed with
`field_count == 12`:

| Field | Name | Observed |
| --- | --- | --- |
| 5 | Series ID | the `Data series/<id>` this trajectory's samples live in |
| 6 | 6dDataPoint | absent for marker trajectories |
| 7 | Trajectory Display Order | 1..5 for the labelled static markers, 6 for the rest |
| 8 | Trajectory Label | `u32 char_count` then UTF-16-LE + NUL; `count=0` when unlabelled |
| 9 | Trajectory Color | packed RGB, e.g. `0x898989` |
| 10 | Trajectory Is Visible | 1 |
| 11 | Trajectory Show Trace | 1 |
| 12 | **Trajectory Type** | 1 or 2 — the export gate, see below |
| 13 | **Point Type** | 1 = *Measured*, 3 = *Mixed*, 0 for an empty trajectory |
| 14 | Trajectory Physical ID | 0 |
| 15 | Label Type | 0 |
| 16 | Trajectory Model or Body Index | 0 |
| 17 | **Parts** | the piece table |
| 18 | 3d Data Point | — |

The names are not guesses: `Measurement NBC/Metadata` is a **schema** listing every
type and field name in field-id order (`… Series ID, 6dDataPoint, Trajectory
Display Order, Trajectory Label, Trajectory Color, Trajectory Is Visible,
Trajectory Show Trace, Trajectory Type, Point Type, Trajectory Physical ID, Label
Type, Trajectory Model or Body Index, Parts, 3d Data Point …`). That list maps
gaplessly onto the observed ids 5–18, with 6 the only absent one.

The 20 bytes that follow field 17 restate the trajectory's overall span as
`(1, first_start_frame, last_end_frame, 0, 0)`. Nothing needs them, so the reader
resumes its scan at the next type-10 header rather than modelling them.

## Absolute timing: the `Parts` table

Tracking breaks wherever the system loses a marker — for juggling, at throw apexes
and ball crossings — so a trajectory is not necessarily one contiguous run. QTM
calls a trajectory containing gap-filled samples **Mixed**.

Field 17, `Parts`, is the per-trajectory piece table:

```
u32 count
repeat count: u32 start_frame, u32 end_frame, u32 type
```

Frames are **1-based and inclusive**; `type` matches the per-sample type code
(1 = measured, 2 = gap-filled). A trajectory's `Data series` payload is the
**concatenation of its parts**, so the part lengths sum exactly to the decoded
sample count.

Worked example from `5_ball_juggling_cut_balls_only.qtm`, the trajectory QTM shows
as three pieces `[1681-1691], [1692-1693], [1694-4967]` (series 448):

```
count=3  (1681, 1691, 1)  (1692, 1693, 2)  (1694, 4967, 1)
         11 samples       + 2 samples      + 3274 samples   = 3287
```

3287 is exactly the decoded length of that series. Note that these three parts
*abut*: no frame is missing, and the split exists only because the middle two
samples were gap-filled. QTM still calls that trajectory Mixed, so "Mixed" means
**"contains gap-filled samples"**, not "has a hole".

Two independent checks make the parse self-validating, and the reader raises
rather than continuing if either fails:

1. the part lengths sum to the decoded sample count, for every trajectory in all
   three sample recordings (24, 62 and 25 objects respectively);
2. the per-sample type flags in the series equal the part types, sample for sample.

## Not every data series is a trajectory

The balls-only 5-ball sample has **41** data series, **25** of which decode as
40-byte-aligned XYZR records — but only **19** trajectories, confirmed by a QTM TSV
export (`NO_OF_MARKERS 19`). Two independent gates get from 25 to 19, and both are
needed:

* **A series must be referenced by a Trajectory object.** Series 232 is not; it
  decodes to 18 422 samples in a 4 967-frame recording. (The 3-ball file has two
  such orphans, 139 and 140.)
* **`Trajectory Type` must be 1.** Five referenced series are type 2. They decode
  as perfectly motionless points at z ≈ −0.66 to −0.68 m with sub-millimetre
  residuals: static markers on the rig. In the *other* two recordings the same five
  physical markers are type 1 and carry the labels `base_0`..`base_4`, so type 2
  records a per-project state — the balls-only file is one where the owner removed
  them — rather than a property of the marker. Excluding type 2 reproduces the
  export's 19 markers exactly; the reader's `include_unexported=True` returns them
  for inspection.

Reading series without both gates invents phantom markers. Objects with an empty
`Parts` table (one in each of the two non-balls-only files) have no samples at all
and are skipped.

### The export is reproduced exactly

With those gates, all 19 trajectories match the TSV export **frame for frame**, and
positions agree to a worst case of **5.000e-07 m** — precisely half of the TSV's
own 1 µm text quantisation, i.e. the reader is bit-exact and the residual
disagreement is entirely the export's rounding. Marker order in the export is
trajectory-object order. `Point Type` predicts the export's `TRAJECTORY_TYPES`
column for all 19 (3 Mixed, 16 Measured).

## Identity is still not free

Absolute timing is solved, but a trajectory is still not a *ball*: one ball spans
several trajectories, and deciding which trajectory continues which ball across a
gap remains an inference problem. It is a well-posed one — in the 5-ball clip the
ball trajectories already cover 98.5% of all ball-frames — and is handled by the
linker (`DESIGN.md` §7), not the reader.

## Sample data facts

| | `3_ball_juggling_cut` | `5_ball_juggling_cut` | `…_balls_only` |
| --- | --- | --- | --- |
| Frames @ 300 Hz | 9101 (30.3 s) | 4967 (16.6 s) | 4967 (16.6 s) |
| Data series | 77 (63 decodable) | 40 (25) | 41 (25) |
| Trajectory objects | 62 | 25 | 24 |
| Orphan series (no object) | 139, 140 | 232 | 232 |
| Empty objects (no parts) | 1 | 1 | 0 |
| `Trajectory Type` 2 | 0 | 0 | 5 |
| **Trajectories read** | **61** | **24** | **19** |
| Static markers labelled | `base_0`..`base_4` | `base_0`..`base_4` | none (labels stripped) |
| Ground truth | 22 catches → drop → 2 collection catches (24 in run 1) → 31 catches in run 2 | — | — |

The 3-ball counts are the owner's hand-labelled target for the event detector
(DESIGN.md §12). The two catches after the drop are the balls that were already
airborne when it happened; they belong to run 1 — see the collection-catch rule in
DESIGN.md §6.
