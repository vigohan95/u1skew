import math
import re

import pytest

from u1skew.core import Bounds, Skew, SkewError, correct_text
from u1skew.measure import factor_from_angle, skew_factor


# --- an independent, minimal printer model used as the oracle -------------
def simulate(text):
    """Return the machine XYZ (and absolute E) after every motion line.

    Supports what the tests emit: G90/G91, M82/M83, G92, G0/G1 and G2/G3
    (arcs are recorded only by their end point).
    """
    absolute, abs_e = True, True
    pos = {"X": 0.0, "Y": 0.0, "Z": 0.0}
    off = {"X": 0.0, "Y": 0.0, "Z": 0.0}
    e = 0.0
    trace = []
    for raw in text.splitlines():
        line = raw.split(";")[0].strip()
        if not line:
            continue
        words = dict((w[0].upper(), float(w[1:])) for w in line.split()[1:])
        code = line.split()[0].upper()
        if code == "G90":
            absolute = abs_e = True
        elif code == "G91":
            absolute = abs_e = False
        elif code == "M82":
            abs_e = True
        elif code == "M83":
            abs_e = False
        elif code == "G92":
            for a in "XYZ":
                if a in words:
                    off[a] = pos[a] + off[a] - words[a]
                    pos[a] = words[a]
            if "E" in words:
                e = words["E"]
        elif code in ("G0", "G1", "G2", "G3"):
            for a in "XYZ":
                if a in words:
                    pos[a] = words[a] if absolute else pos[a] + words[a]
            if "E" in words:
                e = words["E"] if abs_e else e + words["E"]
            trace.append((pos["X"] + off["X"], pos["Y"] + off["Y"], pos["Z"] + off["Z"], e))
    return trace


def skewed(p, s):
    return s.apply(p[0], p[1], p[2])


SQUARE = """G90
M83
G1 X0 Y0 Z0.2 F3000
G1 X100 E5
G1 Y100 E5
G1 X0 E5
G1 Y0 E5
"""


def test_y_only_moves_get_x_written_and_square_closes():
    s = Skew(xy=0.01)
    out, rep = correct_text(SQUARE, s)
    before, after = simulate(SQUARE), simulate(out)
    assert len(before) == len(after)
    for b, a in zip(before, after):
        exp = skewed(b, s)
        assert a[0] == pytest.approx(exp[0], abs=1e-3)
        assert a[1] == pytest.approx(exp[1], abs=1e-3)
    # closes: last point equals first
    assert after[-1][:2] == pytest.approx(after[0][:2], abs=1e-9)
    # the "G1 Y100" line must now carry X99
    assert "G1 X99 Y100 E5" in out
    assert rep.moves_xy_added == 4  # every square side names only one axis


def test_extrusion_and_feedrate_preserved():
    out, _ = correct_text(SQUARE, Skew(xy=0.01))
    assert re.findall(r"E[-\d.]+", out) == re.findall(r"E[-\d.]+", SQUARE)
    assert "F3000" in out


def test_identity_is_noop_apart_from_marker():
    out, rep = correct_text(SQUARE, Skew())
    assert out.split("\n", 1)[1] == SQUARE
    assert rep.lines_changed == 0


def test_refuses_to_apply_twice():
    out, _ = correct_text(SQUARE, Skew(xy=0.01))
    with pytest.raises(SkewError):
        correct_text(out, Skew(xy=0.01))


def test_relative_xy_mode():
    src = "G90\nG1 X10 Y10\nG91\nG1 X20\nG1 Y30\nG1 X-5 Y-5\nG90\nG1 X50 Y50\n"
    s = Skew(xy=-0.02)
    out, _ = correct_text(src, s)
    for b, a in zip(simulate(src), simulate(out)):
        exp = skewed(b, s)
        assert a[:2] == pytest.approx(exp[:2], abs=2e-3)


def test_relative_mode_does_not_accumulate_rounding_drift():
    lines = ["G90", "G1 X10 Y10", "G91"] + ["G1 Y0.0333"] * 3000 + ["G90"]
    src = "\n".join(lines) + "\n"
    s = Skew(xy=0.0123)
    out, _ = correct_text(src, s)
    a, b = simulate(out)[-1], skewed(simulate(src)[-1], s)
    assert a[0] == pytest.approx(b[0], abs=1e-3)
    assert a[1] == pytest.approx(b[1], abs=1e-3)


def test_g92_offsets_are_tracked():
    src = "G90\nG1 X100 Y100\nG92 X0 Y0\nG1 X10 Y10\nG1 Y50\nG92 X100 Y100\nG1 X120\n"
    s = Skew(xy=0.01)
    out, _ = correct_text(src, s)
    for b, a in zip(simulate(src), simulate(out)):
        exp = skewed(b, s)
        assert a[:2] == pytest.approx(exp[:2], abs=1e-3)


def test_g10_and_g17_are_not_mistaken_for_moves():
    src = "G90\nG1 X10 Y10\nG10\nG17\nG11\nG1 Y20\n"
    out, _ = correct_text(src, Skew(xy=0.01))
    assert "\nG10\n" in out and "\nG17\n" in out and "\nG11\n" in out


def test_macros_and_comments_pass_through():
    src = ("G90\nG1 X10 Y10 ; travel\nSET_PRINT_STATS_INFO CURRENT_LAYER=2\n"
           "T1\nM117 hello X5\n;LAYER_CHANGE\nG1 Y20 E1.5 ; inner wall\n")
    out, _ = correct_text(src, Skew(xy=0.01))
    for keep in ["SET_PRINT_STATS_INFO CURRENT_LAYER=2", "T1\n", "M117 hello X5", ";LAYER_CHANGE"]:
        assert keep in out
    assert "G1 X9.8 Y20 E1.5 ; inner wall" in out


