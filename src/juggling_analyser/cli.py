"""Command-line entry point.

``info`` reads a recording and prints what ingestion and cleaning found.
``analyse`` and ``serve`` land with their pipeline stages (PLAN.md P7, P8).

There is no console-script shim: everything runs as
``python -m juggling_analyser ...`` (CLAUDE.md rule 5).
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Callable

from juggling_analyser.core.clean import classify_session, refine_with_flights
from juggling_analyser.core.flight import event_positions, segment_session
from juggling_analyser.core.frame import derive_frame
from juggling_analyser.core.params import GRAVITY
from juggling_analyser.core.trajectory import Trajectory
from juggling_analyser.io.qtm import read_qtm, scan_qtm


def _format_trajectory(trajectory: Trajectory, f_s: float) -> str:
    bounds = trajectory.bounds()
    extent = ""
    if bounds is not None:
        low, high = bounds
        extent = f"z[{low[2]:+.2f},{high[2]:+.2f}]m span {trajectory.height_span:.2f}m"
    gaps = trajectory.gaps()
    gap_note = f" {len(gaps)} gap(s)" if gaps else ""
    sigma = trajectory.uncertainty.sigma()
    label = f" {trajectory.label!r}" if trajectory.label else ""
    return (
        f"  {trajectory.id:>5}  {trajectory.kind:<8} "
        f"k[{trajectory.first_frame:>5},{trajectory.last_frame:>5}] "
        f"{trajectory.n_samples:>5} smp {trajectory.n_samples / f_s:6.2f}s  "
        f"sigma {sigma.mean() * 1000:4.2f}mm  {extent}{gap_note}{label}"
    )


def _cmd_info(args: argparse.Namespace) -> int:
    session, _report = classify_session(read_qtm(args.file, include_unexported=args.all))
    print(session.summary())

    scan = scan_qtm(args.file)
    print(
        f"  source: {len(scan.series_ids)} data series, "
        f"{len(scan.decodable_series)} decodable, "
        f"{len(scan.objects)} trajectory objects, "
        f"{len(scan.exported_objects)} exported"
    )
    if scan.orphan_series:
        print(
            f"  skipped {len(scan.orphan_series)} series with no trajectory object: "
            f"{', '.join(str(s) for s in scan.orphan_series)}"
        )
    if scan.unexported_objects:
        types = sorted({o.trajectory_type for o in scan.unexported_objects})
        verb = "included" if args.all else "skipped"
        hint = "" if args.all else "; pass --all to include them"
        print(
            f"  {verb} {len(scan.unexported_objects)} trajectories QTM does not "
            f"export (Trajectory Type {types}){hint}"
        )
    if scan.empty_objects:
        print(f"  skipped {len(scan.empty_objects)} trajectory objects with no samples")

    if args.verbose:
        print()
        for trajectory in sorted(session.trajectories, key=lambda t: (t.kind, -t.n_samples)):
            print(_format_trajectory(trajectory, session.f_s))

    active = session.active_at(1)
    print(f"\n{len(session.balls)} ball trajectorie(s); {len(active)} active at frame 1.")

    segmentation = segment_session(session)
    session, report = refine_with_flights(session, segmentation.flights)
    suspect = sum(1 for f in segmentation.flights if f.is_suspect())
    complete = len(segmentation.complete_flights)
    print(
        f"\nflights: {len(segmentation.flights)} ({complete} with a throw and a catch at "
        f"both ends, {suspect} suspect); after the physics pass: {report}"
    )
    check = segmentation.gravity_check
    if check is not None:
        print(
            f"  measured g over {check.n_flights} long flights: {check.median:.4f} m/s^2, "
            f"{check.relative_error * 100:+.2f}% vs {GRAVITY} "
            f"(spread {check.spread:.3f}); fits used {segmentation.gravity_used:.4f}"
        )
        if check.within > 0.02:
            print(
                "  NOTE: that is outside 2%. It is consistent across recordings, so it "
                "reads as a capture-scale or sample-rate offset, not an analysis error "
                "— see OWNER_ACTIONS.md."
            )

    cloud = event_positions(segmentation.flights)
    if len(cloud) >= 2:
        transform = derive_frame(cloud)
        diagnostics = transform.diagnostics
        quality = (
            f", anisotropy {diagnostics.anisotropy:.1f}, axis "
            f"+/-{math.degrees(diagnostics.axis_angle_sigma):.2f} deg"
            if diagnostics is not None
            else ""
        )
        print(
            f"  juggling frame from {len(cloud)} throw/catch positions: hand axis "
            f"{math.degrees(transform.angle_to_nominal_hand_axis()):.2f} deg from nominal"
            f"{quality}"
        )
        print(f"  origin (QTM frame): {transform.origin.round(4).tolist()}")

    print("Ball identity across gaps is recovered by the linker (PLAN.md P4), not by the reader.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m juggling_analyser",
        description="Analyse ball-juggling from motion-capture recordings.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_info = sub.add_parser("info", help="Summarise a .qtm recording.")
    p_info.add_argument("file", help="Path to a .qtm file.")
    p_info.add_argument("-v", "--verbose", action="store_true", help="List every trajectory.")
    p_info.add_argument(
        "--all",
        action="store_true",
        help="Include trajectories QTM would not export (Trajectory Type != 1).",
    )
    p_info.set_defaults(func=_cmd_info)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.func
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
