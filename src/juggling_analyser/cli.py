"""Command-line entry point.

Currently exposes ``info``, which reads a recording and prints what the ingestion
+ cleaning stages find. Analysis (``analyse``) and replay (``replay``) commands
land as their pipeline stages come online.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from juggling_analyser import load
from juggling_analyser.core.trajectory import Fragment


def _fmt_fragment(f: Fragment) -> str:
    b = f.bounds()
    if b is None:
        extent = "                       "
    else:
        lo, hi = b
        extent = f"z[{lo[2]:+.2f},{hi[2]:+.2f}]m span {f.height_span:.2f}m"
    return (
        f"  {f.id:>5}  {f.kind:<7} {f.n_samples:>5} smp  "
        f"{f.duration:5.2f}s  res {f.mean_residual * 1000:4.2f}mm  {extent}"
    )


def _cmd_info(args: argparse.Namespace) -> int:
    session = load(args.file)
    print(session.summary())
    if args.verbose:
        print()
        for f in sorted(session.fragments, key=lambda f: (f.kind, -f.n_samples)):
            print(_fmt_fragment(f))
    n_balls = len(session.balls)
    if n_balls:
        print(
            f"\n{n_balls} ball fragment(s). Note: absolute timing / ball identity "
            "is recovered by the tracker (not yet implemented) - see docs/qtm-format.md."
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="juggling-analyser",
        description="Analyse ball-juggling from motion-capture recordings.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_info = sub.add_parser("info", help="Summarise a .qtm recording.")
    p_info.add_argument("file", help="Path to a .qtm file.")
    p_info.add_argument("-v", "--verbose", action="store_true", help="List every fragment.")
    p_info.set_defaults(func=_cmd_info)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.func
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
