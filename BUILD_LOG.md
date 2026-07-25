# BUILD_LOG.md — Ledger

Phase status, decisions, and deferred work. Builders append; nothing here is
deleted. `PLAN.md` says what to build; this says what happened.

---

## Pre-phase — Research, format reverse-engineering, scaffold    DONE (2026-07-19 → 2026-07-25)

**Delivered**: feasibility research and prior-art survey; the `.qtm` format cracked
(OLE2 + LZO1X, decompressor identified by disassembling `NBC.dll`); a working
reader, trajectory data model, fragment classifier, `info` CLI, and tests; the
open-source scaffold; the doc set (`NOTATION.md`, `DESIGN.md`, `PLAN.md`,
`CLAUDE.md`, this file).

**Decisions** (owner, 2026-07-20 and 2026-07-25):

- Scope: up to 7 balls now (9+ later); **solo vanilla siteswap only** in v1.
- Balls: 71 g, 74 mm. Energy in real units, plus J/kg for Airtime comparability.
- **No body or hand markers** — hands are inferred from the catch/throw cloud.
- Front end: Python core + local server + browser SPA. **Own viewer now,
  converge with Airtime later** — mirror its module boundaries so extraction is
  mechanical.
- Synthetic truth: **Airtime exports clean labelled data; this repo owns the
  mocap-degradation model.** Fixtures committed so CI needs no Node.
- Methodology: full mirror of Airtime's doc set and gate discipline.
- Test data: curated fixtures committed; larger corpus on GitHub releases.
- Batch-first; real-time gets its own causal tracker later.
- Coordinate frame: **X = left hand → right hand, Y = forward, Z = up**, derived
  per recording (NOTATION.md § Frames of reference).
- Drop = below the catch plane; loss of tracking above it is occlusion.
- Repeatability reported as **raw distributions**, no composite score.
- `Rerun` dropped as the viewer; the `viz` extra and `.rrd` artifacts are retired.
- Reader stays in this repo (no separate `pyqtm` package) — revisit later.
- Hosted at `github.com/Project-DeepBlue-Juggling/juggling-analyser`.

**Key finding — absolute timing is in the file (2026-07-25).** An earlier
conclusion that `.qtm` does not store per-trajectory start frames was **wrong**.
`Measurement NBC/Data Items` field 17 is a per-trajectory piece table:
`count`, then `count × (start_frame, end_frame, type)`. Confirmed against the
owner's QTM reading of `[1681-1691], [1692-1693], [1694-4967]` and the TSV export:
18 distinct tables for 19 trajectories (the two full-length ones share a table),
sample totals matching the decode exactly (19 535 + 4 967 = 24 502), and exactly
5 trajectories active at frame 1 for the 5-ball clip. Consequence: the planned
"recover absolute timing" milestone is **deleted**; only identity linking remains,
and it starts from 98.5% frame coverage.

**Ground truth corrected (owner, 2026-07-25).** The earlier note that
`3_ball_juggling_cut` is "24 catches, then a drop, then 31" was imprecise. The
actual structure is **22 catches → drop → the 2 still-airborne balls are caught
(24 in run 1) → 31 catches in run 2**. This established the *collection-catch*
rule (DESIGN.md §6): a drop ends the run at the drop event, but trailing catches
of already-airborne balls still belong to that run. The owner also notes this
recording is appreciably noisier than the 5-ball clips, which is why it is the
acceptance piece rather than a convenience sample.

**Deferred / open**:

- Reader emits 25 series for a 19-trajectory file — six phantoms (ids 232,
  432–435, 440) decode as constant z ≈ −0.66 m; 232 claims 18 422 samples in a
  4 967-frame recording. Fix in P1 by gating on a matching piece table.
- **Resolved**: the `C:\Python311\Scripts` lock that blocked `.exe` shims (and
  therefore `ruff`, `mypy`, numpy 2) is sidestepped by a project venv, which has
  its own Scripts directory. Verified 2026-07-25: `ruff 0.16.0` and `mypy 2.3.0`
  install into `.venv` cleanly. Console-script entry points stay banned anyway
  (CLAUDE.md §5) so nothing depends on a shim.
- Environment verified for autonomous build: Python 3.11.3, Node 22.14.0,
  npm 10.9.2, `gh` 2.89 authenticated as `Jugleer` with `repo` + `workflow`
  scopes. Node's presence means the Airtime truth exporter (P3) and the viewer
  SPA (P8) are both buildable locally without owner intervention.
