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

