# juggling-analyser

Analyse **ball-juggling sessions from motion-capture recordings**. Point it at a
Qualisys `.qtm` file and it will (eventually) tell you how many balls are in the
air, what pattern you're juggling in [siteswap](https://en.wikipedia.org/wiki/Siteswap)
notation, how many catches you made before a drop, how high and how consistently
you throw, and how much work you're doing — plus a 3D replay of the session with
configurable ball trails.

> **Status: early alpha — Phase 4 of 10.** Ingestion is finished and pinned: the
> reader reproduces a QTM TSV export of the 5-ball clip frame for frame for all 19
> trajectories, to 5.0e-07 m — the export's own rounding floor. Flight segmentation
> and the derived juggling frame are in, and they found something: measured gravity
> in the sample recordings is **2.6% below 9.80665**, consistently across two
> independent clips, which points at the capture calibration rather than the
> analysis ([BUILD_LOG](BUILD_LOG.md)). Identity linking recovers exactly 5 balls
> from the 5-ball clip. Events, notation, metrics and replay are next.
> See the [roadmap](#roadmap).

## Why

Motion capture gives millimetre-accurate 3D ball positions at hundreds of hertz —
far richer than video — yet no tool turns that into juggling analysis. The mature
juggling software (Juggling Lab, JoePass) only goes *notation → animation*; the
video apps only count catches. This project goes the other way: **measured motion → notation and metrics.**

## Install

```bash
pip install -e .            # core reader + analysis
pip install -e ".[dev]"     # + pytest / hypothesis / ruff / mypy
```

Requires Python 3.11+. Depends on `numpy`, `scipy`, `olefile`, and `python-lzo`
(for the `.qtm` decompressor). On Linux, `python-lzo` builds from source and needs
the LZO headers (`apt install liblzo2-dev`).

Development uses one quality gate:

```bash
python tools/gate.py        # ruff check + ruff format --check + mypy + pytest
```

It must be green before every commit, and CI runs exactly the same command.

## Usage

```console
$ python -m juggling_analyser info data/5_ball_juggling_cut_balls_only.qtm
data/5_ball_juggling_cut_balls_only.qtm
  300 Hz, 16.6 s (4967 frames), qtm frame
  19 trajectories (0 with internal gaps): 10 ball, 9 spurious
  source: 41 data series, 25 decodable, 24 trajectory objects, 19 exported
  skipped 1 series with no trajectory object: 232
  skipped 5 trajectories QTM does not export (Trajectory Type [2]); pass --all to include them

10 ball trajectorie(s); 5 active at frame 1.
```

```python
import juggling_analyser as ja

session = ja.load("data/5_ball_juggling_cut_balls_only.qtm")  # read + classify
print(session.summary())
for ball in session.balls:
    # frames are absolute and 1-based; one ball may span several trajectories
    print(ball.id, ball.first_frame, ball.last_frame, ball.height_span)
```

## Reading `.qtm` directly

`.qtm` is an undocumented binary format. This project reads it **without a QTM
install or any export step** — no CSV/TSV/C3D round-trip. The container is OLE2;
the marker samples are LZO1X-compressed with a custom block framing, recovered by
disassembling Qualisys's `NBC.dll`; the trajectory *descriptions* — labels,
colours, types, and the absolute frame ranges — live in a separate typed-object
stream whose field names come from a schema inside the file itself. Full write-up:
[`docs/qtm-format.md`](docs/qtm-format.md).

Each recovered sample keeps QTM's per-sample residual and measured/gap-filled
flag — quality information a text export would discard. The residual becomes a
per-sample position uncertainty that is carried through the whole pipeline.

Ingestion is pinned against a QTM TSV export of the 5-ball clip: all 19
trajectories reproduce frame for frame, positions to **5.0e-07 m**, which is the
export's own 1 µm text quantisation. Two gates are needed to get from the file's
25 decodable data series to 19 real trajectories, and both are tested.

## Architecture

A pure-Python analysis core behind one source-agnostic data model
([`core/trajectory.py`](src/juggling_analyser/core/trajectory.py)), so the mocap
reader, a future real-time stream, and a future video front-end all feed the same
pipeline:

```
.qtm ─▶ io.read_qtm ─▶ clean (ball/static/ghost) ─▶ track & link ─▶ events ─▶ metrics
                                                          │
                                                          └─▶ web 3D replay
```

## Roadmap

Built phase by phase against [`PLAN.md`](PLAN.md); the design is frozen in
[`DESIGN.md`](DESIGN.md) and the symbols in [`NOTATION.md`](NOTATION.md).

- [x] Raw `.qtm` reader (OLE2 + LZO1X) → trajectories
- [x] Trajectory classification
- [x] Toolchain, quality gate, core-purity enforcement, CI *(P0)*
- [x] Reader v2 — absolute frame ranges from the `Parts` table, per-sample
      uncertainty, phantom-series fix, pinned against a TSV oracle *(P1)*
- [x] Flight segmentation + derived juggling frame *(P2 — the `g` self-check
      fails on the current corpus; see BUILD_LOG)*
- [~] Synthetic ground truth — Airtime's exporter and 8 labelled fixtures are in
      (`data/truth/`); this repo's degradation model is **not** built yet *(P3)*
- [~] Identity linking — trajectories → balls across gaps. The real 5-ball clip
      links into exactly 5 balls tiling the clip with 383 frames of gap; the
      synthetic 5-ball case falls short of 100% *(P4, see BUILD_LOG)*
- [ ] Events — throws, catches, apexes, drops, hands, runs *(P5 — not built, but
      the headline catch total already measures 55 against a hand-counted 55;
      the run split does not yet agree, see OWNER_ACTIONS)*
- [ ] Siteswap extraction from the beat grid *(P6)*
- [ ] Versioned session JSON *(P7)*
- [ ] 3D replay with configurable trails *(P8)*
- [ ] Metrics — throw height, beat rate, **dwell time**, dwell ratio, energy,
      repeatability distributions *(P9)*
- [ ] Real-time analysis via the Qualisys RT SDK *(deferred)*
- [ ] 2D video ingestion *(deferred)*
- [ ] Measured-vs-ideal overlay against [Airtime](https://github.com/Jugleer/airtime) *(v2)*

## License

MIT