def _arc_points(out):
    pts = []
    for line in out.splitlines():
        m = re.match(r"G1 X([-\d.]+) Y([-\d.]+)", line)
        if m:
            pts.append((float(m.group(1)), float(m.group(2))))
    return pts


@pytest.mark.parametrize("code,ij,end", [
    ("G2", "I10 J0", "X120 Y100"),      # half circle, clockwise over the top
    ("G3", "I10 J0", "X120 Y100"),      # half circle underneath
    ("G3", "I10 J0", "X100 Y100"),      # full circle
    ("G2", "R10", "X110 Y110"),         # quarter, R form
])
def test_arcs_are_flattened_onto_the_skewed_curve(code, ij, end):
    s = Skew(xy=0.02)
    src = f"G90\nM83\nG1 X100 Y100 Z0.3\n{code} {end} {ij} E3\n"
    out, rep = correct_text(src, s, chord_tol=0.002)
    assert rep.arcs_flattened == 1
    assert not re.search(r"^G[23] ", out, re.M)
    pts = _arc_points(out)[1:]  # drop the lead-in move
    # every vertex lies on the skewed image of the circle
    # all four cases share the centre (110, 100); for the R case that is
    # the short clockwise arc from (100,100) to (110,110)
    cx, cy = 110.0, 100.0
    inv = lambda x, y: (x + y * s.xy, y)  # undo the shear
    for x, y in pts:
        ux, uy = inv(x, y)
        assert math.hypot(ux - cx, uy - cy) == pytest.approx(10.0, abs=2e-3)
    # end point is exact
    ex, ey = (float(v[1:]) for v in end.split())
    assert pts[-1] == pytest.approx(s.apply(ex, ey, 0.3)[:2], abs=1e-3)
    # extrusion is preserved in total
    es = [float(v) for v in re.findall(r"E([-\d.]+)", out)]
    assert sum(es) == pytest.approx(3.0, abs=1e-5)


def test_arc_direction_matters():
    s = Skew(xy=0.0)
    s2 = Skew(xy=1e-9)
    src_cw = "G90\nG1 X100 Y100\nG2 X120 Y100 I10 J0\n"
    src_ccw = "G90\nG1 X100 Y100\nG3 X120 Y100 I10 J0\n"
    up = [p[1] for p in _arc_points(correct_text(src_cw, s2)[0])[1:-1]]
    down = [p[1] for p in _arc_points(correct_text(src_ccw, s2)[0])[1:-1]]
    assert min(up) > 100 and max(down) < 100
    assert correct_text(src_cw, s)[1].arcs_flattened == 0  # identity leaves arcs alone


def test_arc_absolute_e():
    src = "G90\nM82\nG92 E10\nG1 X100 Y100\nG2 X120 Y100 I10 J0 E14\nG1 X130 E15\n"
    out, _ = correct_text(src, Skew(xy=0.01))
    trace = simulate(out)
    assert trace[-2][3] == pytest.approx(14.0, abs=1e-6)
    assert trace[-1][3] == pytest.approx(15.0, abs=1e-6)
    es = [float(v) for v in re.findall(r"E([-\d.]+)", out.split("flattened")[1])]
    assert es == sorted(es)  # monotonic, no back-extrusion


def test_xz_yz_like_klipper():
    s = Skew(xy=0.01, xz=0.002, yz=-0.003)
    src = "G90\nG1 X10 Y10 Z0.2\nG1 Z50\nG1 X100 Y100 Z100\n"
    out, _ = correct_text(src, s)
    for b, a in zip(simulate(src), simulate(out)):
        assert a[:3] == pytest.approx(skewed(b, s), abs=1e-3)


def test_bounds_violations_reported():
    src = "G90\nG1 X1 Y260\n"
    _, rep = correct_text(src, Skew(xy=0.01), bounds=Bounds())
    assert rep.out_of_bounds and rep.out_of_bounds[0][0] == 2


def test_unknown_position_after_home_is_left_alone():
    src = "G28\nG1 Z5\nG1 Y20\nG1 X10 Y20\nG1 Y30\n"
    out, rep = correct_text(src, Skew(xy=0.01))
    assert "\nG1 Y20\n" in out  # X unknown here: untouched, warned
    assert rep.warnings
    assert "G1 X9.8 Y20" in out and "G1 X9.7 Y30" in out


# --- measurement helpers ---------------------------------------------------
@pytest.mark.parametrize("f", [0.0, 0.004, -0.0075, 0.02])
def test_skew_factor_recovers_printed_parallelogram(f):
    # An uncorrected printer draws commanded (x, y) at (x + f*y, y).
    L = 100.0
    A, C = (0.0, 0.0), (L + f * L, L)
    B, D = (L, 0.0), (f * L, L)
    ac = math.dist(A, C)
    bd = math.dist(B, D)
    ad = math.dist(A, D)
    assert skew_factor(ac, bd, ad) == pytest.approx(f, abs=1e-9)
    # and correcting with that factor squares the part
    s = Skew(xy=f)
    cx, cy, _ = s.apply(L, L, 0)
    printed = (cx + f * cy, cy)
    assert printed == pytest.approx((L, L), abs=1e-9)


def test_factor_from_angle():
    assert factor_from_angle(0.5) == pytest.approx(math.tan(math.radians(0.5)))