- Airtime's move to the `Project-DeepBlue-Juggling` org is deliberately deferred
  (its GitHub Pages URL would change and existing share links would break).

---

## Phase 0 — Toolchain, gate, docs    DONE (2026-07-25)

**Delivered**

- `pyproject.toml` reworked: `requires-python >=3.11` (matching DESIGN.md §14),
  `[project.scripts]` removed (CLAUDE.md rule 5), the `viz` extra removed, dev
  extras now `pytest`, `pytest-cov`, `hypothesis`, `ruff`, `mypy`.
- `tools/gate.py` — the four-stage gate (ruff check → ruff format --check → mypy →
  pytest), each stage run as `sys.executable -m <tool>` so nothing depends on an
  `.exe` shim. Prints a PASS/FAIL table and one `GATE GREEN` / `GATE RED` line.
  **Its definition is now frozen**; later phases add tests, not gate stages.
- ruff: 14 rule families selected (E/W/F/I/UP/B/C4/SIM/RUF/NPY/TID/PTH/ARG/RET/PL/DTZ),
  line length 100, `py311`.
- mypy: `strict = true` **project-wide** (not just `core/`), plus
  `warn_unreachable` and `ignore-without-code` / `redundant-expr` / `truthy-bool`.
  Stricter than PLAN.md asked for; all code here is new, so there was no reason
  to settle for less.
- **Core purity, enforced twice.** `src/juggling_analyser/core/.ruff.toml` is a
  nested ruff config (verified to apply: a probe module importing `time` and
  `juggling_analyser.io` inside `core/` raised TID251 twice) banning the wall
  clock, `random`, the global numpy RNG, all I/O and serialisation modules, and
  outward imports — including `ban-relative-imports = "parents"`.
  `tests/test_core_purity.py` re-implements the same rule as an AST walk, and
  *tests the detector itself* against a deliberately impure sample, so an inert
  checker cannot pass silently.
- `.github/workflows/ci.yml` — the same `python tools/gate.py` on 3.11 and 3.12,
  with `liblzo2-dev` installed because `python-lzo` builds from source on Linux.
- `tests/conftest.py` — corpus locator honouring `JA_DATA_DIR`, so the
  "gates green without the corpus" property (P10) is checkable without ever
  touching `data/`. `tests/` is now a package so tests can share it.
- Docs: `docs/qtm-format.md` sample-facts table corrected to the real ground truth
  (22 → drop → 2 collection → 31); README status line, install section (3.11+,
  `liblzo2-dev`, the gate command) and roadmap updated.

**Numbers**

    ruff check    : all checks passed (0 findings)
    ruff format   : 21 files, all formatted
    mypy (strict) : Success, 13 source files
    pytest        : 13 passed, 0 skipped, 0.5 s

**Decisions**

- **The `.qtm`/`.tsv` corpus in `data/` is committed** (17.8 MB across three clips
  plus the TSV oracle). PLAN.md P10 calls for "curated `.qtm` fixtures committed;
  larger corpus attached to a GitHub release" — these three cut clips *are* the
  curated fixtures, and committing them is what lets CI run the P1 TSV-oracle test
  and the P5 headline acceptance test for real rather than skipping them. The
  skip-if-absent path stays supported and is verified via `JA_DATA_DIR`.
- **The stale `.rrd` files in `data/` are left on disk, not deleted.**
  PLAN.md P1 says to delete them; ORCHESTRATOR_PROMPT.md §3 forbids deleting
  anything in `data/`. The forbidding rule wins. They are already matched by
  `.gitignore` (`*.rrd`), so they never enter the repository, which achieves what
  P1 actually wanted. The `viz` extra that produced them is gone.
- mypy's deprecated `numpy.typing.mypy_plugin` is not used (numpy ≥ 2.3 warns).
- Project-wide absolute imports (`from juggling_analyser.core... import`) rather
  than parent-relative ones, so TID252 is on everywhere and the core-purity
  relative-import ban is not the only thing holding the dependency direction.

**Deferred / open**

- `pyproject.toml` records no coverage threshold yet. DESIGN.md §14 holds
  `core/flight`, `core/link`, `core/pattern`, `core/metrics` to ≥ 90% line
  coverage; those modules do not exist yet, so the threshold lands with them
  (P2/P4/P6/P9) rather than as an empty promise now.
- `filterwarnings = ["error"]` is on. If scipy/numpy emit an unavoidable
  DeprecationWarning later, the fix is a *targeted* ignore with a comment, never
  removing the setting.

