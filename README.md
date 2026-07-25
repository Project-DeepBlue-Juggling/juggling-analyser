# juggling-analyser

Analyse **ball-juggling sessions from motion-capture recordings**. Point it at a
Qualisys `.qtm` file and it will (eventually) tell you how many balls are in the
air, what pattern you're juggling in [siteswap](https://en.wikipedia.org/wiki/Siteswap)
notation, how many catches you made before a drop, how high and how consistently
you throw, and how much work you're doing — plus a 3D replay of the session with
configurable ball trails.

> **Status: early alpha — Phase 0 of 10 complete** (toolchain, quality gate, CI).
> The raw `.qtm` reader works and is tested; the tracking, metrics, and replay
> stages are under active construction. See the [roadmap](#roadmap).

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

```bash
python -m juggling_analyser info data/3_ball_juggling_cut.qtm -v
```

```python
import juggling_analyser as ja

session = ja.load("data/5_ball_juggling_cut.qtm")  # read + classify
print(session.summary())
for ball in session.balls:  # ball trajectory fragments
    print(ball.id, ball.n_samples, ball.height_span)
```

## Reading `.qtm` directly

`.qtm` is an undocumented binary format. This project reads it **without a QTM
install or any export step** — no CSV/TSV/C3D round-trip. The container is OLE2;
the marker data is LZO1X-compressed with a custom block framing, recovered by
disassembling Qualisys's `NBC.dll`. Full write-up: [`docs/qtm-format.md`](docs/qtm-format.md).

Each recovered sample keeps QTM's per-sample residual and measured/gap-filled
flag — quality information a text export would discard.

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
- [ ] Reader v2 — absolute frame ranges from the piece table, per-sample
      uncertainty, phantom-series fix *(P1)*
- [ ] Flight segmentation + derived juggling frame *(P2)*
- [ ] Synthetic ground truth — Airtime exports clean truth, this repo degrades it
      into realistic mocap *(P3)*
- [ ] Identity linking — trajectories → balls across gaps *(P4)*
- [ ] Events — throws, catches, apexes, drops, hands, runs *(P5)*
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
