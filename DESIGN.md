# DESIGN.md — juggling-analyser

The frozen design. Read `NOTATION.md` first — its symbols, frames, and terms are
normative and are used without re-definition here. `PLAN.md` implements this
document phase by phase; § references from other documents point here.

Status: design frozen 2026-07-25. Changes require an entry in `BUILD_LOG.md`
recording what changed and why.

---

## 1. Product scope

### v1 (this build)

Point the tool at a Qualisys `.qtm` recording of solo ball juggling and get:

- **Ball count** over time.
- **Siteswap** — the vanilla async pattern being juggled, per run, with sections
  that are not valid vanilla siteswap labelled as such rather than force-fitted.
- **Runs** — automatic segmentation into spans of continuous juggling, each ended
  by a drop or a deliberate stop, with catches and duration per run.
- **Session totals** — total catches, longest run, most catches in a given pattern,
  total airtime, drop count.
- **Per-throw metrics** — throw height (`z_apex` above the release point), air
  time, dwell time and dwell ratio, beat period.
- **Energy** — mechanical work done on the balls: `W⁺`, `W⁻`, `W_net`, and average
  power `P̄`, in joules and J/kg, per run and per session.
- **Accuracy and repeatability** — raw distributions (not a composite score):
  apex scatter per hand, throw-height spread, dwell-time spread, beat-period
  jitter, catch-position scatter, left/right symmetry.
- **3D replay** — a local web viewer: balls at true 74 mm scale, configurable
  trails, scrubbable timeline, synchronised metric panels, an instrument aesthetic
  that is configurable toward the expressive.
- **Session JSON** — a versioned, documented analysis output persisted per
  recording, so future longitudinal features have retroactive data.

### Explicitly deferred (documented, not built in v1)

- **Measured-vs-ideal overlay** (your recording against the simulated ideal
  pattern) — v2. It is the strongest reason to converge viewers with Airtime, so
  §11 keeps the module boundaries compatible.
- **Real-time analysis** via the Qualisys RT SDK. Batch-first is deliberate: the
  offline pipeline uses global optimisation and backward smoothing, which a causal
  filter cannot. Real-time gets its own tracker later, behind the same `Session`
  contract.
- **2D video ingestion** (Hawkeye-like). Confirmed as a major future direction;
  §3 carries per-sample covariance from day one specifically so that video's
  anisotropic uncertainty drops in without reshaping the model.
- **Robotic juggling** beyond `n_h = 1`. Jugglebot is a single hand today; the
  hand model is a list, not a left/right pair, so multi-arm robots do not require
  a redesign. Sync and multiplex notation are out of scope for v1.
- **Longitudinal progress tracking** across sessions.
- Non-vanilla notation (sync, multiplex, passing).

---

## 2. Architecture: measurement in, notation out

One source-agnostic data model sits between ingestion and everything downstream, so
the mocap reader, a future real-time stream, and a future video front-end all feed
the same pipeline.

```
.qtm ──▶ io.qtm        ──▶ Session[Trajectory]     absolute frames, per-sample Σ
     ──▶ core.clean    ──▶ reject non-ball trajectories
     ──▶ core.flight   ──▶ flight / carry segmentation, throw & catch candidates
     ──▶ core.frame    ──▶ derive origin + hand axis, rotate into the juggling frame
     ──▶ core.link     ──▶ Trajectory[] → Ball[]   identity across gaps
     ──▶ core.events   ──▶ throws, catches, apexes, drops, hand assignment
     ──▶ core.pattern  ──▶ beat grid, siteswap extraction, run segmentation
     ──▶ core.metrics  ──▶ per-throw, per-run, per-session
     ──▶ io.session    ──▶ versioned session JSON
                            └──▶ viewer (local server + browser SPA)
```

**Core purity** (CLAUDE.md rule 1) is the load-bearing decision, the analogue of
Airtime's "pure function of time". `core/` is pure, deterministic, and I/O-free:
the same recording always yields a byte-identical analysis, results diff cleanly
between algorithm changes, and every stage is testable against synthetic ground
truth without touching a file. Dependency direction is strictly
`cli / viewer / io → core`, never the reverse.

### Module map

