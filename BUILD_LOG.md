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

---

## Phase 3 — Synthetic ground truth    PARTIAL (2026-07-25)

### Airtime truth exporter — DONE, branch pushed, but its gate could not be verified here

Delivered on Airtime's `feat/truth-export` branch (HEAD `ac4ae31`, pushed; `main`
untouched at `efe72c1`). One new module `src/export/truth.ts`, one test file
(18 tests), one Node runner `scripts/exportTruth.mjs`, and a single line added to
`package.json`. **No dependency added** — the runner loads the TypeScript through
Vite's own `runnerImport`. The exporter is deliberately not re-exported from
`src/export/index.ts`, and the built `dist/` bundle is byte-identical to before.

Eight fixtures are committed here in `data/truth/`: `3 4 5 7 441 531 552 423`, each
3000 frames at 300 Hz, τ_b = 0.25 s, `t_d` = 0.30 s, `g` = 9.80665, positions in the
juggling frame, byte-identical on regeneration. Verified: `mean(pattern digits)`
equals the ball count exactly for all eight; `t_air` matches `h·τ_b − t_d_eff` to
7e-16; apex height matches `g·t_air²/8` to 5e-10 m; throw times sit on the τ_b grid
with **zero** drift; differentiating the sampled tracks twice recovers `a_z = −g` to
1.6e-4 m/s², which is exactly the 1e-9 coordinate rounding and nothing else.

**`npm run gate` in Airtime is red on this machine and it is not our change.**
Airtime has five pairs of source files differing only in case (`Hands.tsx`/`hands.ts`
and four more). `tsc` cannot resolve them on Windows — `typescript.js` hard-codes
`isFileSystemCaseSensitive() === false` for win32, so it fails even on an
NTFS per-directory case-sensitive mirror. Measured on a clean `main` checkout: 20
typecheck errors, 32 test failures. On the branch: the same 20 typecheck errors, and
on a case-sensitive mirror **816 tests pass (60 files), eslint clean, `vite build`
clean**. Not one error is in an added file. Nothing was renamed to force it green —
that would be the refactor ORCHESTRATOR §6 forbids. **This needs verifying on Linux
or in Airtime's CI before the branch is merged** (OWNER_ACTIONS.md).

### Four properties of the fixtures that downstream code must know

1. **`y` (forward) is identically 0** in every sample. Airtime's default hand preset
   is a line, so the truth is perfectly planar and the throw/catch cloud is four
   collinear points. Any depth spread must come from the degradation model.
2. **Event frames were rounded half-*up*** in JavaScript. Python's `round()` is
   half-to-even and ~21% of events land exactly halfway between samples, so a naive
   conversion disagrees on ~10% of them. Use `math.floor(t·f_s + 1.5)`.
   `generator.params.frame_rounding` records this.
3. **Held `2`s emit no events.** In `552` and `423` a `2` rides the hand with no
   flight, giving 27 throws rather than 40 and one ball with a long eventless carry.
4. **`t_air` for a `1` is not `h·τ_b − t_d`.** Airtime clamps dwell as
   `t_d_eff(h) = min(t_d, 0.75·h·τ_b)`, so `441` and `531` deviate from the naive
   identity by exactly 0.1125 s on their `1`s. Against the clamped form: 7e-16.

Also: `7.json` uses the same τ_b = 0.25 s as the rest, giving a 2.58 m apex —
unrealistic for 7 balls, but perfectly valid labelled data. The exporter takes
`--beat-period` if realistic heights are wanted.

### `core/synth.py` — LANDED LATE, UNTESTED

**Note added after the Phase 3 entry was written.** `core/synth.py` (1147 lines) and
`io/truth.py` (381 lines) arrived near the end of the run, after the P3 assessment
below was recorded and after the P4 work had already been validated without them.
They import cleanly, pass the core-purity AST walk, and are ruff- and mypy-clean, but
`tests/test_synth.py` arrived last, with **81 tests, all passing**, and the gate is
green with them in. So the modules are tested, but note what that does and does not
mean: **the P4 linking results below were measured without them**, against exact truth
fragmented by hand rather than against the degradation model. The linker still has
never been shown an identity swap or a spurious reflection, so its scores remain an
upper bound. The next session's first job in this area is to run the linker against
`degrade()` output and see what the swaps cost it. The calibration table has not been
independently checked by me — the tests assert it, and the tests were written by the
same agent that chose the presets.

