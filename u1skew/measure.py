"""Turn print measurements into skew factors.

Same formula as Klipper's skew_correction.calc_skew_factor, so numbers
measured for a Klipper [skew_correction] section can be reused unchanged.

Print a calibration square (Califlower, or Klipper's skew_xy test square),
then measure with calipers:
  AC  - diagonal from the front-left corner to the back-right corner
  BD  - diagonal from the back-left corner to the front-right corner
  AD  - the left edge (front-left to back-left)
"""

from __future__ import annotations

import math


def skew_factor(ac: float, bd: float, ad: float) -> float:
    if min(ac, bd, ad) <= 0:
        raise ValueError("measurements must be positive")
    side = math.sqrt(2 * ac * ac + 2 * bd * bd - 4 * ad * ad) / 2.0
    cos_arg = (ac * ac - side * side - ad * ad) / (2 * side * ad)
    if not -1.0 <= cos_arg <= 1.0:
        raise ValueError("measurements are inconsistent (not a parallelogram); re-measure")
    return math.tan(math.pi / 2 - math.acos(cos_arg))


def factor_from_angle(deg: float) -> float:
    """Factor for an axis pair that measures ``90 + deg`` degrees."""
    return math.tan(math.radians(deg))


def angle_from_factor(f: float) -> float:
    return math.degrees(math.atan(f))


def factor_from_offset(err_mm: float, length_mm: float) -> float:
    """Califlower-style: X drifts ``err_mm`` over ``length_mm`` of Y travel."""
    if length_mm <= 0:
        raise ValueError("length must be positive")
    return err_mm / length_mm