---

## Phase 1 — Reader v2: absolute timing, real trajectories    DONE (2026-07-25)

**Accepted, with every number measured.**

| Criterion (PLAN.md P1) | Result |
|---|---|
| gate green | green: ruff clean, mypy clean (19 files), 97 tests passed |
| reader reproduces the TSV frame for frame, all **19** trajectories, ≤ 1e-6 m | **5.000e-07 m** worst case over all 19 — see below |
| `info` reports 19, not 25 | 19 |
| exactly 5 trajectories active at frame 1 | 5 |

The 5.000e-07 m figure is not "within tolerance", it is **the floor**: the TSV
writes millimetres to three decimals, so text round-tripping alone costs up to
0.5 µm. The reader is bit-exact and the whole residual disagreement is the
export's own rounding. The test asserts both bounds, so a real regression cannot
hide behind the loose 1e-6.

### The format finding: `Data Items` is a typed-object stream, not flat TLV

The pre-phase note said "field id 17 is a per-trajectory piece table", read
through a flat TLV walk. That walk does not actually parse the stream — it breaks
77 bytes in. `Measurement NBC/Data Items` is a sequence of **objects**:
`<u32 type><u32 object_id><u32 field_count>` then `field_count` TLV fields.
**Type 10 is a Trajectory**, and its field 17 (`Parts`) is the piece table.

`Measurement NBC/Metadata` turned out to be a **schema** naming every object type
and field in id order, which converted the whole field map from inference to fact:
field 5 = Series ID, 8 = Trajectory Label, 9 = Colour, 12 = Trajectory Type,
13 = Point Type, 17 = Parts. Written up in `docs/qtm-format.md`.

**Two gates, not one, get from 25 decodable series to 19 trajectories** — PLAN.md
anticipated only the first:

1. the series must be referenced by a Trajectory object (excludes series 232, the
   one claiming 18 422 samples in a 4 967-frame recording);
2. `Trajectory Type` must be 1 (excludes five *more* series in the balls-only
   file, all motionless at z ≈ −0.67 m).

Those five are static rig markers. In the other two recordings the same physical
markers are Trajectory Type **1** and carry labels `base_0`..`base_4`, so type 2
records a per-project state (the owner removed them from the balls-only file)
rather than a property of the marker. Gating on it reproduces the export exactly;
`read_qtm(..., include_unexported=True)` returns them for inspection, and a test
asserts they are the statics.

**The parse is self-validating**, which is why it can be trusted rather than
merely believed. For every trajectory in all three recordings (24 / 62 / 25
objects): part lengths sum to the decoded sample count, *and* the per-sample type
flags equal the part types sample for sample. Both are hard errors in the reader —
a mismatch would mean the format model is wrong, and continuing would corrupt
everything downstream. `Point Type` also predicts the export's Mixed/Measured
column for all 19.

Also learned: QTM's **Mixed** means "contains gap-filled samples", *not* "has a
hole". Series 448's three parts `[1681-1691][1692-1693][1694-4967]` abut — no
frame is missing — and QTM still calls it Mixed. `Trajectory.is_contiguous` and
`Trajectory.has_gap_filled_samples` are therefore separate properties.

### Delivered

- `core/trajectory.py` rewritten: `Uncertainty` (isotropic / diagonal / full,
  with `cov` / `inv_cov` / vectorised `variances` / `sigma` / `take` / `scaled`),
  `Piece`, `Trajectory`, `Session`. All frozen, all validating their invariants in
  `__post_init__`, all `eq=False` where they hold arrays.
  **`Fragment` and `start_frame` are gone**, along with the unknown-start-frame model.
- `core/params.py` — DESIGN.md §13's constants in one place (`GRAVITY`,
  `BALL_MASS`, `BALL_DIAMETER`, `HAND_COUNT`, `RESIDUAL_SIGMA_FLOOR`). Not in the
  §2 module map; added under ORCHESTRATOR §7 because §13 requires one home for
  defaults. The algorithm knobs join it in P2 as they start being used.
- `io/qtm.py` rewritten: object scan, `Parts` parsing, label decoding
  (`u32 count` + UTF-16-LE), integrity checks, `read_qtm()` and `scan_qtm()`.
  `QtmScan` exposes what was skipped and why, so `info` reports it instead of
  hiding it.