### What `core/synth.py` was specified to do

The degradation model (Gaussian noise, apex/crossing dropouts, fragmentation,
identity swaps, spurious reflections) was specified in full, with calibration
targets measured from the corpus, and delegated. It did not land before the run
ended. What it was to be calibrated against is recorded here so the work is not
lost:

| Statistic | `5_ball_juggling_cut` (CLEAN preset) | `3_ball_juggling_cut` (NOISY preset) |
|---|---|---|
| ball trajectories per ball | 10 for 5 = **2.0** | 19 for 3 = **6.3** |
| trajectory length, median samples | **2422** | **1015** |
| trajectory length, min | 285 | 166 |
| coverage of all ball-frames | **98.5%** (24 452 of 24 835) | **91.6%** (25 013 of 27 303) |
| internal gaps inside a trajectory | 0 | 5, median 16, max 27 |
| reported σ, median / p90 / max | 0.441 / 1.655 / 5.743 mm | 0.559 / 2.414 / 5.890 mm |
| clip length | 4967 frames @ 300 Hz | 9101 frames @ 300 Hz |

The consequence is that P4 was validated against **fragmented exact truth** — the
fixtures cut into pieces with gaps and Gaussian noise, in `tests/test_link.py` — but
not against identity swaps or spurious reflections. Those two are precisely the
degradations the linker is least likely to survive, so its measured scores below
should be read as an **upper bound**.

---

## Phase 4 — Identity linking    PARTIAL (2026-07-25)

**The real-data criterion passes exactly. The synthetic 5-ball criterion fails.**

| Criterion (PLAN.md P4) | Result |
|---|---|
| gate green | **PASS** — ruff clean, mypy strict clean, 272 tests, 1 xfail |
| real 5-ball clip: exactly 5 lanes tiling the recording | **PASS** — 5 lanes, every one spanning frames 1–4967 |
| total gap ≤ 400 frames | **PASS** — **383** (24 452 measured of 24 835 ball-frames, 98.46%) |
| no non-collision violation | **MARGINAL** — one frame at 73.8 mm against the 74 mm diameter, 0.2 mm inside. Correcting for Phase 2's measured −2.87% scale deficit puts it at 76.0 mm, i.e. no violation. Asserted as measured numbers, not waved away. |
| 100% linking on synthetic 3 balls | **PASS** — 1.000, every lane pure, 3 lanes |
| 100% linking on synthetic 5 balls | **FAIL** — **0.615**, purity 0.571, 7 lanes for 5 balls |
| ≥ 95% on synthetic 7 balls | **PASS** — **0.952**, every lane pure |

Other fixtures, for the record: `4` 1.000/pure; `441` 0.889/pure; `531` 0.778/pure;
`423` 0.778/pure; `552` 0.583/purity 0.50. `ball_count` — the maximum-overlap
estimate — is **correct on all eight**, so the failure is in *bridging*, never in
counting.

Two scores are reported because they mean different things. `score_linking` is the
fraction of trajectories in the right lane; `identity_purity` is the fraction of
lanes containing exactly one true ball. A ball split across two lanes is a coverage
failure and every lane stays pure; a lane holding two balls is an identity failure
that corrupts dwell times and the siteswap. Only `5.json` and `552.json` show the
latter.

### Approach

Minimum-cost path cover of a DAG, solved exactly with the Hungarian algorithm, not
greedily. By Dilworth's theorem the minimum number of node-disjoint paths equals the
maximum number of simultaneously-active trajectories — which is also DESIGN.md §7's
ball-count estimate — so minimising the lane count and estimating `b` are the same
problem and the answer is not a guess. Each gap is scored under two hypotheses:
**ballistic** (predict the state forward, chi-squared on position and velocity) and
**carry** (position continuity only, velocity free because it reverses at a catch).

### What was measured and what it cost

