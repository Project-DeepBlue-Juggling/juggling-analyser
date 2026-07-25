# OWNER_ACTIONS.md

Everything from the autonomous build run that only you can do, ordered by value.
Each item says what to do, why, which phase it unblocks, and roughly how long.

`BUILD_LOG.md` has the full account of what happened. Reading that file and this one
should tell you everything without opening a line of code.

**Where the build got to:** P0 and P1 fully green, P2 and P4 partial with their
shortfalls measured and recorded, P3 complete (exporter, fixtures and degradation
model, 81 tests), P5 not built but measured, P6–P9 not started. Six commits, all
pushed, CI green, gate green at every commit. Honest summary at the end.

---

## 1. Settle the gravity discrepancy — 10 minutes, highest value

**The single most important thing on this list, and the cheapest.**

Measured vertical acceleration in all three sample recordings is **9.55 m/s², about
2.6% below 9.80665** — and it is *not* an analysis bug. Two independently recorded
clips agree to within 0.06 percentage points, the best-determined flights show the
largest deficit, per-flight formal σ is 0.004–0.009 m/s², and a free fit leaves a
1.2 mm residual where fixing `g` leaves 4.2 mm. Ruled out with numbers
(BUILD_LOG.md, Phase 2): volume-dependent distortion, air drag, a tilted Z axis, and
the reader. Two causes remain and ball trajectories alone cannot separate them:

1. the QTM calibration's **length scale** is about 2.9% small, or
2. the capture **sample rate** is really ~295.7 Hz, not the 300 Hz the file reports
   (`g_fit = g·(f_true/f_s)²`).

**What to do.** Put a tape measure across two of the static `base_N` markers that sit
in the volume in every recording, and compare with what this analysis measures:

| Marker pair | Measured distance | If the *scale* hypothesis is right, the tape should read |
|---|---|---|
| `base_2` ↔ `base_4` | **0.7107 m** | ≈ 0.7317 m (+21 mm) |
| `base_1` ↔ `base_2` | **0.3149 m** | ≈ 0.3242 m (+9 mm) |
| `base_1` ↔ `base_4` | **0.5509 m** | ≈ 0.5672 m (+16 mm) |
| `base_3` ↔ `base_4` | **0.1165 m** | ≈ 0.1200 m (+3.5 mm) |

Use `base_2`↔`base_4` — it is the longest, so a tape resolves 2.9% most clearly
(21 mm out of 711 mm). The measured values reproduce to **0.1 mm** across the two
recordings, so the analysis side of the comparison is solid; any disagreement is real.

- **Tape agrees with the measured value** → **timing**. Check the system's actual
  frame rate and whether anything resampled the file.
- **Tape reads ~2.9% longer** → **calibration scale**. Re-run the wand/L-frame
  calibration and check the wand length entered in QTM.

**Why it matters.** Every absolute length and every energy figure inherits this.
Throw heights read 2.9% low under hypothesis 1; all times read 1.45% short under
hypothesis 2; Phase 9's joules are wrong under either until it is resolved. Siteswap
extraction is ordinal and unaffected.

There is an independent corroboration already in the data: the 5-ball clip has
exactly one frame where two balls come within one ball diameter — 73.8 mm against
74 mm, i.e. 0.2 mm inside. Correcting for the measured 2.87% deficit puts that pair at
76.0 mm, no violation. Two unrelated measurements pointing the same way.

**Unblocks**: Phase 2's remaining criterion, and the credibility of Phase 9.

**Also useful, same trip (5 min):** if the `base_N` markers sit on a manufactured
object whose dimensions you know from CAD, those numbers beat a tape measure — send
them and the comparison becomes exact.

---

## 2. Record a static-marker clip — 5 minutes of capture

Place 3–5 markers on the floor and on a stand at juggling height and record
**30 seconds with nothing moving**.

**Why.** The pipeline uses QTM's per-sample *residual* as the position uncertainty σ,
and Phase 2 measured that this **understates the true error by about 3×**: clean
flights fit a parabola to ~1.2 mm while the reported σ is ~0.35 mm, and χ²/dof runs at
15–35 where a correct σ gives ~1. A motionless marker's scatter *is* σ, directly.

