"""Same input files through u1skew and gskewer; measure endpoint error.

For every motion line, the ideal result is Skew.apply(input machine point).
We replay both outputs with the printer model from the tests and report the
largest XY distance from that ideal, plus closure error of closed loops.

usage: python bench/compare.py <path to gskewer skew.py>
"""

import math
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))

from test_core import simulate  # noqa: E402
from u1skew.core import Skew, correct_text  # noqa: E402

XY = 0.01  # 0.573 deg, typical of the U1 skew reports on the forum

CASES = {
    "square, one axis per line": "G90\nM83\nG1 X50 Y50 Z0.2\nG1 X150 E5\nG1 Y150 E5\nG1 X50 E5\nG1 Y50 E5\n",
    "square, X and Y on every line": "G90\nM83\nG1 X50 Y50 Z0.2\nG1 X150 Y50 E5\nG1 X150 Y150 E5\nG1 X50 Y150 E5\nG1 X50 Y50 E5\n",
    "circle as G2 (arc fitting on)": "G90\nM83\nG1 X100 Y100 Z0.2\nG2 X100 Y100 I20 J0 E8\n",
    "G91 relative moves": "G90\nG1 X50 Y50 Z0.2\nG91\nG1 X100 E5\nG1 Y100 E5\nG1 X-100 E5\nG1 Y-100 E5\nG90\n",
}


def ideal_points(src):
    s = Skew(xy=XY)
    pts = []
    for x, y, z, _ in simulate(src):
        pts.append(s.apply(x, y, z)[:2])
    return pts


def arc_samples(src):
    """Ideal skewed points densely along the G2 circle for the arc case."""
    s = Skew(xy=XY)
    cx, cy, r = 120.0, 100.0, 20.0
    return [s.apply(cx + r * math.cos(t), cy + r * math.sin(t), 0)[:2]
            for t in [i * 2 * math.pi / 720 for i in range(720)]]


def dist_to_polyline(p, poly):
    best = float("inf")
    for (ax, ay), (bx, by) in zip(poly, poly[1:]):
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy
        t = 0 if L == 0 else max(0, min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L))
        best = min(best, math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy))
    return best


def gskewer(path, src):
    d = tempfile.mkdtemp()
    f = os.path.join(d, "in.gcode")
    open(f, "w").write(src)
    subprocess.run([sys.executable, "-I", path, "--xyskew", str(XY), "--yzskew", "0", "--xzskew", "0", f],
                   check=True, capture_output=True)
    outs = [n for n in os.listdir(d) if n != "in.gcode"]
    return open(os.path.join(d, outs[0])).read()


def measure(name, src, out):
    got = [(x, y) for x, y, _, _ in simulate(out)]
    if name.startswith("circle"):
        ideal = arc_samples(src)
        ideal.append(ideal[0])
        err = max(dist_to_polyline(p, ideal) for p in got[1:])
        # also how far the printed arc strays: an untouched G2 is a circle,
        # the ideal is an ellipse; sample the circle the firmware would draw
        if not any(l.startswith("G1") for l in out.splitlines()[3:]):
            circ = [(got[0][0] + 20 + 20 * math.cos(t), got[0][1] + 20 * math.sin(t))
                    for t in [i * 2 * math.pi / 720 for i in range(720)]]
            err = max(dist_to_polyline(p, ideal) for p in circ)
    else:
        ideal = ideal_points(src)
        err = max(math.dist(a, b) for a, b in zip(got, ideal))
    closure = math.dist(got[0], got[-1])
    return err, closure


def main():
    g = sys.argv[1]
    rows = []
    for name, src in CASES.items():
        ours, _ = correct_text(src, Skew(xy=XY))
        theirs = gskewer(g, src)
        rows.append((name, *measure(name, src, ours), *measure(name, src, theirs)))
    print(f"xy skew factor {XY} ({math.degrees(math.atan(XY)):.3f} deg)\n")
    print("| case | u1skew max error (mm) | u1skew loop gap (mm) | gskewer max error (mm) | gskewer loop gap (mm) |")
    print("|---|---|---|---|---|")
    for r in rows:
        print(f"| {r[0]} | {r[1]:.3f} | {r[2]:.3f} | {r[3]:.3f} | {r[4]:.3f} |")


if __name__ == "__main__":
    main()