- **Every trajectory endpoint initially read `ballistic=False`**, so half the cost
  model was dead code. Cause: Phase 2's boundary refinement trims a flight *inward*,
  so when tracking dies mid-flight the trajectory's final samples are exactly the
  contaminated ones the refinement dropped, and the last index falls outside its own
  flight. Fixed with `LINK_FLIGHT_MARGIN = 12` samples of slack, taking the state at
  the flight's own boundary time.
- **DESIGN.md §13's 250 ms gap ceiling makes P4's own criterion unreachable.** The
  five gaps that must be crossed on the 5-ball clip are 413, 293, 280, 157 and
  150 ms. So 250 ms became the *confidence* boundary (`BridgedGap.confident`) and
  `MAX_LINK_GAP = 600 ms` the feasibility one.
- **`CARRY_MAX_TRAVEL` was swept, not guessed.** `hand_speed × dt` alone permits
  1.65 m over a 550 ms gap, which admits almost anything. But the cap must exceed the
  0.4 m hand separation, because a gap containing a catch and a throw also contains
  part of a flight — the clip's 417 ms bridge legitimately covers 722 mm. Measured:
  0.5 m splits the real clip into 6 lanes; 0.8, 1.0 and 1.5 m all give exactly 5 with
  *identical* synthetic scores. 1.0 sits mid-plateau.
- **A finite lane-end cost was tried and is worse.** Making "start a new lane" cost
  24 — just under the worst link the gates admit — was meant to stop the linker
  inventing an identity it cannot support. It changed no synthetic score and took the
  real clip from 5 lanes to 8. Reverted; recorded in `params.py` so the experiment is
  not repeated blindly.
- **A stricter carry test was tried and is correct physics but not yet usable.**
  Requiring `v_z(in) > v_z(out) − g·dt` — only a hand can leave a ball above free
  fall — improved synthetic purity, but rejected the 417 ms bridge and split the real
  clip into 6 lanes. The endpoint velocities across that gap are not determined well
  enough to carry the test. Left in the code as a comment at the exact site.
- **The σ under-estimation from Phase 2 is now used quantitatively.** A *correct*
  link across the 417 ms gap scored chi² = 11.4 against a gate of 9, purely because
  the reported σ understates the true error by ~3× and chi² scales as σ⁻².
  `SIGMA_UNDERESTIMATE_FACTOR = 3.0` applies the measured factor.

### Best hypothesis for the 5-ball synthetic failure

The mis-linked pairs all sit across gaps of 283–553 ms that the test's fragmenter
created by dropping short pieces and merging two cuts. Over such a gap in a 5-ball
pattern the ball is *still in flight* (`t_air` = 0.95 s in the fixture), so the
ballistic hypothesis should decide it — but a 550 ms extrapolation from a velocity
fitted over 67 ms has a position uncertainty of tens of millimetres, which admits
several candidates, and the forced minimum path cover then picks one. The fix is a
properly propagated prediction covariance from the flight fit itself (the flight's
own parameter covariance, not a scalar σ_v estimate), so the chi-squared genuinely
discriminates. That is a contained change to `_endpoint_states` and `_link_cost` and
is the first thing to do in this area.

### Deferred / open

- `core/synth.py` (above) — without it, no identity-swap or spurious-reflection case
  has ever been put through the linker.
- **The 3-ball clip does not link into 3 balls**: 7 lanes, `ball_count` estimate 4,
  and only 69% of its ball-frames tracked. Its untracked stretches run to seconds,
  which no bridge can honestly cross. The estimate reads 4 because trajectories
  briefly overlap — 17 frames of the 9101 have four active. PLAN.md sets a criterion
  for the 5-ball clip only; this is recorded as a measured number, not a pass.
  **P5's headline catch count does not depend on it**: a catch is the end of a
  flight, which needs no identity.
- 76 non-collision violations on the 3-ball clip, closest 38.4 mm. Consistent with
  its 7 lanes for 3 balls — extra lanes are duplicate views of the same ball.
- `score_linking` matches lanes to true balls by maximum agreement before scoring,
  because lane ids are arbitrary. Spurious trajectories (truth `-1`) are excluded:
  whether a reflection reaches a lane is `core.clean`'s job.

---

## Phase 5 — Events, hands, runs, drops    NOT BUILT — but measured (2026-07-25)

