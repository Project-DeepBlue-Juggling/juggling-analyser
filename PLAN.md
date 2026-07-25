# PLAN.md — Phased Implementation Plan

Eleven phases, each sized for one focused build session, each ending with
`python tools/gate.py` green and a commit. Read `DESIGN.md` and `NOTATION.md`
first; § references point into DESIGN.md.

**Ordering rationale.** Ingestion is pinned first against an exact oracle (P1), so
no later bug can be blamed on the reader. Flight segmentation (P2) comes next
because it needs neither ball identity nor a coordinate frame, and it *produces*
the frame. Synthetic ground truth (P3) lands before linking (P4) so that the
hardest algorithm in the project is developed against labelled data rather than
guesswork. The 3D replay (P8) is deliberately early — before the metrics
dashboard — because seeing the balls move is how every stage before it gets
validated by eye. Metrics (P9) come last, on top of a pipeline already known good.

---

## Phase 0 — Toolchain, gate, docs

**Goal**: the quality machinery, before any new algorithm.

- `ruff` (lint + format), `mypy` (strict on `core/`), `pytest`, `hypothesis`,
  `pytest-cov` wired up; config in `pyproject.toml`.
- `tools/gate.py` = ruff check + ruff format --check + mypy + pytest. Define it
  here and keep it stable forever. No console-script entry points (CLAUDE.md §5).
- Core-purity enforcement: lint rules banning `core/` imports of `io`, `viewer`,
  `cli`, and banning `time.time`, `datetime.now`, `perf_counter`, `random.*`,
  and global-RNG numpy calls — plus a test that walks the AST of `core/**` and
  fails on a violation, so the rule holds even if lint config drifts.
- `.github/workflows/ci.yml` running the gate on push and PR.
- Commit the doc set (`NOTATION.md`, `DESIGN.md`, `PLAN.md`, `CLAUDE.md`),
  start `BUILD_LOG.md`, rename the branch to `main`, first push.
- Correct `docs/qtm-format.md` (the "absolute-timing gap" section is obsolete —
  document the piece table instead) and the README roadmap.

**Accept**: gate green on the existing code; CI green on GitHub; repo pushed.

## Phase 1 — Reader v2: absolute timing, real trajectories

**Goal**: ingestion pinned exactly against the TSV oracle (§4).

- Parse `Measurement NBC/Data Items` field 17 piece tables; match each to its data
  series by summed piece length; populate `Trajectory.frames`, `.pieces`,
  `.sample_type`.
- **Gate on having a piece table** — this removes the six phantom series in the
  5-ball sample (§4).
- Replace the scalar residual with `Uncertainty` (§3), isotropic for mocap.
- `io/tsv.py`: read a QTM TSV export (validation only).
- Delete `Fragment.start_frame` and every trace of the "unknown start frame"
  model, including the stale `.rrd` files and the Rerun `viz` extra.

**Accept**: gate green; a test asserting the reader reproduces
`data/5_ball_juggling_cut_balls_only.tsv` frame for frame for all **19**
trajectories within 1e-6 m; `info` reports 19, not 25; exactly 5 trajectories
active at frame 1.

## Phase 2 — Flight segmentation and the juggling frame

**Goal**: the most reliable signal in the data, and the coordinate frame it yields
(§5, §6).

- `core/flight.py`: Savitzky–Golay derivatives; ballistic test weighted by
  uncertainty; flight/carry segmentation; throw, catch and sub-sample apex from a
  fitted parabola; per-flight fit residual as confidence.
- `core/frame.py`: origin from the catch/throw cloud mean; hand axis by PCA; sign
  from the nominal mapping; transform into the juggling frame, recorded and
  invertible.
- `core/clean.py` rewritten: reject spurious trajectories on lifetime and physics
  now that phantom series are gone.

**Accept**: gate green; on the 5-ball clip every detected flight has a parabola
residual below tolerance and a fitted `g` within 2% of 9.80665 (the strongest
available self-check); the derived hand axis is within 15° of the nominal frame;
round-tripping through the frame transform is identity to 1e-12.

## Phase 3 — Synthetic ground truth

**Goal**: unlimited labelled data before the hard algorithms (§12).

- Small addition to Airtime: export exact labelled trajectories sampled at `f_s`
  as JSON (pattern, per-ball positions, every event, ball identities).
- `core/synth.py`: degrade clean truth into realistic mocap — Gaussian noise,
  gaps at apexes and crossings, trajectory fragmentation and identity swaps,
  spurious short reflections, occlusion dropouts. Seeded `np.random.Generator`
  only (CLAUDE.md §1), so every synthetic case is reproducible.
- Commit fixtures for 3, 5 and 7 balls and for `441`, `531`, `552`, plus a
  deliberate-drop case. CI needs no Node.

**Accept**: gate green; a synthetic recording round-trips through the P1 reader
model; the degradation model reproduces the *measured* fragment-length and gap
statistics of both real clips within a factor of two — calibrate the noise level
against `3_ball_juggling_cut` (the noisy one) and the fragmentation against
`5_ball_juggling_cut_balls_only`, so synthetic data spans the real quality range
rather than only the clean end of it.