```
src/juggling_analyser/
  core/
    trajectory.py   Trajectory, Ball, Uncertainty, Session — the data model
    clean.py        non-ball trajectory rejection
    flight.py       ballistic segmentation; throw/catch/apex detection
    frame.py        juggling-frame derivation and transformation
    link.py         trajectory → ball identity linking, gap bridging
    events.py       event consolidation, hand assignment, drops, runs
    pattern.py      beat grid, siteswap extraction and validation
    metrics.py      all metric definitions (§9)
    synth.py        mocap degradation model for synthetic ground truth (§12)
  io/
    qtm.py          the .qtm reader (docs/qtm-format.md)
    tsv.py          QTM TSV reader — validation oracle only, never the pipeline
    session.py      session JSON read/write, schema versioning
  viewer/
    server.py       local HTTP server; serves the SPA and session JSON
    app/            the browser SPA (React + three.js), see §11
  cli.py            info | analyse | serve
```

---

## 3. Data model

All types are immutable dataclasses of numpy arrays. Positions in metres in the
juggling frame (except immediately after reading, see §5).

**`Uncertainty`** — per-sample position uncertainty, stored in whichever form the
source provides and read through one accessor:

- `isotropic` — one σ per sample `(N,)`. Mocap: derived from QTM's residual.
- `diagonal` — `(N, 3)` per-axis σ.
- `full` — `(N, 3, 3)` covariance. What 2D video will need (precise in the image
  plane, vague in depth).

Downstream code only ever calls `.cov(i)` / `.inv_cov(i)`, so adding video later
changes no consumer. Storing isotropic mocap data costs one float per sample rather
than nine, which matters for 10-minute recordings.

**`Trajectory`** — what QTM tracked, *not* a ball:

| field | meaning |
|---|---|
| `id` | source identifier (the `.qtm` data-series id) |
| `frames` | `(N,)` int, **absolute 1-based frame index per sample** |
| `positions` | `(N, 3)` m |
| `uncertainty` | `Uncertainty` |
| `sample_type` | `(N,)` uint8 — 1 measured, 2 gap-filled (from the piece table) |
| `pieces` | `[(start_frame, end_frame, type)]` as stored in the file |
| `kind` | `ball` \| `spurious` \| `unknown` |

`frames` is explicit rather than implied by a start offset, because a Mixed
trajectory has internal gaps. There is no such thing as an unknown start frame
(see §5) — that was an early misreading of the format and is now closed.

**`Ball`** — a physical ball reconstructed by linking (§7): an ordered list of
`(trajectory_id, frame_range)` spans, plus bridged gaps flagged as inferred.

**`Event`** — `kind` (throw / catch / apex / drop), `time`, `position`, `ball`,
`hand`, `confidence`.

**`Run`** — `start_time`, `end_time`, `end_reason` (drop / stop / recording-end),
`catches`, `pattern`, per-run metrics.

**`Session`** — source, `f_s`, frame count, trajectories, balls, events, runs,
session metrics, and the frame transform that was derived.

---

## 4. Ingestion

`.qtm` is an undocumented OLE2 + LZO1X format, fully reverse-engineered in
`docs/qtm-format.md`. No QTM installation and no export step is required.

Two facts drive the reader:

- **Trajectories carry absolute frame ranges.** `Measurement NBC/Data Items`
  field id 17 is a per-trajectory piece table: `count`, then
  `count × (start_frame, end_frame, type)`, 1-based inclusive. A trajectory's data
  series is the concatenation of its pieces, so piece lengths sum to the decoded
  sample count — that is how a piece table is matched to a series.
- **Not every data series is a trajectory.** The reader must accept only series
  that have a matching piece table. Without that gate it invents phantom
  trajectories (six of them in the 5-ball sample, decoding as a constant
  z ≈ −0.66 m; one claimed 18 422 samples in a 4 967-frame recording).

Validation: `io.tsv` reads a QTM TSV export purely so tests can assert the reader
reproduces it frame for frame. The pipeline never reads TSV.

---

## 5. The juggling frame

Derived, not assumed (NOTATION.md § Frames of reference).

1. Read positions in the QTM frame.
2. Segment flights (§6) on the raw trajectories — ballistic detection needs no
   frame and no identity, only that `a ≈ (0, 0, −g)`, and Z is common to both frames.
3. Collect all throw and catch positions. Their mean is the **origin**.
4. The **hand axis** is the principal axis of that cloud in the horizontal plane
   (PCA on x, y about the origin).
5. The sign — which end is the right hand — is resolved by the nominal mapping
   `x_J = −y_Q, y_J = x_Q, z_J = z_Q`, choosing the direction with positive
   projection onto nominal `+x_J`.