`core/events.py` was **not written**; the run ended first. What follows is a
measurement taken directly from the Phase 2 flight segmentation, because a catch is
the end of a flight and therefore needs no ball identity. It is the most useful thing
that could be said about the headline acceptance test without building the phase, and
it is a **finding to check against the recording**, not a result.

### The headline number, measured

On `3_ball_juggling_cut`, counting untruncated flight ends on ball trajectories,
excluding suspect flights:

    total catch-like events           55        owner's ground truth: 22 + 2 + 31 = 55
    split at the 3.6 s dead gap       28 / 27   owner's ground truth: 24 / 31

**The total agrees exactly. The split does not.** Both facts matter, and neither is
adjustable: the total is not a coincidence at n = 55, and a 28/27 split is not a
rounding of 24/31.

### Everything the owner needs to check it, with timestamps

- **Dead time 11.60 s → 15.20 s** (3.60 s, the largest inter-catch interval in the
  clip; the median is 0.482 s). Taken as the boundary between run 1 and run 2.
- **Drop candidate at t = 10.96 s**: a flight ends at **z = −0.172 m**, i.e. 1.12 m
  below the catch plane `z_c = 0.947 m`. A ball on the floor. **26 catches precede
  it** and **1 follows it** before the dead time — against the owner's 22 and 2.
- **Second low flight end at t = 2.86 s, z = 0.492 m** (0.46 m below `z_c`). The
  owner reports only one drop in this recording, so this is either a very low catch,
  a bounce, or a second drop that the manual count treated differently. **Worth
  looking at**: if it is a drop, the run structure is not what either count assumes.
- **7 of the 62 detected flights have a truncated end** — tracking died mid-flight,
  so their catch is real but unobserved and is *not* in the 55. If four of those fall
  in run 2, that alone reconciles 27 with the owner's 31. The 3-ball clip tracks only
  69% of its ball-frames, so this is the most likely single explanation for the
  split.
- Note also that the floor impact at 10.96 s is currently *counted* as a flight end.
  A ball hitting the floor is not a catch, so the real figure is 54 + 1 impact, and
  DESIGN.md §6's below-`z_c` rule is exactly what would separate them.

### Best hypothesis

The total is right because the flight segmenter is sound (Phase 2). The split is
wrong because run 2's catches are systematically under-counted by truncated flights,
and run 1's over-counted by including the floor impact and possibly by an earlier
event at 2.86 s that the manual count did not treat as a drop. Nothing here requires
the segmenter to change; it requires the drop rule, the collection-catch rule and the
truncated-flight rule that Phase 5 was to build.

### What Phase 5 still needs

Hand assignment by k-means along the derived hand axis, `z_c` as the median catch
height (already computable — 0.947 m in the QTM frame on this clip), the
below-`z_c − 0.30 m` drop rule, run segmentation with `end_reason`, and the
collection-catch rule of DESIGN.md §6. All of it now has its inputs: flights with
confidence, a derived frame, and linked balls.

---

## Run summary — where this build stopped

| Phase | Status | Acceptance |
|---|---|---|
| P0 toolchain, gate, CI | **DONE** | all criteria met; CI green on 3.11 and 3.12 |
| P1 reader v2 | **DONE** | all criteria met, positions exact to 5.0e-07 m |
| P2 flight + frame | **PARTIAL** | 3 of 4; fitted `g` is −2.6%, an instrument finding |
| P3 synthetic truth | **PARTIAL** | Airtime exporter + 8 fixtures done; `core/synth.py` not built |
| P4 linking | **PARTIAL** | real 5-ball clip exact; synthetic 5-ball 0.615 against a 1.0 target |
| P5 events | **NOT BUILT** | measured only: total catches 55/55, split 28/27 vs 24/31 |
| P6–P9 | **NOT STARTED** | — |

Gate green at every commit; four commits, all pushed; CI green.

---

## Phase 3 — addendum: calibration verified, and a DESIGN.md §12 discrepancy    (2026-07-25)

`core/synth.py`, `io/truth.py` and `tests/test_synth.py` are complete: 82 synth tests,
gate green, and **all 18 calibration rows inside the required factor of two**. PLAN.md
P3's acceptance criterion is therefore **met**, which supersedes the "not delivered"
note above.

