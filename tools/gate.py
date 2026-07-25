"""The quality gate: ``python tools/gate.py``.

Four checks, in the order that fails cheapest first::

    ruff check          lint, including the core-purity import bans
    ruff format --check formatting
    mypy                strict typing
    pytest              the test suite

It must be green before every commit (CLAUDE.md rule 2). Its definition is
deliberately stable — phases add tests, never new gate stages or flags — so
that "the gate was green" means the same thing at Phase 9 as at Phase 0.

Everything runs through ``sys.executable -m <tool>``, so the gate works with no
console-script shims on ``PATH`` (CLAUDE.md rule 5).
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STAGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ruff check", ("-m", "ruff", "check", ".")),
    ("ruff format", ("-m", "ruff", "format", "--check", ".")),
    ("mypy", ("-m", "mypy")),
    ("pytest", ("-m", "pytest")),
)


def run_stage(name: str, args: tuple[str, ...]) -> tuple[bool, float]:
    """Run one gate stage; return ``(passed, elapsed_seconds)``."""
    print(f"\n=== {name} " + "=" * max(0, 60 - len(name)))
    started = time.perf_counter()
    completed = subprocess.run([sys.executable, *args], cwd=ROOT, check=False)
    return completed.returncode == 0, time.perf_counter() - started


def main() -> int:
    results: list[tuple[str, bool, float]] = []
    for name, args in STAGES:
        passed, elapsed = run_stage(name, args)
        results.append((name, passed, elapsed))

    print("\n" + "=" * 68)
    for name, passed, elapsed in results:
        print(f"  {'PASS' if passed else 'FAIL'}  {name:<14} {elapsed:6.2f}s")
    failed = [name for name, passed, _ in results if not passed]
    if failed:
        print(f"\nGATE RED: {', '.join(failed)}")
        return 1
    print("\nGATE GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
