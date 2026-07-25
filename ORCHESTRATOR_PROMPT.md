# ORCHESTRATOR_PROMPT.md

The protocol for an autonomous build session. The owner is not available during
the run: your job is to build as far down `PLAN.md` as the evidence allows, then
hand back a precise list of what only they can do.

## Mission

Build `juggling-analyser` phase by phase per `PLAN.md`, starting at Phase 0.
Get as far as you legitimately can without owner input. Then write
`OWNER_ACTIONS.md` (§9) telling them exactly what to record, export, look at, and
decide.

**Quality over distance.** Reaching Phase 9 with a weakened test is a failed run.
Stopping at Phase 5 with everything genuinely green, and a clear account of what
blocked you, is a successful one.

## 1. Read first, in this order

1. `NOTATION.md` — normative symbols, frames, terms. Non-negotiable.
2. `DESIGN.md` — the frozen design. § references point here.
3. `PLAN.md` — the phases and their acceptance criteria.
4. `CLAUDE.md` — hard rules, commands, environment.
5. `BUILD_LOG.md` — decisions already made and open items.
6. `docs/qtm-format.md` — the `.qtm` format.

These documents are the source of truth and were agreed with the owner. If you
believe one is *wrong*, say so in `BUILD_LOG.md` and proceed under it — do not
silently redesign. If one is merely *silent* on something, make the call yourself
and record it.

## 2. Verified environment (do not re-litigate)

- Python 3.11.3, Node 22.14.0, npm 10.9.2, `gh` 2.89 authenticated as `Jugleer`
  with `repo` + `workflow` scopes.
- **Work inside `.venv/`** (already created). The global `C:\Python311\Scripts` is
  locked against `.exe` shims; the venv is not. `ruff 0.16.0` and `mypy 2.3.0`
  install there cleanly. Invoke tools as `python -m <tool>`; never add a
  `[project.scripts]` entry point.
- Git remote `origin` → `github.com/Project-DeepBlue-Juggling/juggling-analyser`,
  branch `main`, **no commits yet**. Phase 0 makes the first one.
- Airtime is a sibling checkout at `../airtime` (`github.com/Jugleer/airtime`),
  a working Node/TS SPA. Its `NOTATION.md` is the shared parent of ours.
- `data/` holds three `.qtm` recordings and one TSV oracle. **Never delete or
  modify anything in `data/`.**

## 3. Autonomy

**Do freely**: write and refactor code, install into `.venv`, run `npm install`
and build the viewer SPA, run headless browser screenshots, commit, and push to
`main` of *this* repo. Use subagents and multi-agent workflows as much as you find
useful — the owner has explicitly authorised orchestration for this build.

**Do not, under any circumstances**:

- create a GitHub release, push a tag, or change repository settings or visibility
- merge anything into Airtime's `main`, or trigger its Pages deployment
- delete or edit files in `data/`
- weaken, skip, or delete a test to reach green
- stop and wait for the owner mid-run — queue the question in `OWNER_ACTIONS.md`
  and carry on with work that does not depend on the answer

## 4. Per-phase protocol

For each phase in `PLAN.md`, in order:

1. Re-read that phase's entry and the DESIGN.md sections it cites.
2. Build it. Write tests as you go, not afterwards.
3. Run `python tools/gate.py`. It must be green.
4. Check the phase's **Accept** criteria honestly, with actual measured numbers.
5. Append a `BUILD_LOG.md` entry: status, the numbers you actually got, decisions
   taken, and anything discovered-but-deferred.
6. Commit as `phase-N: summary` with a `Phase: N` trailer. Push.
7. Update the README status line if it changed.

Never commit red. Never batch two phases into one commit.

## 5. Failure policy

If a phase's acceptance criteria cannot be met:

- **Stop that phase.** Do not tune parameters until a number matches, and do not
  relax the criterion. Both are silent corruption of the result.
- Record in `BUILD_LOG.md`: what you attempted, the actual measured values, and
  your best hypothesis for the gap.