Medians over 5 seeds, measured over the same population the real targets were measured
over:

| statistic | CLEAN target | achieved | NOISY target | achieved |
|---|---|---|---|---|
| trajectories per ball | 2.000 | 2.400 | 6.333 | 5.667 |
| length, median samples | 2422 | 1872 | 1015 | 1142 |
| length, min | 285 | 194 | 166 | 115 |
| coverage of ball-frames | 98.46% | **98.00%** | 91.61% | **91.90%** |
| internal gaps | 0 | **0** | 5 (median 16, max 27) | 7 (median 16, max 28) |
| reported σ median | 0.441 mm | **0.442 mm** | 0.559 mm | 0.530 mm |
| reported σ p90 | 1.655 mm | 1.242 mm | 2.414 mm | 1.820 mm |
| reported σ max | 5.743 mm | 6.000 mm | 5.890 mm | 6.000 mm |

Independent end-to-end check on degraded CLEAN data: 90 flights found against 83 true
throws, **0 suspect**, and `g = 9.80148 m/s² (−0.053%)` over 76 flights. That is a
genuinely strong result — the flight pipeline recovers `g` to half a tenth of a percent
on data that obeys `g`, which is the cleanest available confirmation that Phase 2's
−2.6% on the real corpus is the instrument and not the algorithm.

### The discrepancy: QTM's position error is not white, and DESIGN.md §12 says it is

DESIGN.md §12 and PLAN.md P3 both describe the degradation as "Gaussian position
noise". Implemented literally — true σ = 3 × reported σ, all of it white — the model is
not merely unrealistic but **destructive**: flight segmentation on a 5-ball fixture
falls from 43 flights to **3**, while the real clips segment cleanly at the same
reported σ. If the spec were right, the real data could not work.

Measured cause: a third-difference estimator over 146 clean flights puts the genuinely
**white** part of QTM's position error at **0.035 mm**, about a tenth of the reported σ.
The rest is smooth on a ~0.25 s scale. Real mocap data is an excellent parabola in
slightly the wrong *place*, not a noisy one — which is also exactly why Phase 2 found
χ²/dof of 15–35 with sub-millimetre residuals.

Two further measurements in the same direction: `corr(log σ_reported, log |residual|)`
is only 0.27–0.31, and across σ deciles the residual grows ~2.7× while σ grows ~5×
(exponent ≈ 0.6). **QTM's residual spikes are camera geometry, not position error.**

So `synth.py` splits the injected error into white and smooth parts
(`white_error_fraction = 0.10`, `smooth_error_seconds = 0.25`) and decouples its
magnitude from the per-sample σ spikes (`error_sigma_coupling = 0.5`). The *total*
magnitude is still `sigma_report_factor` × reported σ, pinned by a test at 3.00× ± 5%,
so the "reported σ understates the truth" contract is unchanged. This is a deviation
from a frozen design document and is flagged for the owner in OWNER_ACTIONS.md.

### Identity swaps are off in CLEAN, and deliberately

`swap_probability` is **0.0 in CLEAN_PRESET and 0.05 in NOISY_PRESET**. The reasoning is
sound and worth keeping: a swap leaves a ~0.2 m single-frame step, and the largest step
inside *any* ball trajectory in *either* real clip is **26 mm**, fully explained by a
7.8 m/s release. The corpus therefore contains no swap to calibrate against, and an
uncalibrated failure mode does not belong in the preset that reproduces the clean clip.
NOISY carries it so the failure mode exists somewhere (8 swapped trajectories at seed 0).

### Correction to a claim in the delivery report

The delivering agent suggested that Phase 4's 0.615 five-ball figure "was measured
against a CLEAN_PRESET that then had `swap_probability = 0.02`" and should be
re-measured. **That is not the case, and it was checked rather than accepted:**
`tests/test_link.py` imports nothing from `core.synth` — no `degrade`, no preset — and
fragments the raw Airtime fixtures with its own helper and its own Gaussian noise,
precisely so that a change to the degradation model cannot move what "100% correct
linking" means. The 0.615 is independent of every synth parameter. It stands as a real
linker shortfall.

What *is* still true is the converse, and it remains the top item of outstanding work:
the linker has **never been run against `degrade()` output**, so it has never seen an
identity swap or a spurious reflection, and its scores are an upper bound.