- `io/tsv.py` — the oracle reader (subagent-built, reviewed). Three undocumented
  quirks it handles: CRLF line endings; the column-header line carries a trailing
  tab that data rows do not; `TIME_STAMP` is multi-valued, so a header line is not
  always `KEY<TAB>one-value`.
- `core/clean.py` adapted to the new model and the `ball`/`spurious`/`unknown`
  vocabulary of DESIGN.md §3 (was `ball`/`static`/`ghost`). The physics-based
  rewrite is P2's; this is lifetime and gross geometry only.
- `cli.py`: `info` now prints the frame, the source-series accounting, what was
  skipped, and `--all`.
- 97 tests: the oracle suite (11), the data model (30, two of them hypothesis
  property tests), reader invariants across the whole corpus (8 × 3 files), the
  TSV reader (25), core purity (9).

**Numbers from the corpus** (`info`):

    balls_only : 41 series -> 25 decodable -> 24 objects -> 19 read; 5 at frame 1
    5-ball     : 40 series -> 25 decodable -> 25 objects -> 24 read
    3-ball     : 77 series -> 63 decodable -> 62 objects -> 61 read; 8 at frame 1
                 (3 balls + the 5 base_N statics, which this file does export)

### Decisions

- **`f_s` is the Python identifier**, not `sample_rate`. NOTATION.md § Conventions
  says to spell symbols out, but DESIGN.md §3 and §10 both name the field `f_s`,
  it is already snake_case and unambiguous, and the JSON key must match. Recorded
  here as a deliberate deviation from the convention rather than an oversight.
- `Session.frame` records whether positions are in the QTM or the juggling frame,
  so §5's transform cannot be applied twice or forgotten. Not in DESIGN.md §3's
  field list; added because "which frame is this in" is otherwise unanswerable.
- `Trajectory.label` added (generic, a video tracker can label too). QTM-specific
  integers — display order, colour, trajectory type — deliberately stay in
  `io/qtm.py`'s `TrajectoryObject` and out of the source-agnostic model.
- `ruff allowed-confusables` now permits σ Σ τ × − · ≈ – ’, because NOTATION.md is
  normative and the docstrings quote it verbatim.

### Deferred / open

- **QTM's residual is used as an isotropic 1σ, clamped below at 0.1 mm.** It is a
  ray-intersection RMS, not a calibrated position σ, so the absolute scale of
  every uncertainty-weighted result inherits that assumption. Cheap to improve if
  the owner can supply a static-marker recording: the scatter of a motionless
  marker gives the real σ directly. Queued for `OWNER_ACTIONS.md`.
- The 20-byte trailer after `Parts` restates the trajectory's span
  `(1, first_start, last_end, 0, 0)`. Not modelled — nothing needs it.
- `Trajectory Type == 2` is understood operationally (not exported) but not
  semantically. If a future recording exports type-2 trajectories the gate would
  be wrong; the corpus offers no way to tell, so it is recorded, not guessed.
- Object types other than 10 in `Data Items` are unparsed. The scan locates type-10
  headers directly instead of walking the full grammar. Safe because every hit is
  cross-validated against its data series, and asserted: object ids come back
  unique and ascending, series ids unique, across all three files.
- `io/tsv.py` uses `@dataclass(frozen=True)` with ndarray fields, so `==` on two
  `TsvExport`s would return an array. Nothing compares them; worth `eq=False` next
  time that file is touched.
- No coverage threshold yet — still waiting on the modules DESIGN.md §14 names.

---

## Phase 2 — Flight segmentation and the juggling frame    PARTIAL (2026-07-25)

**Three of four acceptance criteria pass. The fourth fails, and the failure is a
property of the recordings, not of the code.**

| Criterion (PLAN.md P2) | Result |
|---|---|
| gate green | **PASS** — ruff clean, mypy strict clean (23 files), 245 tests |
| every detected flight's parabola residual below tolerance | **PASS** — 0 of 62 (3-ball) and 0 of 87 (5-ball) exceed the 5 mm tolerance; worst free-gravity residual 3.36 mm and 4.05 mm, medians 0.42 and 0.78 mm |
| fitted `g` within 2% of 9.80665 | **FAIL** — **−2.59%** (3-ball, 27 flights) and **−2.65%** (5-ball, 52 flights) |
| derived hand axis within 15° of nominal | **PASS** — **0.59°** and **1.56°** |
| frame round-trip identity to 1e-12 | **PASS** — worst 5.55e-16 m |

### The gravity finding — read this before Phase 9

