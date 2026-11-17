# u1skew

Square prints on a Snapmaker U1 **without Advanced Mode and without touching the firmware**.

u1skew is a slicer post-processing script. It rewrites the X/Y(/Z) coordinates of the G-code that Snapmaker Orca just sliced, using the same transform as Klipper's `[skew_correction]`, so a part printed out of square comes out square. Stock firmware, stock `printer.cfg`, no warranty pop-up.

[中文说明](README.zh-CN.md)

## Why

If your U1 prints a calibration square with unequal diagonals, the frame is slightly out of square. The usual fix is Klipper's `[skew_correction]`, which on the U1 means switching on Advanced Mode and editing `printer.cfg`. Several owners on the Snapmaker forum would rather not do that. u1skew applies the same correction on the slicer side instead.

Generic G-code skew scripts exist, but in our tests they get three things wrong that a sliced U1 file hits all the time (numbers below, from `bench/compare.py`):

- a move that names only `Y` (or only `X`) is left as is, so a square no longer closes;
- `G2`/`G3` arcs (Orca's *arc fitting*) are passed through, but a sheared circle is an ellipse, which an arc command cannot draw;
- `G91` relative moves and `G92` offsets are not tracked.

| skew xy = 0.01 (0.573°) | u1skew max error | u1skew loop gap | gskewer max error | gskewer loop gap |
|---|---|---|---|---|
| square, one axis per line | 0.000 mm | 0.000 mm | 1.000 mm | 1.000 mm |
| square, X and Y on every line | 0.000 mm | 0.000 mm | 0.000 mm | 0.000 mm |
| circle as G2 (arc fitting on) | 0.001 mm | 0.000 mm | 0.100 mm | 1.000 mm |
| G91 relative moves | 0.000 mm | 0.000 mm | 1.500 mm | 1.500 mm |

"Error" is the distance between where a move ends and where the skew transform says it should end; "loop gap" is how far a closed shape fails to close.

## What it does to a file

- **Moves (`G0`/`G1`)**: transformed in machine coordinates. Whenever X/Y change, both are written, so single-axis moves stay correct.
- **Arcs (`G2`/`G3`, I/J or R form)**: split into `G1` chords within 0.005 mm of the true (sheared) curve; extrusion is shared out by length. Works with absolute (`M82`) and relative (`M83`) E.
- **`G90`/`G91`/`G92`/`G28`**: tracked, separately for the input and the rewritten output.
- **Tool changes and Snapmaker macros** (`T0`–`T3`, `MOVE_TO_DISCARD_FILAMENT_POSITION`, `TIMELAPSE_TAKE_FRAME`, the wipe/prime routines…): passed through byte for byte. The firmware may park or dock the head inside them, so u1skew forgets the XY position after any command it does not know to be motion-free, and leaves the next move alone until one names both X and Y (Orca always emits such a move after a tool change).
- **`EXCLUDE_OBJECT_*`, `SET_*`, temperature/fan M-codes, comments**: untouched, position kept.
- **Bounds**: if any corrected move would leave the 270 × 270 mm bed, the file is **not** written and the slicer shows the error. Move the part toward the centre or pass `--allow-out-of-bounds`.
- **Twice**: a marker comment is added to the top; running it again on the same file is refused.

## Install

Python 3.8 or newer.

```
pip install git+https://github.com/vigohan95/u1skew
```

## 1. Measure

Print a skew test square (Califlower, or the square from the Klipper skew docs), then measure with calipers:

- `AC` – diagonal, front-left to back-right corner
- `BD` – diagonal, back-left to front-right corner
- `AD` – left edge, front-left to back-left corner

```
u1skew calc --ac 140.4 --bd 142.5 --ad 99.8
```

It prints the factor (same formula as Klipper's `CALC_MEASURED_SKEW`, so a factor you already have from Klipper works as is). If you measured an angle instead, `u1skew calc --angle 0.2`.

## 2. Add it to Snapmaker Orca

*Process → Others → Post-processing Scripts*:

```
python -m u1skew fix --xy 0.0035
```

On Windows you may need the full path to `python.exe`. Orca appends the path of the temporary G-code file and u1skew edits it in place. Slice, and the log line tells you how many moves were changed and the largest shift applied.

Without the slicer: `u1skew fix --xy 0.0035 part.gcode -o part.square.gcode`.

## Tool offsets

The U1 applies each toolhead's XY offset in firmware, after u1skew has corrected the coordinates. That offset itself is not sheared, which leaves an error of `|Δy offset| × xy` in X (and `|Δz| × yz` for XZ/YZ factors). For a 2 mm Y offset and xy = 0.0035 that is 0.007 mm — below what a nozzle can show.

## Checked on

- 28 unit tests with an independent printer-replay model as the oracle (every move end point, every arc vertex on the sheared ellipse within ±0.002 mm, no drift over 3,000 relative steps, tool changes and macros, Orca's spiral Z-hop helix, measurement formula recovers a constructed parallelogram).
- Real U1 files sliced with Snapmaker Orca 2.4.1 and the stock U1 profile (two tools, prime tower, 24 tool changes, `EXCLUDE_OBJECT`, arc fitting off and on): all 19,960 / 14,351 move end points within 0.0005 mm of the transform, every non-move line identical and in order, extrusion total unchanged. The same files through gskewer: 88 / 713 points off by more than 0.01 mm, worst 0.087 / 0.750 mm. Details in `bench/RESULTS.md`.
- The default U1 profile has arc fitting off, but Orca's spiral Z-hop still writes `G3 … P1` helices on every travel lift; u1skew flattens them too.
- Speed: about 715,000 lines in 13 s.

**Not yet checked on a real printer.** If you own a U1 and try it, please open an issue with your before/after diagonals.

## License

MIT
