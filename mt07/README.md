# Yamaha MT-07 (2025) — 3D-printable scale model

A stylized 1:18 model of the 2025 Yamaha MT-07, generated in Python with
[manifold3d](https://github.com/elalish/manifold). Every STL is a watertight solid.

![preview](preview.png)

You only need **one** of the three versions below. The mirrors are an optional add-on for any of them.

## 1. One piece (`stl/mt07_full.stl`)

124 × 47 × 63 mm on a display base with raised “MT-07” lettering.
Print upright with tree/organic supports.

## 2. Two halves (`stl/mt07_half_left.stl`, `stl/mt07_half_right.stl`)

The whole bike, wheels included, split down the middle. Print cut face down with **no supports**.
Push 3.5 mm pieces of 1.75 mm filament into the two 2.1 mm holes to line the halves up, then glue them.

## 3. Kit with separate wheels (`stl/kit/`)

The cleanest result, and the wheels can be a different colour from the body.

| File | Contents |
|---|---|
| `body_left.stl`, `body_right.stl` | Body halves without wheels. Print cut face down and glue them with filament pins, as above. |
| `wheels.stl` | Front and rear wheels, each split into two halves (4 parts). Print cut face down. |
| `discs_and_sprocket.stl` | 2 front discs, 1 rear disc and 1 rear sprocket. Print flat. |

Assembly: glue each wheel's two halves together, using a piece of filament through the hub to line them up.
The axles are 1.75 mm filament through the 2.1 mm holes:

- **Front (about 17 mm of filament):** fork leg → disc → wheel → disc → fork leg. Each disc's collar faces outward.
- **Rear (about 18 mm of filament):** swingarm → sprocket (left) → wheel → disc (right) → swingarm.

Trim the filament flush and add a drop of glue at the ends.

## 4. Multi-colour sections (`stl/colour/`)

`paint.stl`, `dark.stl`, `metal.stl` and `light.stl` share one coordinate system and don't overlap.
Import all four together as **one object with multiple parts** in PrusaSlicer, OrcaSlicer or Bambu Studio,
then give each part a filament. Suggested colours:

- `paint`: Icon Blue (tank side panels, small accent under the seat step, wheels, shock spring)
- `dark`: matte black (tank top cover, side covers, frame, engine, swingarm, tail, seat, tyres, base)
- `metal`: silver (handlebar, discs, exhaust, fork inner tubes, filler cap, emblems, lettering)
- `light`: white (headlight, tail light, indicators, reflectors, number plate)

## Mirrors (`stl/mt07_mirrors.stl`)

Print flat. Each mirror has a 1.4 mm peg that fits the 1.7 mm hole on top of the mount on each side of the
handlebar. Every version has these holes. The mirrors are delicate, so add them last.

## Print settings

0.4 mm nozzle, 0.12 mm layers recommended (the discs and mirrors are about 1 mm thick), 3 walls, 15 % infill,
PLA or PETG.

## Rebuild or rescale

```bash
pip install manifold3d numpy matplotlib
python3 mt07.py --scale 12 --preview   # 1:12 (about 177 mm long)
```

All geometry uses Yamaha's published 2025 figures: 2065 × 780 × 1110 mm overall, 1395 mm wheelbase,
805 mm seat height, 140 mm ground clearance, 24.3° rake with 94 mm trail, 120/70-17 and 180/55-17 tyres,
twin 298 mm front discs and a 245 mm rear disc.

2025-specific details modelled: the LED face with a central light flanked by two eye-shaped DRLs and a
small "forehead" light above; the narrower 14 L tank cover (slimmest where it meets the two-piece seat)
with four mesh acoustic vents and no side intake vents behind the forks; side covers following MT-09 lines;
the 41 mm upside-down fork with radially mounted 4-piston calipers; the 5" TFT under a short visor; the
wider, lower handlebar; thicker backbone frame tubes; the new asymmetric steel swingarm; the 2-into-1
exhaust with its catalytic converter near the headers; the slim vertical LED tail light; and the factory
rear fender carrying the plate. The spoke pattern of the new SpinForged wheels isn't published, so the
wheels are approximate. Colours, the side covers, the tank panel and the long plate bracket were checked against a photo of a 2025 Icon Blue bike. Pin holes, axle holes and mirror pegs
are sized in printed millimetres, so they stay correct at any scale. Small parts such as
brake discs, levers and footpegs are thickened so they survive printing at 1:18.