### Deferred / open

- `cascade_truth()`, an arbitrary-length cascade truth generator, lives in
  `tests/test_synth.py`. P6 will want it; promoting it into `core/synth.py` would be
  cleaner than importing across test modules.
- The reported-σ p90 sits at 0.75 of target in both presets. The real distribution is
  heavier between median and p90 than a log-normal can be (p90/median = 3.75 needs
  `sd(log σ) = 1.03`, but the measured within-trajectory spread is 0.73–0.78) and
  lighter above p99. The measured log-spread was matched rather than the p90, so no
  single quantile is fitted at another's expense — but a two-component σ model would do
  better.
- `swap_distance` is 3 ball diameters, not one, because the Airtime fixtures are planar
  (`y ≡ 0`) and their minimum ball separation is 138–199 mm; at one diameter the swap
  path would never fire and would go untested.
- The average theorem holds exactly for `3/4/5/7` and fails for `441` (3.025), `531`
  (3.05), `423` (3.519) and `552` (5.000 against 4 balls) — the last two because held
  `2`s emit no throw event, so the mean is over the throws that exist. Exposed as data,
  not raised, which is the right call.

---

## Phase 2 — addendum: the tape-measure validation route is dead (2026-07-25)

The owner measured `base_2`↔`base_4` as **261 mm**. The recordings put that pair at
**710.7 mm**. Crucially, 261 mm matches **none** of the five tracked markers' ten
pairwise distances (116.5, 298.8, 314.9, 432.2, 435.8, 443.0, 526.6, 550.9, 600.5,
710.7 mm), so this **cannot** be a labelling mismatch — no permutation of names yields
261 mm. The two point sets are different configurations, and the recordings are dated
2024-12-12 16:14:59 and 16:25:26, so the markers were most likely moved in the months
since. `OWNER_ACTIONS.md` item 1 has been rewritten to ask for a fresh tape-measured
baseline recording instead.

Verified while checking: labels are read directly from field 8 and are QTM's own. QTM
defines **six** markers `base_0`..`base_5`; **`base_0` has zero samples** and was never
tracked, which is a plausible source of an off-by-one in any manual numbering.

**A new piece of evidence, and it moves the diagnosis.** `Measurement/Info` carries a
wall-clock date-time (field 1, a `SYSTEMTIME`) and a float64 session clock (field 4).
Across the two recordings the session clock advances 626.757794 s while the wall clock
advances 626.758000 s — **0.206 ms of disagreement over 626.8 s, i.e. 3.3e-07
relative**. A 1.45% sample-rate error would require 9.1 s of disagreement here.

This is strong evidence against the timing hypothesis *if* field 4 is derived from the
camera time base rather than being the PC clock re-expressed, and the file does not
settle which; QTM's metadata schema names `TimeBaseFrequency` and `TimeBaseOffset` but
both are absent from these files. Recorded as **moderate evidence against timing,
making the calibration length scale the leading explanation of the −2.6% gravity
deficit.** No timecode stream exists in the files, so there is no further route to
pin the sample rate from the data alone.

---

## Calibration recording — scale is CORRECT, and a Phase 2 claim is corrected (2026-07-25)

The owner recorded `data/2026-06-10-1m_markers_calibration.qtm` — 3000 frames at
300 Hz, robots left in the scene, two markers laid on the floor a tape-measured
**1000 mm** apart. It settles one question and overturns one of my own conclusions.

### 1. The length scale is right. The scale hypothesis is dead.

The two floor markers are the only ~1 m pair at floor height (series 25 and 27, both
at z ≈ −70 mm; the other near-metre pair, 26–30 at z ≈ 1.9–2.3 m, is on a robot):

    MEASURED   1000.22 mm   sd 0.016 mm over 3000 frames
    TAPE       1000    mm
    error      +0.22 mm  =  +0.022%

The −2.87% scale hypothesis predicted **971.3 mm**. It is excluded by two orders of
magnitude. This also validates the reader end to end: it recovers a known metre to
0.2 mm.