This is not cosmetic. It already caused a concrete failure: a **correct** link across
the 5-ball clip's 417 ms gap scored χ² = 11.4 against a 3σ gate of 9, purely because
σ was understated and χ² scales as σ⁻². The linker now multiplies by a hard-coded 3.0
(`SIGMA_UNDERESTIMATE_FACTOR`) to compensate. One recording replaces that guess with a
measurement.

**Unblocks**: honest error bars everywhere; the linker's χ² gate; Phase 4's remaining
shortfall is partly this.

---

## 3. Recordings to make — one session, ~40 minutes

Keep **a clear 3-second pause with all balls held** between runs: run segmentation
becomes unambiguous and it costs nothing. Otherwise juggle normally — the point is
real data, not clean data.

Please **do not delete, discard or re-label trajectories** in QTM afterwards, and
export each as its own `.qtm`. (`5_ball_juggling_cut_balls_only.qtm` has five
trajectories marked `Trajectory Type 2` with their labels stripped, which is how the
reader learned to gate on that field — but it also means that file is not
representative.)

In rough priority order:

| # | What to juggle | Why |
|---|---|---|
| 1 | **`3` cascade, 60 s clean, no drops** | A long clean run for beat-period drift and repeatability over time, and the scale test for the linker. **The single most useful recording on this list.** |
| 2 | **`441`**, then **`531`**, then **`552`**, ~30 s each | Phase 6 needs patterns with *different* throw values in one run. `531` is the key one: it contains a `1`, a hand-across pass rather than a throw, where a naive beat grid breaks. |
| 3 | **`423`**, ~30 s | Contains a `2` — a hold with no flight. The pipeline must not report a flight where there is none. |
| 4 | **`4` fountain**, ~30 s | Each ball stays in one hand, so hand assignment is tested differently from a cascade. |
| 5 | **`5` cascade, as long as you can** | 5-ball is in the corpus but only 16.6 s of it, and it is the clip everything is currently pinned to. |
| 6 | **3 deliberate drops**, one per clip | Please **drop deliberately and let the ball hit the floor**, then note roughly how many catches preceded it. One of these should be a drop where you *keep juggling* the remaining balls afterwards. |
| 7 | **3 deliberate clean stops** — juggle, then collect every ball and hold | Phase 5 must distinguish "stopped" from "dropped"; `data/` contains no example of a clean stop. |
| 8 | **7-ball attempt**, even a 3-second flash | Phase 4's 7-ball target is only testable on synthetic data today. A flash is enough. |
| 9 | **One deliberately awkward clip**: a `3` cascade where you scratch your head mid-pattern, or walk two steps | The frame derivation assumes the hands stay put. This is the test of how badly that fails. |

If time is short, **1, 2 and 6 unblock the most work.**

---

## 4. Exports to share — 10 minutes

- **A QTM TSV 3D export of `3_ball_juggling_cut.qtm`.** The reader is pinned against a
  TSV of the *clean* 5-ball clip, where it reproduces all 19 trajectories to
  5.0e-07 m. A TSV of the **noisy** clip gives a second oracle at the other end of the
  quality range — the one that would catch a gap-filling or `Parts`-table edge case
  the clean file never exercises. Export **3D data, no filtering, no gap-fill**, and
  say if QTM applied any.
- The **calibration report** for the session (per-camera residual, wand length,
  calibration date). It would very likely settle item 1 outright.

---

## 5. Review the Airtime branch — 15 minutes, needs a non-Windows machine

`feat/truth-export` is pushed to Airtime (HEAD `ac4ae31`; `main` untouched at
`efe72c1`). It adds one module, one test file, one Node runner, and one line to
`package.json`. **No dependency was added.** The exporter is not re-exported from
`src/export/index.ts` and the built `dist/` bundle is byte-identical to before.

**But `npm run gate` could not be verified on this machine, and you should not merge
until it is.** Airtime has five pairs of source files differing only in case
(`Hands.tsx`/`hands.ts`, `Tracers.tsx`/`tracers.ts`, `Charts.tsx`/`charts.ts`,
`TimelineBar.tsx`/`timelineBar.ts`, `EnergyPanel.tsx`/`energyPanel.ts`). TypeScript
cannot resolve those on Windows — `typescript.js` hard-codes
`isFileSystemCaseSensitive() === false` for win32, so `tsc` fails even on an NTFS
per-directory case-sensitive mirror. Evidence that it is pre-existing and not the
change: a clean `main` checkout on Windows gives the **same 20 typecheck errors** and
32 test failures; on a case-sensitive mirror the branch gives **816 tests passing,
eslint clean, `vite build` clean**, and not one error in an added file.