## Phase 4 — Identity linking

**Goal**: trajectories → balls (§7). The hardest component; now built against
labelled data.

- Candidate link generation across gaps; cost from ballistic continuity weighted
  by uncertainty; global min-cost assignment (not greedy).
- Constraints: non-overlap, coverage, non-collision at one ball diameter.
- Ball-count estimation; bridged gaps recorded as inferred with inflated
  uncertainty; unbridgeable gaps marked uncertain.

**Accept**: gate green; **100% correct linking on synthetic 3- and 5-ball cases,
≥ 95% on 7-ball**; on the real 5-ball clip, exactly 5 lanes tiling the recording
with total gap ≤ 400 frames and no non-collision violation.

## Phase 5 — Events, hands, runs, drops

**Goal**: the event timeline (§6).

- Consolidate per-ball throws and catches; hand assignment by k-means along the
  hand axis; catch plane `z_c`.
- Drop vs occlusion by the below/above-`z_c` rule; run segmentation with
  `end_reason` (drop / stop / recording-end); balls collected at the end count as
  catches.

**Accept**: gate green; on `3_ball_juggling_cut` the pipeline reports
**22 catches → drop → 2 collection catches (24 in run 1) → 31 catches in run 2**,
with the drop placed between catches 22 and 23 and the two post-drop catches
attributed to run 1. This is the project's headline acceptance test, on the
noisiest recording in `data/`.

## Phase 6 — Beat grid and siteswap extraction

**Goal**: motion → notation (§8).

- Piecewise robust beat-grid fit (`τ_b`, phase) over a sliding window.
- Ordinal throw values; permutation test; average theorem and Shannon cross-check.
- Shortest-repeating-cycle detection; pattern sections; explicit
  **"not a valid vanilla siteswap"** labelling with the raw throw sequence.

**Accept**: gate green; every synthetic pattern recovered exactly (`441`, `531`,
`552`, `3`, `5`, `7`); the 3-ball clip recovers `3`; a deliberately invalid
synthetic sequence is labelled invalid rather than snapped to a legal pattern.

## Phase 7 — Session JSON and the `analyse` command

**Goal**: a persisted, versioned result (§10).

- Schema with `schema_version`, source hash, frame transform, balls, events, runs,
  parameters, code version. Sample data referenced, not inlined.
- `python -m juggling_analyser analyse <file>` writes it; round-trip test pins the
  schema; written schema doc beside the writer.

**Accept**: gate green; round-trip is lossless; two runs over the same input
produce byte-identical JSON (the determinism guarantee, made checkable).

## Phase 8 — 3D replay

**Goal**: see it move (§11). Early, because it validates everything before it.

- `viewer/server.py`: local HTTP server, session JSON + trajectory streams.
- SPA: Vite + TypeScript strict + React + three.js + zustand, mirroring Airtime's
  `core` / `state` / `render3d` / `ui` boundaries.
- Balls at true 74 mm, configurable trails, orbit camera with presets, ground
  grid, dark theme; scrubbable shared timeline with event markers; drops and
  uncertain sections visually distinct.
- `python -m juggling_analyser serve <file>` opens it.

**Accept**: gate green (Python) and the SPA's own typecheck/lint/test/build;
operator check: the 3-ball clip looks like a 3-ball cascade, the drop is visible
where the analysis says it is, trails match flight paths, scrubbing is smooth.

## Phase 9 — Metrics

**Goal**: the numbers (§9), on a pipeline already known good.

- Per-throw: `z_apex`, `t_air`, `t_d`, `r_d`, beat period.
- Energy: endpoint and integrated `W⁺`/`W⁻`/`W_net`, J and J/kg, per run and
  session, with `P̄`; disagreement between the two estimates surfaced.
- Repeatability: raw distributions (median, IQR, σ, n) per hand and throw value;
  left/right symmetry. No composite score.
- Session totals: total catches, longest run, most catches per pattern, drops,
  total airtime.
- Metric panels wired into the viewer, timeline-synchronised.

**Accept**: gate green; energy cross-check — endpoint and integrated work agree
within 5% on synthetic data; `mean(h)` matches the detected ball count on every
synthetic case; dwell ratio on the 3-ball clip lands in the biomechanically
plausible 0.6–0.8 band (a sanity check, not a pass/fail on the juggler).

## Phase 10 — Polish, fixtures, release

- README with real numbers and screenshots; `docs/` complete and honest.
- Curated `.qtm` fixtures committed; larger corpus attached to a GitHub release.
- Public API surface reviewed and documented; `0.1.0` tagged.

**Accept**: gate green; a fresh clone gates green without the corpus; the README's
worked example reproduces on a clean machine.

---

## Cross-phase rules

- Never weaken or delete a test to make a phase pass — that is a stop-and-surface
  event.
- Any phase may refactor earlier code freely **as long as the gate stays green**
  and core purity holds.
- Discovered-but-deferred work goes in `BUILD_LOG.md` under the phase entry, not
  in `TODO` comments.
- Update the README status line as phases land — one line, kept honest.
- Where measurement contradicts an idealised identity, report it. Never snap
  measured data to the ideal to make a test pass.
