# NOTATION.md — Canonical Symbols & Identities

This file is **normative**. All code, docs, UI labels, and commit messages use these
symbols and definitions. If a concept isn't here and needs a symbol, add it here first.

Juggling-analyser is the inverse of [Airtime](https://github.com/Jugleer/airtime)
(notation → motion; this project is motion → notation). **Airtime's `NOTATION.md` is
the shared parent**: every symbol it defines means the same thing here. This file
restates the shared core, records the deliberate deltas (§ Deltas), and adds the
symbols that only a *measurement* project needs (§ Measurement).

## Shared glossary (identical to Airtime)

| Symbol   | Meaning                                        | Unit  |
|----------|------------------------------------------------|-------|
| `b`      | Number of balls                                | count |
| `h`      | Siteswap throw value (the "digit")             | beats |
| `n_h`    | Number of hands (2 for a human; 1 for Jugglebot) | count |
| `τ_b`    | Beat period                                    | s     |
| `t_d`    | Dwell time (catch → throw of the same ball)    | s     |
| `t_e`    | Hand-empty time                                | s     |
| `t_air`  | Air time of a throw                            | s     |
| `z_apex` | Apex height **above the throw point**          | m     |
| `g`      | Gravitational acceleration (9.80665)           | m/s²  |
| `r_d`    | Dwell ratio, `r_d = t_d / (n_h · τ_b)`         | –     |

Shared identities (Airtime NOTATION §Identities) hold as *expectations to test
against measurement*, not as constructions:

1. `t_air(h) = h·τ_b − t_d` — measured air time should match the ordinal throw value.
2. `t_d + t_e = n_h·τ_b` — a hand's full cycle.
3. `z_apex = g·t_air²/8` — for equal throw and catch heights.
4. `b = mean(h)` over a pattern — the average theorem, used to cross-check the
   detected ball count against the extracted siteswap.

Where a measurement disagrees with an identity, **the measurement is the truth and
the disagreement is a reported metric** (that is the entire point of this project).

## Measurement symbols (this project only)

| Symbol    | Meaning                                                     | Unit   |
|-----------|-------------------------------------------------------------|--------|
| `f_s`     | Capture sample rate (300 Hz in current data)                 | Hz     |
| `k`       | Absolute frame index, **1-based** (QTM's convention)          | –      |
| `m`       | Ball mass (0.071 kg)                                          | kg     |
| `d`       | Ball diameter (0.074 m)                                       | m      |
| `σ`       | Per-sample position uncertainty (1σ)                          | m      |
| `Σ`       | Per-sample 3×3 position covariance                            | m²     |
| `z_c`     | Catch plane — the median height of detected catches           | m      |
| `t_thr`   | Throw instant (start of free flight)                          | s      |
| `t_cat`   | Catch instant (end of free flight)                            | s      |
| `W⁺`      | Positive mechanical work done on a ball by the hand           | J      |
| `W⁻`      | Negative work (catch absorption); reported as a magnitude     | J      |
| `W_net`   | `W⁺ − W⁻` over a carry, run, or session                       | J      |
| `P̄`      | Average mechanical power over a run or session                | W      |

## Terms

- **frame** — one capture sample instant, indexed by `k`.
- **trajectory** — what QTM tracked: a numbered marker path with an absolute frame
  range. **Not** a ball. May be *Mixed* (several pieces separated by gaps).
- **piece** — one contiguous `(start_frame, end_frame, type)` run inside a trajectory.
- **ball** — a physical ball, reconstructed by linking trajectories across gaps.
  One ball spans many trajectories; one trajectory belongs to at most one ball.
- **flight** — a segment where the ball's acceleration is `(0, 0, −g)` within tolerance.
- **carry** — the catch → throw segment where a hand holds the ball (Airtime's term).
- **event** — a throw, catch, apex, or drop instant.
- **run** — a maximal span of continuous juggling, ended by a drop or a deliberate
  stop (all balls collected). Runs are the unit that "31 catches" counts over.
- **session** — one recording, containing zero or more runs plus dead time.
- **drop** — a ball descending below `z_c` minus a threshold while unheld.
- **occlusion** — loss of tracking *above* `z_c`; bridged, never counted as a drop.

## Frames of reference

- **QTM frame** (`_Q`): as recorded. X forward, Y left, Z up. Right-handed. Origin
  is the calibration origin, which is not physically meaningful for us.
- **Juggling frame** (`_J`, canonical — all analysis and output use this):
  **X = left hand → right hand, Y = forward, Z = up.** Right-handed.

  ```
  x_J = −y_Q      y_J = x_Q      z_J = z_Q
  ```

  Equivalently: axes rotated −90° about Z, i.e. points rotated +90° about Z.

  **Origin** = the mean of all detected catch and throw positions (the point between
  the hands). The QTM calibration origin and the floor are both discarded — neither
  is reliably identifiable, and no metric depends on them.

  This is the *nominal* mapping. The hand axis is **derived per recording** from the
  principal axis of the catch/throw cloud; the nominal mapping only fixes the sign
  (which end is the right hand), so session-to-session yaw self-corrects.

## Conventions

- Units are **metres, seconds, kilograms, radians**. Angles in radians internally,
  degrees only in UI.
- Time `t = (k − 1) / f_s`, so frame 1 is `t = 0`.
- Frame indices are 1-based inclusive (matching QTM and the `.qtm` piece tables);
  numpy array indices are 0-based. Never mix them in one variable — a variable
  holding a frame index is named `frame`/`k`, an array index is `i`/`idx`.
- Code identifiers use descriptive names (`beatPeriod` → `beat_period`, `dwell_time`,
  `air_time`, `throw_value`, `ball_count`, `hand_count`); symbols live in comments
  and docs.
- Hands are named `left` / `right`, never indexed by number, until `n_h > 2` exists.

## Deltas from Airtime

| | Airtime | Here | Why |
|---|---|---|---|
| Mass | normalised to 1 kg | real, `m = 0.071 kg` | measured balls have a real mass |
| Energy | J/kg only | **J and J/kg** | J is the answer; J/kg keeps Airtime comparable |
| Up axis | y-up in core, z-up display frame | **z-up everywhere** | QTM is z-up; one frame, no conversion layer |
| `g` | 9.81, user-adjustable | 9.80665, fixed | it is a measured constant, not a knob |
| Time | closed-form function of `t` | sampled at `f_s`, interpolated | measurement is discrete |
| Truth | the pattern defines the motion | the motion defines the pattern | inverse problem |
