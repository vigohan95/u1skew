"""Skew-correct sliced G-code without touching printer firmware.

The transform matches Klipper's [skew_correction] module, so a factor
measured with Klipper's CALC_MEASURED_SKEW (or this package's
``u1skew calc``) means the same thing here:

    x' = x - y * xy - z * (xz - xy * yz)
    y' = y - z * yz
    z' = z

It is applied in machine space (G92 offsets are tracked separately for the
input stream and for the rewritten output stream), every XY-changing move
gets both X and Y written out, and G2/G3 arcs are flattened to G1 chords,
because an affine shear turns a circle into an ellipse that G2/G3 cannot
express.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

AXES = ("X", "Y", "Z")
MARKER = "; u1skew applied"

# One word of a G-code line: a letter followed by a number.
_WORD = re.compile(r"([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")


class SkewError(Exception):
    """The file cannot be corrected safely; nothing should be written."""


@dataclass(frozen=True)
class Skew:
    xy: float = 0.0
    xz: float = 0.0
    yz: float = 0.0

    def apply(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        return (
            x - y * self.xy - z * (self.xz - self.xy * self.yz),
            y - z * self.yz,
            z,
        )

    @property
    def is_identity(self) -> bool:
        return self.xy == 0 and self.xz == 0 and self.yz == 0


@dataclass
class Bounds:
    """Printable XY area in machine coordinates (mm)."""

    x_min: float = 0.0
    x_max: float = 270.0
    y_min: float = 0.0
    y_max: float = 270.0

    def contains(self, x: float, y: float, tol: float = 1e-6) -> bool:
        return (
            self.x_min - tol <= x <= self.x_max + tol
            and self.y_min - tol <= y <= self.y_max + tol
        )


@dataclass
class Report:
    lines_in: int = 0
    lines_changed: int = 0
    moves_xy_added: int = 0  # moves that lacked X or Y and had it written
    arcs_flattened: int = 0
    arc_segments: int = 0
    max_shift: float = 0.0  # largest XY displacement applied (mm)
    out_of_bounds: list = field(default_factory=list)  # (line_no, x, y)
    warnings: list = field(default_factory=list)


@dataclass
class _Cmd:
    letter: str
    number: str
    params: list  # [(letter, raw_value_str)]
    comment: str
    raw: str

    @property
    def code(self) -> str:
        return f"{self.letter}{int(float(self.number))}" if self.number else ""

    def get(self, letter: str):
        for k, v in self.params:
            if k == letter:
                return float(v)
        return None


def _parse(line: str) -> _Cmd | None:
    body, sep, comment = line.partition(";")
    comment = sep + comment
    body = body.strip()
    if not body:
        return None
    words = _WORD.findall(body)
    if not words:
        return None
    # Anything that is not a plain word list (macros such as
    # "SET_PRINT_STATS_INFO CURRENT_LAYER=1", "T0", "M117 text") is passed
    # through untouched; we only rewrite lines that are pure G0-G3 words.
    rebuilt = "".join(l + n for l, n in words)
    if rebuilt.upper() != re.sub(r"\s+", "", body).upper():
        return None
    letter, number = words[0][0].upper(), words[0][1]
    params = [(l.upper(), n) for l, n in words[1:]]
    return _Cmd(letter, number, params, comment, line)


def _fmt(v: float, digits: int) -> str:
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    return s


class Corrector:
    def __init__(
        self,
        skew: Skew,
        bounds: Bounds | None = None,
        chord_tol: float = 0.005,
        xyz_digits: int = 3,
        e_digits: int = 5,
    ):
        self.skew = skew
        self.bounds = bounds
        self.chord_tol = chord_tol
        self.xyz_digits = xyz_digits
        self.e_digits = e_digits

    # -- public -----------------------------------------------------------
    def run(self, lines: list[str]) -> tuple[list[str], Report]:
        for ln in lines[:200]:
            if ln.startswith(MARKER):
                raise SkewError("file already skew-corrected by u1skew; refusing to apply twice")
        rep = Report(lines_in=len(lines))
        self._reset_state()
        out = [
            f"{MARKER}: xy={self.skew.xy:.8g} xz={self.skew.xz:.8g} yz={self.skew.yz:.8g}\n"
        ]
        for no, line in enumerate(lines, 1):
            new = self._line(line, no, rep)
            if new != [line]:
                rep.lines_changed += 1
            out.extend(new)
        return out, rep

    # -- state ------------------------------------------------------------
    def _reset_state(self):
        self.abs_xyz = True
        self.abs_e = True
        # logical position of the input stream, per axis (None = unknown)
        self.pos = {a: None for a in AXES}
        self.e = 0.0
        # G92 offsets: machine = logical + offset
        self.in_off = {a: 0.0 for a in AXES}
        self.out_off = {a: 0.0 for a in AXES}
        # last emitted logical output position (rounded, as the printer sees it)
        self.out_pos = {a: None for a in AXES}
        self.plane = 17

    def _machine(self, logical: dict) -> dict:
        return {a: logical[a] + self.in_off[a] for a in AXES}

    def _skewed_out(self, logical: dict) -> dict:
        m = self._machine(logical)
        sx, sy, sz = self.skew.apply(m["X"], m["Y"], m["Z"] if m["Z"] is not None else 0.0)
        return {"X": sx - self.out_off["X"], "Y": sy - self.out_off["Y"], "Z": sz - self.out_off["Z"]}

    # -- per line ---------------------------------------------------------
    def _line(self, line: str, no: int, rep: Report) -> list[str]:
        cmd = _parse(line)
        if cmd is None or cmd.letter not in ("G", "M"):
            return [line]
        code = cmd.code
        if code == "G90":
            self.abs_xyz = True
            self.abs_e = True  # G90 also sets absolute E on Marlin/Klipper
            return [line]
        if code == "G91":
            self.abs_xyz = False
            self.abs_e = False
            return [line]
        if code == "M82":
            self.abs_e = True
            return [line]
        if code == "M83":
            self.abs_e = False
            return [line]
        if code in ("G17", "G18", "G19"):
            self.plane = int(code[1:])
            return [line]
        if code == "G28":
            homed = [a for a in AXES if cmd.get(a) is not None] or list(AXES)
            for a in homed:
                self.pos[a] = None
                self.out_pos[a] = None
                self.in_off[a] = 0.0
                self.out_off[a] = 0.0
            return [line]
        if code == "G92":
            return self._g92(cmd, line)
        if code in ("G0", "G1"):
            return self._g1(cmd, line, no, rep)
        if code in ("G2", "G3"):
            return self._arc(cmd, line, no, rep)
        return [line]

    def _g92(self, cmd: _Cmd, line: str) -> list[str]:
        if not cmd.params:
            for a in AXES:  # bare G92 zeroes every axis
                cmd.params.append((a, "0"))
            cmd.params.append(("E", "0"))
        for a in AXES:
            v = cmd.get(a)
            if v is None:
                continue
            if self.pos[a] is None:
                raise SkewError(f"G92 {a} with unknown position; cannot track offsets")
            machine_in = self.pos[a] + self.in_off[a]
            self.in_off[a] = machine_in - v
            self.pos[a] = v
            # The printer applies the same G92 to the rewritten stream, so its
            # offset is relative to where the output put the head.
            if self.out_pos[a] is not None:
                machine_out = self.out_pos[a] + self.out_off[a]
                self.out_off[a] = machine_out - v
                self.out_pos[a] = v
        if cmd.get("E") is not None:
            self.e = cmd.get("E")
        return [line]

    def _target(self, cmd: _Cmd) -> dict:
        tgt = {}
        for a in AXES:
            v = cmd.get(a)
            if v is None:
                tgt[a] = self.pos[a]
            elif self.abs_xyz:
                tgt[a] = v
            else:
                if self.pos[a] is None:
                    raise SkewError(f"relative {a} move from unknown position")
                tgt[a] = self.pos[a] + v
        return tgt

    def _emit_move(self, word: str, out: dict, params: list, comment: str,
                   force_xy: bool, rep: Report, no: int) -> str:
        """Build one G0/G1 line moving to output logical position ``out``."""
        written = {}
        for a in AXES:
            if out.get(a) is None:
                continue
            rounded = round(out[a], self.xyz_digits)
            changed = self.out_pos[a] is None or abs(rounded - self.out_pos[a]) > 0
            if a in params or (force_xy and a in ("X", "Y") and changed) or (a == "Z" and a in params):
                written[a] = rounded
        parts = [word]
        for a in AXES:
            if a in written:
                v = written[a] if self.abs_xyz else written[a] - (self.out_pos[a] or 0.0)
                parts.append(a + _fmt(v, self.xyz_digits))
        for k, v in params.items():
            if k in AXES:
                continue
            parts.append(k + v)
        for a, v in written.items():
            self.out_pos[a] = v
        if self.bounds is not None and self.out_pos["X"] is not None and self.out_pos["Y"] is not None:
            mx = self.out_pos["X"] + self.out_off["X"]
            my = self.out_pos["Y"] + self.out_off["Y"]
            if not self.bounds.contains(mx, my):
                rep.out_of_bounds.append((no, round(mx, 3), round(my, 3)))
        text = " ".join(parts)
        if comment:
            text += " " + comment.strip()
        return text + "\n"

    def _g1(self, cmd: _Cmd, line: str, no: int, rep: Report) -> list[str]:
        has_xyz = any(cmd.get(a) is not None for a in AXES)
        if cmd.get("E") is not None:
            e = cmd.get("E")
            self.e = e if self.abs_e else self.e + e
        if not has_xyz:
            return [line]
        tgt = self._target(cmd)
        if self.skew.is_identity:
            self.pos.update(tgt)
            for a in AXES:
                if tgt[a] is not None:
                    self.out_pos[a] = round(tgt[a] - self.out_off[a] + self.in_off[a], self.xyz_digits)
            return [line]
        need = ["X", "Y"] + (["Z"] if (self.skew.xz or self.skew.yz) else [])
        if any(tgt[a] is None for a in need):
            # Position not known yet (e.g. first move after G28 only names
            # one axis). Pass through; the next fully specified move fixes it.
            missing = [a for a in need if tgt[a] is None]
            rep.warnings.append(f"line {no}: position of {''.join(missing)} unknown, line left unchanged")
            self.pos.update({a: tgt[a] for a in AXES if tgt[a] is not None})
            for a in AXES:
                if cmd.get(a) is not None:
                    self.out_pos[a] = None  # output no longer tracks input here
            return [line]
        calc = dict(tgt)
        if calc["Z"] is None:  # only possible when the skew ignores Z
            calc["Z"] = 0.0
        out = self._skewed_out(calc)
        m = self._machine(calc)
        shift = math.hypot(out["X"] + self.out_off["X"] - m["X"], out["Y"] + self.out_off["Y"] - m["Y"])
        rep.max_shift = max(rep.max_shift, shift)
        params = {k: v for k, v in cmd.params}
        if not ("X" in params and "Y" in params):
            rep.moves_xy_added += 1
        if tgt["Z"] is None:
            out["Z"] = None
        self.pos.update({a: tgt[a] for a in AXES if tgt[a] is not None})
        word = cmd.letter + cmd.number
        return [self._emit_move(word, out, params, cmd.comment, True, rep, no)]

    def _arc(self, cmd: _Cmd, line: str, no: int, rep: Report) -> list[str]:
        if self.skew.is_identity:
            tgt = self._target(cmd)
            self.pos.update(tgt)
            if cmd.get("E") is not None:
                e = cmd.get("E")
                self.e = e if self.abs_e else self.e + e
            return [line]
        if self.plane != 17:
            raise SkewError(f"line {no}: arcs outside the XY plane (G{self.plane}) are not supported")
        if self.pos["X"] is None or self.pos["Y"] is None:
            raise SkewError(f"line {no}: arc from unknown position")
        x0, y0 = self.pos["X"], self.pos["Y"]
        z0 = self.pos["Z"] if self.pos["Z"] is not None else 0.0
        tgt = self._target(cmd)
        x1, y1 = tgt["X"], tgt["Y"]
        z1 = tgt["Z"] if tgt["Z"] is not None else z0
        cw = cmd.code == "G2"
        i, j, r = cmd.get("I"), cmd.get("J"), cmd.get("R")
        if i is not None or j is not None:
            cx, cy = x0 + (i or 0.0), y0 + (j or 0.0)
        elif r is not None:
            cx, cy = _center_from_r(x0, y0, x1, y1, r, cw)
        else:
            raise SkewError(f"line {no}: arc without I/J or R")
        radius = math.hypot(x0 - cx, y0 - cy)
        a0 = math.atan2(y0 - cy, x0 - cx)
        a1 = math.atan2(y1 - cy, x1 - cx)
        sweep = a1 - a0
        if cw:
            if sweep >= -1e-12:
                sweep -= 2 * math.pi
        else:
            if sweep <= 1e-12:
                sweep += 2 * math.pi
        # Full circle when start == end.
        if math.hypot(x1 - x0, y1 - y0) < 1e-9:
            sweep = -2 * math.pi if cw else 2 * math.pi
        # Segment count so the chord sagitta stays within chord_tol. The
        # skew stretches the arc by at most (1 + |xy|), so budget for that.
        stretch = 1.0 + abs(self.skew.xy)
        if radius * stretch <= self.chord_tol:
            n = 1
        else:
            max_step = 2 * math.acos(max(-1.0, 1 - self.chord_tol / (radius * stretch)))
            n = max(1, math.ceil(abs(sweep) / max_step))
        e_word = cmd.get("E")
        e_start = self.e
        if e_word is not None:
            e_total = (e_word - self.e) if self.abs_e else e_word
        else:
            e_total = 0.0
        params = {k: v for k, v in cmd.params if k not in ("X", "Y", "Z", "I", "J", "R", "E", "P")}
        rep.arcs_flattened += 1
        rep.arc_segments += n
        lines = [f"; u1skew: G{2 if cw else 3} flattened to {n} segments\n"]
        e_emitted = 0.0
        for k in range(1, n + 1):
            t = k / n
            if k == n:
                px, py, pz = x1, y1, z1
            else:
                ang = a0 + sweep * t
                px, py = cx + radius * math.cos(ang), cy + radius * math.sin(ang)
                pz = z0 + (z1 - z0) * t
            seg_params = dict(params) if k == 1 else {}
            if e_word is not None:
                if self.abs_e:
                    seg_params["E"] = _fmt(e_start + e_total * t, self.e_digits)
                else:
                    target = round(e_total * t, self.e_digits)
                    seg_params["E"] = _fmt(target - e_emitted, self.e_digits)
                    e_emitted = target
            pt = {"X": px, "Y": py, "Z": pz}
            out = self._skewed_out(pt)
            z_known = self.pos["Z"] is not None or tgt["Z"] is not None
            if not z_known:
                out["Z"] = None
            self.pos.update({"X": px, "Y": py})
            if z_known:
                self.pos["Z"] = pz
            # Z is written only when it changes along a helix.
            write = {"X": 1, "Y": 1}
            if z1 != z0:
                write["Z"] = 1
            seg = {a: "" for a in write}
            seg.update(seg_params)
            m = self._machine(pt)
            rep.max_shift = max(rep.max_shift, math.hypot(out["X"] + self.out_off["X"] - m["X"], out["Y"] + self.out_off["Y"] - m["Y"]))
            lines.append(self._emit_move("G1", out, seg, cmd.comment if k == n else "", True, rep, no))
        if e_word is not None:
            self.e = e_start + e_total
        return lines


def _center_from_r(x0, y0, x1, y1, r, cw):
    dx, dy = x1 - x0, y1 - y0
    d = math.hypot(dx, dy)
    if d == 0 or d > 2 * abs(r) + 1e-9:
        raise SkewError("arc R form with impossible radius")
    h = math.sqrt(max(0.0, r * r - (d / 2) ** 2))
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    # Perpendicular direction; sign picks the short or long arc.
    ux, uy = -dy / d, dx / d
    sign = 1.0 if (not cw) == (r > 0) else -1.0
    return mx + sign * h * ux, my + sign * h * uy


def correct_text(text: str, skew: Skew, **kw) -> tuple[str, Report]:
    lines = text.splitlines(keepends=True)
    out, rep = Corrector(skew, **kw).run(lines)
    return "".join(out), rep
