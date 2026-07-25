# CLAUDE.md

juggling-analyser turns motion-capture recordings of ball juggling into siteswap
notation and performance metrics, plus a 3D replay. It is the inverse of
[Airtime](https://github.com/Jugleer/airtime) (notation → motion). Pre-1.0, built
phase-by-phase; see `PLAN.md` for the plan and `BUILD_LOG.md` for status.

## Documents (read before coding)

- `NOTATION.md` — **normative** symbols, identities, frames, terms. All code and
  docs conform. Shares its parent with Airtime's `NOTATION.md`.
- `DESIGN.md` — the frozen design: scope, architecture, algorithms, metric
  definitions, UI. § references in other docs point here.
- `PLAN.md` — phased implementation plan with acceptance gates.
- `BUILD_LOG.md` — ledger: phase status, decisions, deferred items.
- `docs/qtm-format.md` — the reverse-engineered `.qtm` format.

## Hard rules

1. **Core purity**: `src/juggling_analyser/core/**` imports nothing from `io/`,
   `viewer/`, `cli`, or any I/O, plotting, or web library, and never reads the wall
   clock (`time.time`, `datetime.now`, `perf_counter`) or unseeded randomness
   (`random.*`, `np.random.seed`, the global RNG). Randomness arrives as an explicit
   `np.random.Generator` argument. Every core function is a pure function of its
   arguments. This is what makes the analysis reproducible, testable, and
   diffable across runs — it is load-bearing (DESIGN.md §2). Enforced by lint and
   a test; do not weaken it.
2. **The gate**: `python tools/gate.py` (ruff check + ruff format --check + mypy +
   pytest) must be green before every commit. Never commit red. Never weaken or
   delete a test to reach green — surface the problem instead.
3. **Measurement is the truth.** Where measured data contradicts an idealised
   juggling identity, report the discrepancy; never snap the measurement to the
   ideal. No silent smoothing, no silent gap-filling: anything inferred is flagged
   as inferred and carries its uncertainty.
4. **Units and frame**: metres, seconds, kilograms; the juggling frame (X = left
   hand → right hand, Y = forward, Z = up) per NOTATION.md. Frame indices are
   1-based; array indices are 0-based; never conflate them in one variable.
5. **No console-script entry points.** This machine cannot write `.exe` shims into
   `C:\Python311\Scripts` (see Environment). Everything is reachable via
   `python -m juggling_analyser ...`.

## Commands

Run everything through the venv interpreter (`./.venv/Scripts/python.exe`, or
`python` with the venv activated):

```bash
python tools/gate.py                       # ruff + mypy + pytest — the pre-commit gate
python -m pytest -q                        # tests only
python -m juggling_analyser info <file>    # summarise a recording
python -m juggling_analyser analyse <file> # full analysis -> session JSON
python -m juggling_analyser serve <file>   # local viewer at http://127.0.0.1:8000
```

## Environment

Windows 10, Python 3.11.3, Node 22.14, PowerShell + Git Bash.

**Always work inside the project venv `.venv/`.** The global interpreter has a
persistent lock on `C:\Python311\Scripts` that stops pip writing or renaming `.exe`
shims, which breaks console scripts and blocks some installs outright (`ruff`,
`mypy`, numpy 2). The venv has its own `Scripts` directory and is unaffected —
verified: `ruff 0.16.0` and `mypy 2.3.0` install into it cleanly.

```bash
python -m venv .venv                       # once
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

Still invoke tools as `python -m <tool>` and never add a `[project.scripts]` entry
point, so nothing depends on a shim. If a tool cannot be installed even in the
venv, record it in `BUILD_LOG.md` rather than working around it silently.

Sample recordings live in `data/`. `data/5_ball_juggling_cut_balls_only.tsv` is a
QTM export used **only** as a validation oracle — the pipeline never reads TSV.

## Conventions

- Commits: `phase-N: summary` with trailer `Phase: N` during the phased build;
  conventional prefixes (`fix:`, `docs:`, `refactor:`) otherwise.
- Deferred or discovered work goes in `BUILD_LOG.md` under the phase entry, not in
  `TODO` comments.
- Public functions carry type hints and a docstring saying what it returns in what
  units. Comment density matches the surrounding module.
- Tests that need a real recording are skipped (not failed) when `data/` is absent,
  so a fresh clone without the corpus still gates green.