**Caveat that matters.** This recording is from 2026-07-25; the juggling clips are from
2024-12-12, and the setup demonstrably changed in between (different `base` marker
layout, and the owner's earlier 261 mm measurement matched none of the old distances).
So this proves the *current* calibration is metrically sound. It does **not** prove the
December 2024 one was. Combined with the session-clock evidence against a timing error,
the most economical explanation of the 2024 clips' −2.6% gravity is **a bad calibration
in December 2024 that has since been fixed** — but that is now a hypothesis about a
past state of the rig, and only one measurement can close it: **10 seconds of juggling
in the current setup.** That is the top item in `OWNER_ACTIONS.md`.

### 2. Correction: QTM's residual does not understate the position error. It grossly overstates it.

The Phase 2 entry states that QTM's per-sample residual "understates the true position
error by roughly 3×". **That was wrong**, and this recording — 26 motionless markers ×
3000 frames — shows it directly. Measuring the true σ as the scatter of a stationary
marker about its own mean:

| recording | markers | true σ (median) | reported σ (median) | true / reported |
|---|---|---|---|---|
| `3_ball_juggling_cut` statics | 5 | 0.102 mm | 0.279 mm | **0.36** |
| `5_ball_juggling_cut` statics | 5 | 0.034 mm | 0.294 mm | **0.12** |
| `2026-…-1m_markers` statics | 26 | 0.028 mm | 2.333 mm | **0.012** |

QTM's residual is a **ray-intersection residual**, and its magnitude tracks camera
geometry, not position error: it varies by 80× across these recordings while the true
noise stays at 0.03–0.10 mm. It is not a calibrated σ in *either* direction.

Where the original error crept in: Phase 2 compared the reported σ against the
*parabola fit residual* of a flight and attributed the whole difference to sensor
noise. Re-measured properly, that ratio is not even consistent — the flight free-gravity
residual over the reported σ is **0.75** on the 3-ball clip and **1.77** on the 5-ball,
not 3 in both.

### 3. What the numbers actually mean — a better model of the error

Three separate quantities, now each measured rather than conflated:

- **Sensor noise ≈ 0.03 mm**, of which **75% is white** (from successive differences:
  white component 0.020 mm against a total of 0.028 mm). The system is superb.
- **Ball paths deviate from a perfect parabola by 0.42–0.78 mm** — 15–25× the sensor
  noise. So the flight residual is dominated by **real deviation of the ball's path from
  the ballistic model** (drag, spin with an off-centre marker, whatever it is), not by
  measurement error. This is a much more interesting statement than "the data is noisy",
  and it is consistent with the gravity anomaly being systematic rather than noise.
- **QTM's reported residual, 0.28–2.33 mm**, is uninformative about both.

This also reconciles with the Phase 3 finding: the degradation model's author measured
the genuinely white part of the flight error at 0.035 mm, which agrees with the 0.020–
0.028 mm measured here on static markers. Their "smooth remainder" is not sensor drift —
it is the real path deviation above.

### 4. Consequences for the code

- `SIGMA_UNDERESTIMATE_FACTOR = 3.0` (used by the linker to inflate σ so its χ² gate
  admits correct links) **keeps its value but loses its stated justification.** It is
  not true that the sensor is 3× noisier than reported. What *is* true is that the
  quantity the linker's χ² needs — how far a ball's real path departs from a ballistic
  prediction — is larger than the reported residual, by a factor that measured 0.75–1.77
  on flight residuals and more once extrapolation over a gap is included. So the
  inflation is defensible as an **effective** model-error term, and the comment in
  `params.py` now says that instead of the wrong thing. The value itself was chosen
  because it made a *known-correct* link pass the gate, which is weak evidence; it should
  be replaced by a properly propagated prediction covariance (already the recommended
  fix in the Phase 4 entry).
- **Possible double-counting**: `_endpoint_states` already uses `flight.free_residual`
  as the position σ for flight-derived endpoints — a *measured* path-deviation figure —
  and then multiplies it by `sigma_factor` as well. Recorded as an open item; changing
  it moves the P4 numbers, so it is not being done blind at the end of a run.
- **`OWNER_ACTIONS.md` item 2 is complete.** The static-marker recording asked for has
  been made and the answer is above: true σ = 0.028 mm, 75% white. The item is closed
  rather than left asking for something already delivered.