Measured vertical acceleration in this corpus is **9.55 m/s², about 2.6% below
9.80665**. The evidence that this is the instrument and not the analysis:

- **Two independently recorded clips agree**: −2.59% and −2.65%. An earlier
  trimmed-interval analysis of all three files gave −2.88%, −2.87%, −2.87%,
  agreeing to 0.01 percentage points.
- **The best-determined flights show the largest deficit.** Binned by throw size,
  the longest and highest arcs (apex 0.57–0.72 m, 200+ samples) read −2.96%; short
  ones read −2.2% to −2.6%. If this were fit noise the relationship would run the
  other way.
- **Per-flight formal σ_g is 0.004–0.009 m/s²**, so the flight-to-flight spread of
  0.165 is real physical variation and the median is pinned to ~0.2%.
- **Fitting freely leaves a 1.2 mm residual where fixing `g = 9.80665` leaves
  4.2 mm.** The data is an excellent parabola — just not that one.
- **It is not the volume.** Correlation of fitted `g` with mean x, y, z, apex
  height and speed is weak (|r| ≤ 0.46) and *every* bin sits between −1.7% and
  −3.0%; no bin approaches 9.807.
- **It is not air drag.** Measured horizontal acceleration is 0.3–0.4 m/s² at
  horizontal speeds of ~0.5 m/s; quadratic drag on a 74 mm, 71 g ball at that
  speed is 0.004 m/s², two orders of magnitude too small.
- **It is not a tilted Z axis.** A tilt large enough to cost 2.6% of `g` (13°)
  would put 2.2 m/s² of gravity into the horizontal axes; the measurement implies
  a tilt of ~2.4°, worth 0.1%.
- **It is not the reader.** Ingestion reproduces a QTM TSV export to 5e-07 m (P1).

Two hypotheses remain, and ball trajectories alone cannot separate them:

1. a **length-scale** error of about −2.87% in the QTM calibration, or
2. a **timing** error — the true rate being 295.7 Hz rather than the 300 Hz the
   file reports, since `g_fit = g·(f_true/f_s)²`.

**One measurement settles it**, and it is in `OWNER_ACTIONS.md`: tape-measure two
of the static `base_N` markers. This reader measures `base_1`↔`base_2` as
**0.3149 m** and `base_3`↔`base_4` as **0.1165 m**, reproducible to 0.1 mm across
both recordings. If the tape says ~0.3242 m and ~0.1200 m it is scale; if it agrees
with 0.3149 m it is timing.

Consequences, so nothing downstream is surprised: siteswap extraction (P6) is
ordinal and immune; `t_air`, `t_d` and `τ_b` are correct under hypothesis 1 and
1.45% short under hypothesis 2; `z_apex` is 2.9% low under hypothesis 1;
**energy (P9) is affected either way** and its numbers must carry this caveat.

### Delivered

- **`core/flight.py`.** Savitzky–Golay derivatives (polyorder 2, exact for
  ballistic motion); uncertainty-widened ballistic test; mask closing; parabola
  boundary refinement; sub-sample apex from the fitted parabola; per-flight fixed-
  *and* free-gravity fits; `Flight`, `Carry`, `GravityCheck`, `FlightSegmentation`.
- **`core/frame.py`** (subagent-built, integrated). Derived origin and PCA hand
  axis, sign from the nominal mapping, orthonormality and right-handedness
  validated, plus `FrameDiagnostics` carrying the PCA eigenvalues and the axis's
  angular σ so a badly determined frame reports itself instead of looking confident.
- **`core/clean.py`**: `refine_with_flights`, the physics pass. On the 3-ball clip
  it moves 23 balls / 29 spurious / 9 unknown to 19 / 33 / 9, correctly demoting
  trajectories that look like balls but never fly like one.
- `Session.frame_transform` added — DESIGN.md §3 lists it and it was missing — so
  the transform is recorded rather than re-derived, and P7 can serialise it.
- `info` now reports flights, the gravity check with its caveat, and the frame.
- 148 new tests. Synthetic-truth tests measure *accuracy*; corpus tests pin the
  measured numbers so an algorithm change has to acknowledge moving them.

### Measured accuracy against synthetic truth

- Throw and catch instants land within **3 samples (10 ms)** of truth every time,
  and the flight *length* is recovered exactly (151 of 151 samples).
