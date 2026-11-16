"""Command line entry point.

Slicer post-processing (Snapmaker Orca: Process > Others > Post-processing
Scripts) calls the script with the temporary G-code path as the last
argument and expects the file to be rewritten in place:

    "python" -m u1skew fix --xy 0.0035

`u1skew calc` turns caliper readings into the factor.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

from .core import Bounds, Skew, SkewError, correct_text
from .measure import angle_from_factor, factor_from_angle, skew_factor

ENV_KEYS = {"xy": "U1SKEW_XY", "xz": "U1SKEW_XZ", "yz": "U1SKEW_YZ"}


def _fix(ns) -> int:
    skew = Skew(xy=ns.xy, xz=ns.xz, yz=ns.yz)
    bounds = None if ns.no_bounds else Bounds(0, ns.bed_x, 0, ns.bed_y)
    with open(ns.gcode, encoding="utf-8", errors="surrogateescape", newline="") as f:
        text = f.read()
    try:
        out, rep = correct_text(text, skew, bounds=bounds, chord_tol=ns.chord_tol)
    except SkewError as e:
        print(f"u1skew: {e}", file=sys.stderr)
        return 2
    if rep.out_of_bounds and not ns.allow_out_of_bounds:
        first = rep.out_of_bounds[0]
        print(
            f"u1skew: {len(rep.out_of_bounds)} moves would leave the bed after correction "
            f"(first: line {first[0]} -> X{first[1]} Y{first[2]}); file NOT modified. "
            "Move the part toward the bed centre or pass --allow-out-of-bounds.",
            file=sys.stderr,
        )
        return 3
    dest = ns.output or ns.gcode
    d = os.path.dirname(os.path.abspath(dest))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".u1skew")
    with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(out)
    os.replace(tmp, dest)
    print(
        f"u1skew: xy={skew.xy:.6g} ({angle_from_factor(skew.xy):+.3f} deg); "
        f"{rep.lines_changed} lines changed, {rep.moves_xy_added} moves got a missing X/Y, "
        f"{rep.arcs_flattened} arcs -> {rep.arc_segments} segments, max shift {rep.max_shift:.3f} mm",
        file=sys.stderr,
    )
    for w in rep.warnings[:5]:
        print(f"u1skew: warning: {w}", file=sys.stderr)
    return 0


def _calc(ns) -> int:
    if ns.angle is not None:
        f = factor_from_angle(ns.angle)
    else:
        f = skew_factor(ns.ac, ns.bd, ns.ad)
    print(f"factor {f:.8f}  (axis angle {90 + angle_from_factor(f):.4f} deg)")
    print(f"use:    --xy {f:.8f}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="u1skew", description="Skew-correct sliced G-code (no firmware changes).")
    sub = p.add_subparsers(dest="cmd", required=True)

    fx = sub.add_parser("fix", help="rewrite a G-code file in place")
    for k, env in ENV_KEYS.items():
        fx.add_argument(f"--{k}", type=float, default=float(os.environ.get(env, 0.0)),
                        help=f"{k.upper()} skew factor (default ${env} or 0)")
    fx.add_argument("--bed-x", type=float, default=270.0)
    fx.add_argument("--bed-y", type=float, default=270.0)
    fx.add_argument("--no-bounds", action="store_true", help="skip the bed bounds check")
    fx.add_argument("--allow-out-of-bounds", action="store_true")
    fx.add_argument("--chord-tol", type=float, default=0.005, help="max arc flattening error, mm")
    fx.add_argument("-o", "--output", help="write here instead of in place")
    fx.add_argument("gcode")
    fx.set_defaults(func=_fix)

    cc = sub.add_parser("calc", help="skew factor from caliper readings")
    cc.add_argument("--ac", type=float)
    cc.add_argument("--bd", type=float)
    cc.add_argument("--ad", type=float)
    cc.add_argument("--angle", type=float, help="measured deviation from 90 deg, degrees")
    cc.set_defaults(func=_calc)

    ns = p.parse_args(argv)
    if ns.cmd == "calc" and ns.angle is None and None in (ns.ac, ns.bd, ns.ad):
        p.error("calc needs --ac --bd --ad, or --angle")
    return ns.func(ns)


if __name__ == "__main__":
    sys.exit(main())