- Then triage. If later phases do not depend on the failure, continue and carry
  the failure forward as an open item. If they do, skip to the most valuable
  independent work remaining and note the skip.

**Specifically on Phase 5.** The acceptance target — 22 catches → drop → 2
collection catches → 31 catches — is the owner's manual count of a noisy
recording. If your pipeline produces different numbers, that is a *finding*, not
permission to fit the data. Report your counts, your confidence, and the specific
events you disagree about (with timestamps), so the owner can check them against
the recording. An off-by-one in a human count of 31 catches is entirely possible;
so is a bug. Do not assume which.

## 6. Cross-repo work (Airtime)

Phase 3 needs a ground-truth exporter in Airtime. Rules:

- Work on a branch: `feat/truth-export`. Never commit to `main`.
- Keep it small and additive. Respect Airtime's own `CLAUDE.md` hard rules —
  especially core purity and its `npm run gate`, which must be green before you
  commit there.
- The exporter writes exact labelled trajectories sampled at `f_s`: pattern,
  per-ball positions, every event with its type and time, and ball identities.
  Clean data only — **the noise model lives in this repo**, not in Airtime.
- Push the branch. Do not open a PR, merge, or deploy. Flag it for owner review
  in `OWNER_ACTIONS.md`.
- Commit the generated fixtures here so our CI never needs Node.

## 7. Judgement calls you should make yourself

- Tuning the `DESIGN.md` §13 defaults against synthetic data — that is expected
  work, not a design change. Record the tuned values and how you arrived at them.
- Module and function decomposition inside the `DESIGN.md` §2 module map.
- Test strategy and fixture design.
- Anything `DESIGN.md` is silent on.

Escalate to `OWNER_ACTIONS.md` only what genuinely requires them: new recordings,
physical facts about the capture setup, subjective judgement on the viewer's look,
and any acceptance disagreement per §5.

## 8. Verification discipline

The whole project is an inverse problem, which means plausible-looking wrong
answers are the main hazard. Before believing a result:

- Check it against an independent identity — a fitted `g` within 2% of 9.80665,
  `b = mean(h)`, energy conservation within a flight, `t_air` matching `h·τ_b − t_d`.
- Prefer synthetic data with known truth over eyeballing real data.
- When a number looks good, try to break it: a different clip, a noisier synthetic
  case, a pattern with 1s and 0s in it.

## 9. Final deliverable: `OWNER_ACTIONS.md`

Write this at the end of the run, at the repo root. It is the owner's entire
to-do list, so make it specific and ordered by value. For every item give: what to
do, **why** it is needed, which phase it unblocks, and roughly how long it takes.

Cover at least:

- **Recordings to make** — be exact. Ball counts, named patterns to juggle
  (e.g. `3`, `441`, `531`, `552`, `423`, `4`, `5`), how many deliberate drops,
  deliberate clean stops, a long run to test scale, and a 7-ball attempt if
  feasible. State any conventions that would make the data easier to analyse
  (e.g. a clear pause between runs), but keep the list short enough to actually
  get done in one session.
- **Exports to share** — notably a TSV export of `3_ball_juggling_cut`, which
  would give a second, *noisy* reader oracle.
- **Things to look at** — viewer screenshots you captured (commit them under
  `docs/`), and any result you want a human eye on.
- **Decisions needed** — anything queued during the run.
- **Blocked work** — what you could not do, why, and what would unblock it.
- **Acceptance disagreements** — per §5, with timestamps.

End the file with an honest one-paragraph summary: how far you got, what is solid,
what is shaky.

## 10. Done

The run is complete when either every phase through P9 is green, or you have
exhausted the work that does not require the owner. Phase 10's release and tagging
steps are **explicitly out of scope** — leave those for the owner.

Final commit and push. Make sure `BUILD_LOG.md` and `OWNER_ACTIONS.md` are current
and honest. The owner should be able to read those two files and know exactly what
happened without reading a line of code.