6. Everything is transformed into the juggling frame; the transform is recorded in
   the session output so any result can be mapped back to raw QTM coordinates.

The floor and the QTM calibration origin are discarded: neither is reliably
identifiable in this data (some static markers sit on a robot on a shelf, others
are stray reflections) and no v1 metric needs them. Throw height is measured
above the release point, not above the floor.

---

## 6. Flight segmentation and event detection

A ball is in **flight** when its acceleration is `(0, 0, −g)` within tolerance.
This is the single most reliable signal in the data and everything else is built
on it.

- Acceleration comes from a Savitzky–Golay filter over the position samples
  (second derivative), window chosen from `f_s` and the shortest expected flight.
  Never a raw finite difference: differentiating 300 Hz data twice amplifies noise
  by ~`f_s²`.
- A sample is *ballistic* when `‖a − (0,0,−g)‖ < a_tol`, weighted by the sample's
  uncertainty. Contiguous ballistic samples longer than a minimum duration form a
  flight; everything else is a carry.
- **Throw** = first sample of a flight. **Catch** = last sample. **Apex** = the
  zero-crossing of `v_z` inside a flight, refined by fitting the parabola rather
  than taking the sample maximum (sub-sample accuracy, immune to noise at the
  turning point where velocity is smallest).
- Each flight is fitted to a parabola with gravity fixed; the fit residual becomes
  the event's confidence, and a poor fit marks a segment as suspect rather than
  silently accepting it.

**Catch plane** `z_c` = the median height of all detected catches in the session.

**Drop vs occlusion.** When a ball's track ends mid-pattern:

- ending **below** `z_c − z_drop_tol` while unheld → **drop**; ends the run.
- ending **above** `z_c` → **occlusion**; bridged ballistically if the gap is short
  enough for the bridge to be unambiguous, otherwise the run is marked *uncertain*
  and the affected metrics are flagged, never silently interpolated.

**Hand assignment.** With no hand markers, hands are inferred from the catch/throw
cloud: k-means with `k = n_h` along the hand axis, seeded by the sign convention of
§5. For `n_h = 1` (Jugglebot) the step is a no-op. Hands are a list, not a
left/right pair, so a multi-arm robot needs no redesign.

**Run segmentation.** A run starts at the first throw after dead time and ends at
a drop, a deliberate stop (all balls held simultaneously with no throw following),
or the end of the recording. `end_reason` distinguishes them, because "31 catches
then the recording ended" is not the same result as "31 catches then a drop".

**A drop does not end the run instantly.** When a ball is dropped, the balls
already in the air still come down and are still caught. Those trailing
**collection catches** belong to the run that just ended — they are real catches,
made under the same pattern. So a run ends at the drop *event*, but its catch
count continues until the last airborne ball is collected or hits the ground.
`3_ball_juggling_cut` is exactly this case: 22 catches, the drop, then the two
balls that were in flight are caught — 24 catches in that run. The same rule
covers a deliberate stop, where the juggler simply collects everything.

---

## 7. Identity linking

A QTM trajectory is not a ball: tracking breaks at apexes and crossings, so one
ball spans several trajectories and the pipeline must decide which trajectory
continues which ball.

Absolute timing makes this a well-posed assignment problem rather than blind
multi-target tracking. In the 5-ball sample, ball trajectories cover 24 452 of the
24 835 ball-frames — **98.5%** — leaving 383 frames of gap in a 16.6 s clip.

The linker assigns trajectories to `b` lanes subject to:

- **Non-overlap** — one trajectory cannot be two balls; two trajectories of the
  same ball cannot overlap in time.
- **Coverage** — the lanes must tile the recording with only small gaps.
- **Ballistic continuity** — across a gap, the predicted state from the outgoing
  trajectory must match the incoming one within its uncertainty. This is the term
  that resolves ambiguity when the combinatorics alone do not.
- **Non-collision** — two balls cannot be within one ball diameter (74 mm).

Solved as a global minimum-cost assignment over candidate links (Hungarian /
min-cost flow on the gap graph), not greedily, because a greedy pass commits to
early mistakes that later evidence would have corrected. Bridged gaps are recorded
as inferred, with their uncertainty inflated accordingly.

Ball count `b` is estimated from the maximum number of simultaneously-active ball
trajectories and cross-checked against the average theorem (`b = mean(h)`) once a
siteswap exists. Disagreement is reported, not reconciled.

