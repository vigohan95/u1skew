xy skew factor 0.01 (0.573 deg)

| case | u1skew max error (mm) | u1skew loop gap (mm) | gskewer max error (mm) | gskewer loop gap (mm) |
|---|---|---|---|---|
| square, one axis per line | 0.000 | 0.000 | 1.000 | 1.000 |
| square, X and Y on every line | 0.000 | 0.000 | 0.000 | 0.000 |
| circle as G2 (arc fitting on) | 0.001 | 0.000 | 0.100 | 1.000 |
| G91 relative moves | 0.000 | 0.000 | 1.500 | 1.500 |

## Real U1 G-code (`bench/real_check.py`)

Sliced with Snapmaker Orca 2.4.1 (command line), stock `Snapmaker U1 (0.4 nozzle)` printer, `0.20mm Standard` process, Generic PLA ×2: a 30 mm cylinder on T0 and a 20 mm cube on T1, 4 mm tall, prime tower on. Default profile has arc fitting **off** but spiral Z-hop still writes `G3 Z.. I.. J.. P1` helices. xy = 0.005 (0.286°).

| file | lines | moves | arcs | tool changes | EXCLUDE_OBJECT lines | single-axis moves |
|---|---|---|---|---|---|---|
| stock profile (arc fitting off) | 23,491 | 20,027 | 39 | 24 | 82 | 1,501 |
| arc fitting on | 17,883 | 14,418 | 610 | 24 | 82 | 1,501 |

Every move end point and the position at every non-move line, compared with the skew transform of the original:

| file | tool | points checked | max error | points off by > 0.01 mm | other lines identical | extrusion total |
|---|---|---|---|---|---|---|
| arc fitting off | u1skew | 19,960 | 0.0005 mm | 0 | yes | unchanged |
| arc fitting off | gskewer | 19,960 | 0.087 mm | 88 | – | – |
| arc fitting on | u1skew | 14,351 | 0.0005 mm | 0 | yes | unchanged |
| arc fitting on | gskewer | 14,351 | 0.750 mm | 713 | – | – |

(0.0005 mm is the 3-decimal rounding of the output. gskewer also leaves every arc as a circle; that shape error is not in this table, see the synthetic table above.)

Post-processing hook: the slicer's command line does not run post-processing scripts (they run on export from the GUI), so the call was reproduced the way the slicer makes it — one command string, quoted interpreter path, temp file path with spaces appended: exit 0 and file rewritten; a second run on the same file exits 2 and leaves it alone.

Speed: 715,320 lines in 13.2 s.