**What to do**: run `npm run gate` on Linux (or let Airtime's CI do it) and confirm
green, then review and merge at your discretion. Nothing was renamed to force it
green — that would be the refactor the protocol forbade. Separately, **those five
case-colliding filename pairs are worth renaming**: they make Airtime unbuildable on
Windows and macOS, which is a real portability bug independent of this branch.

---

## 6. Decisions I need from you

1. **Throw instant definition (affects `t_air` and `t_d`).** DESIGN.md §6 defines the
   throw as the *first sample* of a flight. Measured on synthetic data, boundary
   refinement admits two or three contaminated samples before the true release, so
   `t_air` carries a systematic bias of about **+10 ms** (~2% of a 0.5 s flight). I can
   remove it by solving for the sub-sample instant at which the path departs the fitted
   parabola — but that changes a definition in the frozen design, so I did not.
   **Recommendation: change it.** The sub-sample crossing is the physically real
   release instant, and the ordinal siteswap logic does not care either way.

2. **Should the analysis use measured `g` or nominal `g`?** It currently measures the
   recording's actual vertical acceleration and fits with that, while always reporting
   the discrepancy against 9.80665 and never absorbing it. That makes fit residuals a
   meaningful confidence signal instead of a proxy for the 2.6% instrument offset. This
   is a deliberate deviation from DESIGN.md §6's single-pass description
   (`calibrate=False` restores it). Fine, or would you rather it always used 9.80665
   and lived with residuals five times the measurement noise?

3. **Is `Trajectory Type 2` ever exported?** The reader excludes it, because doing so
   reproduces your TSV export exactly (19 markers, not 24). In the other two recordings
   the *same five physical markers* are type 1 with labels `base_0`..`base_4`, so type 2
   looks like a per-project state — "removed from this file" — not a property of a
   marker. If you remember what you did to that file in QTM, that confirms it. If a
   future recording has type-2 trajectories you *do* want, the gate is wrong.

4. **What happened at t = 2.86 s and t = 10.96 s in `3_ball_juggling_cut`?** See item 7.

---

## 7. Acceptance disagreements

### 7a. Phase 2 — fitted `g` within 2% of 9.80665: **FAILS at −2.59% and −2.65%**

Not an off-by-one and not tunable. Item 1 above; full evidence in BUILD_LOG.md Phase 2,
including everything that was ruled out.

### 7b. Phase 5 — the headline catch count: **total agrees exactly, the split does not**

`core/events.py` was not built, but the number can be measured from flight
segmentation alone, because a catch is the end of a flight and needs no ball identity:

    total catch-like events         55        your count: 22 + 2 + 31 = 55
    split at the 3.6 s dead gap     28 / 27   your count: 24 / 31

**The total is exactly right. The split is not**, and 28/27 is not a rounding of
24/31. Everything you need to adjudicate it, with timestamps:

- **Dead time 11.60 s → 15.20 s** (3.60 s — the largest inter-catch interval; the
  median is 0.482 s). Taken as the run boundary.
- **Drop candidate at t = 10.96 s**: a flight ends at **z = −0.172 m**, 1.12 m below
  the catch plane `z_c = 0.947 m`. A ball on the floor. **26 catches precede it** and
  **1 follows** before the dead time — against your 22 and 2.
- **A second low flight end at t = 2.86 s, z = 0.492 m** (0.46 m below `z_c`). You
  report only one drop in this recording. Is this a very low catch, a bounce, or a
  second drop? **This is the specific thing to look at**, because if it is a drop then
  the run structure is not what either count assumes.
- **7 of 62 detected flights have a truncated end** — tracking died mid-flight, so
  their catch is real but unobserved and is not in the 55. The 3-ball clip tracks only
  69% of its ball-frames. If four of those fall in run 2, that alone reconciles 27
  with your 31, and it is my leading explanation.
- The floor impact at 10.96 s is currently counted as a flight end. A ball hitting the
  floor is not a catch, so the real figure is 54 catches + 1 impact — and DESIGN.md
  §6's below-`z_c` rule is exactly what separates them. That rule is Phase 5's work.

**An off-by-one in a hand count of 55 is entirely possible; so is a bug. I am not
assuming which**, and the two timestamps above are the fastest way for you to decide.

### 7c. Phase 4 — 100% linking on the synthetic 5-ball case: **FAILS at 0.615**

The real 5-ball clip passes its criterion exactly (5 lanes, each spanning frames
1–4967, 383 frames of gap against a 400 limit, 98.46% coverage). Synthetic 3-ball is
100% and 7-ball is 95.2%, both passing. The synthetic 5-ball case is 0.615 with 7
lanes. Recorded as a **strict xfail**, not a relaxed assertion, so it fails the suite
if it is ever silently fixed. Likely cause and the contained fix are in BUILD_LOG.md
Phase 4: a scalar velocity σ extrapolated over 550 ms is too crude, and the flight
fit's own parameter covariance is what the χ² needs.

---

## 8. Blocked work — what I could not do, and what would unblock it

| Not done | Why | What unblocks it |
|---|---|---|
| Running the linker against `core/synth.py`'s output | `synth.py` landed at the very end, after P4 was already measured. The linker has therefore never been shown an identity swap or a spurious reflection, so its scores are an **upper bound**. | Nothing external — this is the first thing to do next, and it is now cheap since the degradation model and its 81 tests are in. |
| Independently checking `synth.py`'s calibration presets | The tests that assert the calibration were written by the same agent that chose the presets. | Nothing external; just needs a second pair of eyes on the achieved-vs-target table in BUILD_LOG.md Phase 3. |
| Phase 5 `core/events.py` | Ran out of budget. Its inputs all exist: flights with confidence, a derived frame, linked balls, and `z_c` = 0.947 m already computable. | Nothing external, plus your answer on item 7b. |
| Phases 6–9 (siteswap, session JSON, viewer, metrics) | Not started. | Phase 6 in particular needs the **`441`/`531`/`552`/`423` recordings** (item 3.2) — the corpus contains only cascades, so there is no real data with mixed throw values at all. |
| Phase 4's 7-ball criterion on real data | No 7-ball recording exists. | Item 3.8. |
| A real clean-stop case | No recording contains one. | Item 3.7. |
| Verifying Airtime's gate | Windows cannot typecheck Airtime at all (item 5). | A Linux machine or Airtime's CI. |
| Absolute energy figures | The 2.6% scale/timing ambiguity. | Item 1. |
| Calibrated uncertainties | QTM's residual understates σ by ~3×. | Item 2. |

Deliberately **not** done, per the protocol: no GitHub release, no tag, no repository
settings changed, nothing merged into Airtime's `main`, nothing in `data/` deleted or
modified. The stale `.rrd` files in `data/` were left on disk rather than deleted —
PLAN.md P1 asked for their deletion but the protocol forbids touching `data/`, so they
are simply `.gitignore`d and never enter the repository.

---

## 9. Honest summary

Five of ten phases were touched and four commits landed, every one with the gate green
and CI green. **What is solid:** ingestion is finished and pinned to a level I did not
expect — the reader reproduces a QTM TSV export of all 19 trajectories to 5.0e-07 m,
which is the export's own text-rounding floor, so it is bit-exact. Along the way the
`.qtm` format got substantially better understood: `Data Items` is a stream of typed
objects, and the file carries a **schema that names every field**, which turned the
whole field map from inference into fact. Flight segmentation is sound and measured
against known truth — throws and catches within 3 samples, `z_apex` within 3 mm, and
every flight still found at four times the corpus noise. The juggling frame comes out
within 0.6° of nominal and round-trips to 5.6e-16. The linker recovers exactly 5 balls
from the 5-ball clip, tiling all 4967 frames with 383 frames of bridged gap.

**What is shaky:** the linker fails its own synthetic 5-ball target at 0.615, and it
has never been shown an identity swap or a spurious reflection because the degradation
model did not get built — so treat its scores as an upper bound. The 3-ball clip does
not link into 3 balls and honestly cannot, at 69% frame coverage. Phase 5 was measured
but not built, and while its headline total is exactly your 55, the 24/31 split comes
out 28/27 and I do not know yet whether that is my bug or your off-by-one.

**The one thing that changes what this project can claim** is the gravity finding.
Measured `g` is 2.6% low, consistently, across two independent recordings, and I could
not make it be an analysis error however hard I tried to break it. Either the volume's
length scale or its frame rate is wrong by a couple of percent. Until that is settled
every distance and every joule this tool reports carries an unknown 3% systematic — and
settling it takes one tape measure and ten minutes. Do item 1 first.