- `z_apex` matches `g·t_air²/8` to **3 mm**.
- Free-fit `g` on data that obeys `g` returns it to **0.05 m/s²**.
- **Robustness envelope**: every flight found at σ up to **2 mm**, four times the
  corpus's 0.2–0.5 mm. At 3 mm some are lost; at 5 mm none are found and none are
  invented. Asserted at 2 mm so the envelope cannot silently shrink.

### Decisions

- **Two-pass gravity calibration, default on** (`segment_session`). A fixed-`g`
  parabola on data with a `Δg` offset leaves a systematic `½·Δg·(t−t̄)²` — about
  2 mm RMS here, five times the 0.4 mm measurement noise — so the fit residual,
  which is supposed to be the event's *confidence*, would instead be a proxy for
  the instrument error. Pass one measures `g` freely; pass two fits with it.
  `GRAVITY` remains the reference and `gravity_check` always reports the measured
  value against it, so the discrepancy is surfaced, never absorbed. This is a
  deliberate deviation from DESIGN.md §6's single-pass description;
  `calibrate=False` restores it.
- **Two ballistic thresholds.** The design tolerance (`a_tol = 1.5`) decides
  whether a flight *exists*; the σ-widened tolerance decides how far it *extends*.
  With only the widened test, a noisy trajectory's σ can open the tolerance to
  several m/s² and admit spans that are not free flight — that produced 11 false
  flights on the 3-ball clip, one of them fitting `g = −0.58 m/s²`. Requiring half
  a minimum-flight's worth of strictly-ballistic samples removed all 11 and
  improved the derived hand axis from 3.96° to 0.59°.
- **`is_suspect` tests two things, not one.** A low residual proves a path is
  *smooth*, not that it is *falling*: a straight line fits a free quadratic
  perfectly with `g ≈ 0`. So a flight is suspect if its free-gravity residual
  exceeds 5 mm **or** its fitted `g` is more than 20% from the session's.
- **The smoothing window adapts to the noise**, with DESIGN.md §13's 21 samples at
  300 Hz as the floor. It widens only when the trajectory's median σ would push the
  propagated acceleration noise above `a_tol/2`, and is capped at the minimum
  flight length. This is what extends the robustness envelope to 2 mm; on the
  corpus it returns 21 unchanged, so DESIGN's default is what actually runs.
- Suspect flights and truncated flight ends are both excluded from the throw/catch
  cloud the frame is derived from. One suspect segment had placed a "catch" 1.5 m
  below the hands, which moved the origin and skewed the axis.
- New parameters in `core/params.py`, each with its derivation in the comment:
  `BALLISTIC_SIGMA_MULTIPLE = 3.0`, `BALLISTIC_CLOSE_SECONDS = 0.033`,
  `BOUNDARY_TOLERANCE = 1.5 mm`, `MAX_FLIGHT_RESIDUAL = 5 mm`,
  `MAX_GRAVITY_DEVIATION = 0.20`. **None of DESIGN.md §13's published defaults were
  changed.**
- `scipy-stubs` added to the dev extras: without it `mypy --strict` cannot check the
  `scipy.signal` / `scipy.ndimage` call sites at all.

### Deferred / open

- **Boundary bias.** DESIGN.md §6 defines the throw as the flight's first *sample*.
  The refinement admits two or three contaminated samples before that, so `t_air`
  carries a systematic bias of order +10 ms (~2% of a 0.5 s flight), measured on
  synthetic data. Solving for the sub-sample instant at which the path departs the
  parabola would remove it, but that changes a DESIGN definition — flagged for the
  owner rather than done unilaterally.
- **QTM's residual understates the true position error by roughly 3×.** Clean
  flights fit to ~1.2 mm where the reported σ is ~0.35 mm, and χ²/dof runs at 15–35
  rather than 1. Uncertainty-weighted results are therefore over-confident in
  absolute terms. A static-marker recording would calibrate σ directly.
- The `apex_height` vs `v_z²/2g` cross-check in DESIGN.md §9 is **not independent**:
  with `g` fixed the two are algebraically the same quantity, and the test that
  asserts it only guards the sign and factor. The genuinely independent check is the
  free-gravity fit, which is what is reported. A second real check — fitted apex
  against the maximum *observed* z — agrees to 1.4 mm (median) and is available but
  not yet asserted.
- `Carry` is per-trajectory, not per-ball; a true carry needs identity (P4/P5).
- The 3-ball `t_air` distribution has a p10 of 0.13 s, short for a 3-ball cascade
  and probably marking flights split by tracking loss. P4's linking should merge
  them; worth re-checking then.