---

## 8. Beat grid and siteswap extraction

Vanilla async siteswap only (v1).

1. **Beat grid.** Throw instants across all hands are fitted to a uniform grid by
   robust regression, giving `τ_b` and a phase. Tempo drifts, so the grid is fitted
   piecewise over a sliding window rather than globally.
2. **Ordinal throw values.** `h` for a throw is the **number of beats until that
   same ball is next thrown** — an integer count on the grid, not a height
   measurement rounded to an integer. Heights vary continuously with technique;
   the ordinal definition is exact whenever the grid and the linking are right,
   which is why linking (§7) comes first.
3. **Validation.** A candidate pattern must satisfy the permutation test (all
   landing slots distinct), the average theorem (`b = mean(h)` matches the detected
   ball count), and Shannon's theorem as a sanity cross-check on the timing.
4. **Segmentation.** The throw sequence is scanned for the shortest repeating
   cycle; a change of cycle starts a new pattern section.
5. **Failure is a result.** A section that satisfies no valid vanilla siteswap is
   labelled **"not a valid vanilla siteswap"** and reported with its raw throw
   sequence. It is never snapped to the nearest legal pattern.

---

## 9. Metric definitions

The definitions are the product; they are fixed here so results are comparable
across versions.

| Metric | Definition |
|---|---|
| **Catches** | Count of catch events. Balls collected at the end of a run count. |
| **Run length** | Catches between run start and `end_reason`. |
| **Throw height** | `z_apex` = apex height **above that throw's release point**. Measured from the fitted parabola, cross-checked against `v_z²/2g`. |
| **Air time** | `t_air` = catch time − throw time, per throw. |
| **Dwell time** | `t_d` = throw time − catch time for the *same ball* in the same hand. Ball-side dwell, matching Airtime's `t_d`. |
| **Dwell ratio** | `r_d = t_d / (n_h · τ_b)`. |
| **Beat rate** | `1 / τ_b`, from the local beat grid; reported as throws per second and per minute. |
| **Ball count** | Max simultaneously-active balls, cross-checked with `mean(h)`. |

**Energy.** Mechanical work done on the balls only — explicitly *not* metabolic
cost, and not the work done by the body, which is unmeasurable without body
markers. Two figures, because they have different noise properties:

- **Endpoint work** (robust): over a carry, `W = ΔE_mech = (½m v² + m g z)` at
  release minus the same at catch. Needs only two well-determined states.
- **Integrated work** (detailed, noise-sensitive): `W = ∫ F·v dt` over the carry
  with `F = m(a + g ẑ)` from the smoothed ball acceleration, split into
  `W⁺ = ∫ max(F·v, 0) dt` and `W⁻ = ∫ min(F·v, 0) dt`. This is computable because
  the ball is tracked *while held*, and mirrors Airtime §4.5 so the two projects'
  energy numbers are directly comparable.

Reported in **J and J/kg**, per throw, per run, and per session, with session total
and average power `P̄`. Where the two estimates disagree beyond tolerance, both are
shown — that disagreement is a data-quality signal.

**Accuracy and repeatability** — raw distributions, no composite score (a single
number hides *which* thing is inconsistent). Per hand and per throw value:

- apex position scatter (σx, σy, σz) and apex height spread
- release-point and catch-point scatter
- dwell-time spread
- beat-period jitter (residual about the fitted grid)
- left/right symmetry: the difference between per-hand distributions

Every distribution is reported as median, IQR, and σ, with `n`. Composite scores
can be built on top of these later; the raw layer stays.

---

## 10. Session JSON

One versioned, documented JSON per recording, written from day one so future
longitudinal features have retroactive data. It carries a `schema_version`, the
source file and its hash, `f_s`, the derived frame transform, ball/event/run
tables, all metrics, and the analysis parameters and code version that produced it.
Trajectory sample data is referenced, not inlined, and served separately to the
viewer — a 10-minute recording is tens of megabytes of samples.

A written schema lives beside the writer, and a round-trip test pins it. Schema
changes bump `schema_version` and get a note in `BUILD_LOG.md`.

---

## 11. Viewer

A **local server plus a browser SPA**: `python -m juggling_analyser serve <file>`
analyses the recording, serves the session JSON and trajectory data on
`127.0.0.1`, and opens the SPA. Python re-analyses when parameters change, which
is what makes it an instrument rather than a report. A self-contained shareable
export comes later.

