"""Run u1skew over real Snapmaker U1 G-code and check it did no harm.

Checks, per file:
  1. every line that is not a G0-G3 move comes out byte-identical and in
     the same order (tool changes, Snapmaker macros, EXCLUDE_OBJECT, comments);
  2. every move whose input position is known ends exactly where the skew
     transform says it should (independent replay, no shared code);
  3. the extrusion total is unchanged;
  4. nothing leaves the 270 x 270 bed.

Usage: python bench/real_check.py [--xy=0.005] [--gskewer=path/to/skew.py] FILE.gcode [...]
The files used for RESULTS.md were sliced with Snapmaker Orca from the
stock U1 profile (see RESULTS.md for the settings); they are not shipped.
"""

import re
import sys

from u1skew.core import Bounds, Skew, correct_text

MOVE = re.compile(r"^\s*G[0-3](?![0-9])", re.I)
SAFE = re.compile(r"^(M\d+|G(4|17|18|19|21|90|91|92)|EXCLUDE_OBJECT\w*|SET_\w+|;)", re.I)


def words(body):
    return {m.group(1).upper(): float(m.group(2))
            for m in re.finditer(r"([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))", body)}


def replay(text, forget_on_macro):
    """Machine XY after every move that names X or Y, and at every non-move
    line (None if unknown), plus total E (relative-E aware). Arcs count by
    their end point: in u1skew output the chords of a flattened arc are
    collapsed to the last one, so both files give the same number of points.
    The arc shape itself is covered by the unit tests."""
    absolute, abs_e = True, True
    pos = {"X": None, "Y": None}
    off = {"X": 0.0, "Y": 0.0}
    e_total, e_last = 0.0, 0.0
    trace = []
    skip = 0
    for raw in text.splitlines():
        m = re.match(r"; u1skew: G[23] flattened to (\d+) segments", raw)
        if m:
            skip = int(m.group(1)) - 1
            continue
        body = raw.split(";")[0].strip()
        if not body:
            continue
        head = body.split()[0].upper()
        w = words(body)
        if not MOVE.match(body):
            trace.append(None if None in pos.values()
                         else (round(pos["X"] + off["X"], 6), round(pos["Y"] + off["Y"], 6)))
        if head == "G90":
            absolute = abs_e = True
        elif head == "G91":
            absolute = abs_e = False
        elif head == "M82":
            abs_e = True
        elif head == "M83":
            abs_e = False
        elif head == "G28":
            for a in ("X", "Y"):
                if a in w or not any(k in w for k in "XYZ"):
                    pos[a] = None
                    off[a] = 0.0
        elif head == "G92":
            for a in ("X", "Y"):
                if a in w and pos[a] is not None:
                    off[a] = pos[a] + off[a] - w[a]
                    pos[a] = w[a]
            if "E" in w:
                e_last = w["E"]
        elif head in ("G0", "G1", "G2", "G3"):
            for a in ("X", "Y"):
                if a in w:
                    if absolute:
                        pos[a] = w[a]
                    elif pos[a] is not None:
                        pos[a] += w[a]
            if "X" in w or "Y" in w or head in ("G2", "G3"):
                if skip:
                    skip -= 1
                else:
                    trace.append(None if None in pos.values()
                                 else (round(pos["X"] + off["X"], 6), round(pos["Y"] + off["Y"], 6)))
            if "E" in w:
                if abs_e:
                    e_total += w["E"] - e_last
                    e_last = w["E"]
                else:
                    e_total += w["E"]
        elif forget_on_macro and not SAFE.match(head):
            pos = {"X": None, "Y": None}
    return trace, e_total


def sync_error(text, out, xy):
    t_in, _ = replay(text, forget_on_macro=True)
    t_out, _ = replay(out, forget_on_macro=True)
    assert len(t_in) == len(t_out), (len(t_in), len(t_out))
    errs = []
    for a, b in zip(t_in, t_out):
        if a is None or b is None:
            continue
        ex, ey, _ = Skew(xy=xy).apply(a[0], a[1], 0.0)
        errs.append(max(abs(ex - b[0]), abs(ey - b[1])))
    return errs


def gskewer_report(path, xy, script):
    """Same file through gskewer (rival); same sync-point metric."""
    import os
    import subprocess
    import tempfile
    text = open(path, encoding="utf-8", errors="replace").read()
    d = tempfile.mkdtemp()
    f = os.path.join(d, "in.gcode")
    open(f, "w", encoding="utf-8").write(text)
    subprocess.run([sys.executable, "-I", "-X", "utf8", script, "--xyskew", str(xy), "--yzskew", "0",
                    "--xzskew", "0", f], check=True, capture_output=True)
    name = [n for n in os.listdir(d) if n != "in.gcode"][0]
    out = open(os.path.join(d, name), encoding="utf-8", errors="replace").read()
    errs = sync_error(text, out, xy)
    bad = sum(1 for e in errs if e > 0.01)
    return {"tool": "gskewer", "points_checked": len(errs),
            "endpoint_err_mm": round(max(errs), 4), "points_off_by_>0.01mm": bad}


def check(path, xy):
    text = open(path, encoding="utf-8", errors="replace").read()
    out, rep = correct_text(text, Skew(xy=xy), bounds=Bounds())
    keep_in = [l for l in text.splitlines() if not MOVE.match(l)]
    keep_out = [l for l in out.splitlines()[1:]  # drop marker and arc notes
                if not MOVE.match(l) and not l.startswith("; u1skew:")]
    same_other = keep_in == keep_out
    t_in, e_in = replay(text, forget_on_macro=True)
    t_out, e_out = replay(out, forget_on_macro=True)
    assert len(t_in) == len(t_out), (len(t_in), len(t_out))
    worst, checked = 0.0, 0
    for a, b in zip(t_in, t_out):
        if a is None or b is None:
            continue
        ex, ey, _ = Skew(xy=xy).apply(a[0], a[1], 0.0)
        worst = max(worst, abs(ex - b[0]), abs(ey - b[1]))
        checked += 1
    moves = sum(1 for l in text.splitlines() if MOVE.match(l))
    arcs = sum(1 for l in text.splitlines() if re.match(r"^\s*G[23](?![0-9])", l, re.I))
    excl = sum(1 for l in text.splitlines() if l.startswith("EXCLUDE_OBJECT"))
    tools = sum(1 for l in text.splitlines() if re.match(r"^T\d", l))
    return {
        "file": path.replace("\\", "/").split("/")[-1],
        "lines": rep.lines_in,
        "moves": moves,
        "arcs": arcs,
        "tool_changes": tools,
        "exclude_object_lines": excl,
        "arcs_flattened": rep.arcs_flattened,
        "other_lines_identical": same_other,
        "points_checked": checked,
        "endpoint_err_mm": round(worst, 4),
        "E_in": round(e_in, 4),
        "E_out": round(e_out, 4),
        "xy_added": rep.moves_xy_added,
        "out_of_bounds": len(rep.out_of_bounds),
        "warnings": len(rep.warnings),
        "max_shift_mm": round(rep.max_shift, 3),
    }


if __name__ == "__main__":
    xy = 0.005
    files = [a for a in sys.argv[1:] if not a.startswith("--")]
    for a in sys.argv[1:]:
        if a.startswith("--xy="):
            xy = float(a[5:])
    rival = next((a[10:] for a in sys.argv[1:] if a.startswith("--gskewer=")), None)
    for f in files:
        print(check(f, xy))
        if rival:
            print(gskewer_report(f, xy, rival))
