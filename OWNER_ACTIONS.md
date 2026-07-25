# OWNER_ACTIONS.md

Everything from the autonomous build run that only you can do, ordered by value.
Each item says what to do, why, which phase it unblocks, and roughly how long.

`BUILD_LOG.md` has the full account of what happened; this is the to-do list.

> **Status of this file**: written during the run and updated at the end. The
> closing summary is at the bottom.

---

## 1. Settle the gravity discrepancy — 10 minutes, highest value

**The single most important thing on this list.**

Measured vertical acceleration in all three sample recordings is **9.55 m/s²,
about 2.6% below 9.80665** — and it is *not* an analysis bug. Two independently
recorded clips agree to within 0.06 percentage points, the best-determined flights
show the largest deficit, and volume distortion, air drag, a tilted Z axis and the
reader have all been ruled out with numbers (BUILD_LOG.md, Phase 2). Two causes
remain and ball trajectories alone cannot tell them apart:

1. the QTM calibration's **length scale** is about 2.9% small, or
2. the capture **sample rate** is really ~295.7 Hz, not the 300 Hz the file reports
   (`g_fit = g·(f_true/f_s)²`).

**What to do.** Put a tape measure across two of the static `base_N` markers that
are in the volume in every recording, and compare with what this analysis measures:

| Marker pair | Measured distance | If the *scale* hypothesis is right, the tape should read |
|---|---|---|
| `base_1` ↔ `base_2` | **0.3149 m** | ≈ 0.3242 m (+9.0 mm) |
| `base_3` ↔ `base_4` | **0.1165 m** | ≈ 0.1200 m (+3.3 mm) |
| `base_2` ↔ `base_4` | **0.7107 m** | ≈ 0.7317 m (+20.4 mm) |
| `base_1` ↔ `base_4` | **0.5509 m** | ≈ 0.5672 m (+16.3 mm) |

Use `base_2`↔`base_4` if you can — it is the longest, so a tape measure resolves
the 2.9% most clearly (20 mm out of 711 mm). The measured values reproduce to
**0.1 mm** across the two recordings, so the analysis side of the comparison is
solid; any disagreement is real.

- **Tape agrees with the measured value** → it is a **timing** problem. Check the
  camera system's actual frame rate, and whether anything resampled the file.
- **Tape reads ~2.9% longer** → it is a **calibration scale** problem. Re-run the
  wand/L-frame calibration and check the wand length entered in QTM.

**Why it matters.** Every absolute length and every energy figure inherits this.
Throw heights read 2.9% low under hypothesis 1; all times read 1.45% short under
hypothesis 2; Phase 9's joules are wrong under either until it is resolved.
Siteswap extraction is ordinal and unaffected.

**Unblocks**: Phase 2's remaining acceptance criterion, and the credibility of
Phase 9's energy numbers.

**Also useful, same trip (5 min):** if the `base_N` markers sit on a manufactured
object whose dimensions you know from CAD, those numbers are even better than a
tape measure — send them and the comparison becomes exact.

---

## 2. Record a static-marker clip — 5 minutes of capture

Place 3–5 markers on the floor and on a stand at juggling height, and record
**30 seconds with nothing moving**.

**Why.** The pipeline currently uses QTM's per-sample *residual* as the position
uncertainty σ, and the Phase 2 measurements show that **understates the true error
by about 3×**: clean flights fit a parabola to ~1.2 mm while the reported σ is
~0.35 mm, and χ²/dof runs at 15–35 where a correct σ would give ~1. A motionless
marker's scatter *is* σ, directly, at zero modelling cost. That single number
recalibrates every uncertainty-weighted result and every confidence interval in
the project.

**Unblocks**: honest error bars everywhere; `core/params.py`'s
`RESIDUAL_SIGMA_FLOOR` stops being a guess.

---

## 3. Recordings to make — one session, ~40 minutes

Keep **a clear 3-second pause with all balls held** between runs: it makes run
segmentation unambiguous and costs nothing. Otherwise juggle normally — the point
is real data, not clean data.

Please **do not delete or re-label trajectories** in QTM afterwards, and export
each as its own `.qtm`. (The `..._balls_only.qtm` file in `data/` has five
trajectories marked `Trajectory Type 2` and their labels stripped, which is how the
reader learned to gate on that field — but it also means that file is not
representative.)

In rough priority order:

| # | What to juggle | Why |
|---|---|---|
| 1 | **`3` cascade, 60 s clean, no drops** | A long clean run to measure beat-period drift and repeatability over time; also the scale test for Phase 4's linker. |
| 2 | **`441`**, then **`531`**, then **`552`**, ~30 s each | Phase 6's siteswap extraction needs patterns with *different* throw values in one run. `531` is the key one: it contains a `1`, which is a hand-across pass rather than a throw, and is where a naive beat grid breaks. |
| 3 | **`423`**, ~30 s | Contains a `2` (a hold). The pipeline must not report a flight where there is none. |
| 4 | **`4` fountain**, ~30 s | Even-numbered pattern: each ball stays in one hand, so hand assignment is tested differently from a cascade. |
| 5 | **`5` cascade**, as long as you can | 5-ball is already in the corpus but only 16 s of it. |
| 6 | **3 deliberate drops**, one per clip | Phase 5's drop detection. Please **drop deliberately and let the ball hit the floor**, then note roughly how many catches preceded it. One of these should be a drop where you *keep juggling* the remaining balls afterwards. |
| 7 | **3 deliberate clean stops** — juggle, then collect every ball and hold | Phase 5 must distinguish "stopped" from "dropped"; there is currently no example of a clean stop in `data/`. |
| 8 | **7-ball attempt**, even a 3-second flash | Phase 4's acceptance target is ≥ 95% linking on 7 balls, currently only testable on synthetic data. A flash is enough. |
| 9 | **One deliberately awkward clip**: a `3` cascade where you scratch your head with one hand mid-pattern, or walk two steps | The frame derivation assumes the hands stay put; this is the test of how badly that fails. |

If time is short, **items 1, 2 and 6 are the ones that unblock the most work.**

---

## 4. Exports to share — 10 minutes

- **A QTM TSV 3D export of `3_ball_juggling_cut.qtm`.** The reader is currently
  pinned against a TSV export of the *clean* 5-ball clip, where it reproduces every
  one of 19 trajectories to 5e-07 m. A TSV of the **noisy** clip would give a second
  oracle at the other end of the quality range — the one that would catch a
  gap-filling or `Parts`-table edge case the clean file does not exercise.
  Export with **3D data, no filtering, no gap-fill**, and say if QTM applied any.
- If you still have the QTM project, the **calibration report** for the session
  (residual per camera, wand length, calibration date). It would very likely settle
  item 1 outright.

---

## 5. Decisions I need from you

1. **Throw instant definition (Phase 2, affects `t_air` and `t_d`).**
   DESIGN.md §6 defines the throw as the *first sample* of a flight. Measured on
   synthetic data, boundary refinement admits two or three contaminated samples
   before the true release, so `t_air` carries a systematic bias of about **+10 ms**
   (~2% of a 0.5 s flight). I can remove it by solving for the sub-sample instant at
   which the path departs the fitted parabola — but that changes a definition in the
   frozen design, so I have not. **Change the definition, or keep the 10 ms bias and
   document it?** My recommendation: change it; the sub-sample crossing is the
   physically real release instant and the ordinal siteswap logic does not care
   either way.

2. **Is `Trajectory Type 2` ever exported?** The reader excludes trajectories whose
   QTM `Trajectory Type` field is 2, because doing so reproduces your TSV export
   exactly (19 markers, not 24). In the other two recordings the *same five physical
   markers* are type 1 with labels `base_0`..`base_4`. So type 2 looks like a
   per-project state — "removed from this file" — rather than a property of a marker.
   If you know what you did to that file in QTM, that would confirm it. If a future
   recording has type-2 trajectories you *do* want, the gate would be wrong.

3. **Should the analysis use measured `g` or nominal `g`?** It currently measures
   the recording's actual vertical acceleration and fits with that, while always
   reporting the discrepancy against 9.80665 (never absorbing it). That makes fit
   residuals meaningful instead of being dominated by the 2.6% offset. This is a
   deliberate deviation from DESIGN.md §6's single-pass description. Fine, or would
   you rather it always used 9.80665 and lived with the larger residuals?

---

## 6. Things to look at

- `BUILD_LOG.md`, Phase 2, "The gravity finding". It is the one result in this run
  that changes what the project can claim, and it is short.
- `docs/qtm-format.md` — the `.qtm` format write-up is now substantially more
  complete: `Measurement NBC/Data Items` turns out to be a stream of typed objects,
  and `Measurement NBC/Metadata` is a **schema that names every field**, which
  turned the field map from inference into fact. If you ever want a standalone
  `.qtm` reader library, that document is the asset.

---

## 7. Blocked work

*(completed at the end of the run — see the closing summary)*

---

## 8. Acceptance disagreements

**Phase 2, fitted `g` within 2% of 9.80665: FAILS at −2.59% and −2.65%.**
Not an off-by-one and not tunable. See item 1 above and BUILD_LOG.md Phase 2 for
the full evidence, including what was ruled out.

*(Phase 5's catch-count disagreements, if any, are added at the end of the run.)*