Stack: React + three.js (react-three-fiber) + zustand + TypeScript strict — the
same stack as Airtime, deliberately. **This build has its own viewer, but mirrors
Airtime's module boundaries** (`core` / `state` / `render3d` / `ui`, one-way
dependencies) so that converging on a shared visualisation package later is a
mechanical extraction rather than a rewrite. That convergence is what makes the v2
measured-vs-ideal overlay cheap.

The scene: balls at true 74 mm diameter, configurable trail length, orbit camera
with front/side/top/juggler presets, ground reference grid, dark by default. The
timeline is scrubbable and shared with every panel, so an anomaly in a metric chart
can be clicked straight to the moment in 3D that produced it. Events are marked on
the timeline; drops and uncertain sections are visually distinct from clean ones.

Aesthetic: an instrument first — legible, dense, honest about uncertainty —
configurable toward the expressive (themes, trail styling, glow, ball colouring).

---

## 12. Synthetic ground truth and validation

Three independent validation layers, because no single one is sufficient.

**1. The reader against the TSV oracle.** `data/5_ball_juggling_cut_balls_only.tsv`
is a one-off QTM export. The reader must reproduce it frame for frame for all 19
trajectories. This pins ingestion completely and never needs repeating.

**2. Synthetic recordings with known truth.** Airtime is the closed-form simulator
and the shared source of physics truth, so it — not a reimplementation here —
generates the clean data: a small export writes exact labelled trajectories
sampled at `f_s` as JSON. `core/synth.py` then degrades them into realistic mocap:
Gaussian position noise, tracking gaps at apexes and crossings, trajectory
fragmentation and identity swaps, spurious short-lived reflections, and occlusion
dropouts. Because the pattern, every event, and every ball identity are known
exactly, this gives unlimited labelled data for linking, event detection, and
siteswap extraction — including 7-ball cases and failure modes that are hard to
record deliberately. Fixtures are committed, so CI needs no Node.

**3. Real recordings with hand-labelled truth.** The acceptance test.
`3_ball_juggling_cut` is the reference, and its structure is deliberately awkward:

> **22 catches → a ball is dropped → the 2 balls still airborne are caught
> (24 catches in run 1) → 31 catches in run 2.**

The pipeline must reproduce both counts, place the drop between catches 22 and 23,
and still attribute the two post-drop collection catches to run 1. This recording
is also visibly **noisier** than the 5-ball clips, which makes it the right
acceptance piece: it exercises drop detection, the collection-catch rule, and
robustness to poor tracking in a single file.

Property tests hold across all three: energy conservation within a flight; every
catch precedes its throw; no two balls within one diameter; ball count consistent
with `mean(h)`; the analysis is byte-identical across repeated runs.

---

## 13. Defaults

| Parameter | Default | Note |
|---|---|---|
| `g` | 9.80665 m/s² | fixed, not a knob |
| Ball mass `m` | 0.071 kg | configurable per session |
| Ball diameter `d` | 0.074 m | used for the non-collision constraint and rendering |
| `n_h` | 2 | 1 for Jugglebot |
| Ballistic tolerance `a_tol` | 1.5 m/s² | tuned against synthetic data |
| Min flight duration | 100 ms | shorter is noise, not a throw |
| Savitzky–Golay window | 21 samples @ 300 Hz | ~70 ms |
| Drop tolerance `z_drop_tol` | 0.30 m below `z_c` | |
| Max bridged gap | 250 ms | longer gaps mark the run uncertain |
| Trail length | 1.5 s | configurable in the viewer |

Every default lives in one module and is overridable per analysis; the value used
is recorded in the session JSON.

---

## 14. Tech stack and quality gates

Python 3.11+, numpy, scipy. `ruff` (lint + format), `mypy` (strict on `core/`),
`pytest` + `hypothesis` for property tests. Viewer: Vite + TypeScript strict +
React + three.js + zustand.

The gate is `python tools/gate.py` = ruff check + ruff format --check + mypy +
pytest. It must be green before every commit (CLAUDE.md rule 2). CI runs the same
gate on push and PR.

Coverage is a target where it is meaningful — `core/flight`, `core/link`,
`core/pattern`, and `core/metrics` carry the correctness risk and are held to
≥ 90% line coverage. Coverage of I/O and viewer glue is not a goal.
