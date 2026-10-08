#!/usr/bin/env python3
"""
Stylized 3D-printable scale model of a 2025 Yamaha MT-07.

All geometry is authored in real-world millimetres (rear axle at x=0,
ground at z=0, +x toward the front, +y to the rider's right) and scaled
down at export time. Real dimensions used as reference:
  wheelbase 1395 mm, length ~2065 mm, seat height 805 mm,
  120/70-17 front and 180/55-17 rear tyres, 24.8 deg rake.

Small parts are deliberately thickened so that nothing printed is
thinner than MIN_FEATURE_MM at the chosen scale.

Outputs (in ./stl):
  mt07_full.stl        one piece on a display base, print upright with supports
  mt07_half_left.stl   left half, flat side down, no supports needed
  mt07_half_right.stl  right half, flat side down, no supports needed
The halves have two 2.1 mm holes each so you can glue them together with
short pieces of 1.75 mm filament as alignment pins.

Usage: python3 mt07.py [--scale 18] [--out stl] [--preview]
"""
import argparse
import math
import os

import numpy as np
from manifold3d import CrossSection, Manifold, OpType

SEG = 64

# --------------------------------------------------------------- helpers


def sphere(p, r, seg=24):
    return Manifold.sphere(r, seg).translate(tuple(p))


def hull(*parts):
    return Manifold.batch_hull(list(parts))


def blob(points, r, seg=16):
    """Convex hull of spheres of radius r at each point."""
    return Manifold.batch_hull([sphere(p, r, seg) for p in points])


def capsule(a, b, r, seg=24):
    return hull(sphere(a, r, seg), sphere(b, r, seg))


def chain(points, r, seg=24):
    return union([capsule(points[i], points[i + 1], r, seg)
                  for i in range(len(points) - 1)])


def cyl_between(a, b, r, seg=SEG):
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = b - a
    h = float(np.linalg.norm(d))
    d /= h
    ref = np.array([1.0, 0, 0]) if abs(d[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(ref, d)
    u /= np.linalg.norm(u)
    v = np.cross(d, u)
    mat = np.column_stack([u, v, d, a])
    return Manifold.cylinder(h, r, r, seg).transform(mat)


def ycyl(center, r, width, seg=SEG):
    """Cylinder whose axis is the y axis, centred on `center`."""
    x, y, z = center
    return cyl_between((x, y - width / 2, z), (x, y + width / 2, z), r, seg)


def box(x0, x1, y0, y1, z0, z1):
    return Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))


def union(parts):
    parts = [p for p in parts if p is not None]
    return Manifold.batch_boolean(parts, OpType.Add)


def mirror_y(m):
    return m + m.mirror((0, 1, 0))


def to_xz(m):
    """Map a revolve()-made part (axis = z) so that its axis becomes y."""
    return m.rotate((-90, 0, 0))


# --------------------------------------------------------------- layout

REAR_AXLE = np.array([0.0, 0.0, 315.0])
FRONT_AXLE = np.array([1395.0, 0.0, 300.0])
RAKE = math.radians(24.8)
FORK_DIR = np.array([-math.sin(RAKE), 0.0, math.cos(RAKE)])
SWINGARM_PIVOT = np.array([575.0, 0.0, 440.0])


def fork_point(length, y=0.0):
    p = FRONT_AXLE + FORK_DIR * length
    return np.array([p[0], y, p[2]])


# --------------------------------------------------------------- parts


def wheel(center, outer_r, tyre_w, disc_r, disc_y, rim_spokes=10):
    rim_r = 216.0  # 17 inch bead seat
    # tyre: round crown hulled onto a flat bead section, revolved
    crown = CrossSection.circle(tyre_w / 2, 48).translate((outer_r - tyre_w / 2, 0))
    bead = CrossSection.square((10, tyre_w * 0.82), center=True).translate((rim_r + 5, 0))
    tyre = to_xz(CrossSection.batch_hull([crown, bead]).revolve(SEG * 2))

    # rim with Y-spoke pockets cut from both faces, leaving a central web
    rim_w = tyre_w * 0.78
    rim = to_xz(Manifold.cylinder(rim_w, rim_r + 8, rim_r + 8, SEG * 2, center=True))
    ring = (CrossSection.circle(rim_r - 14, SEG * 2)
            - CrossSection.circle(62, SEG))
    spokes = []
    for i in range(rim_spokes // 2):
        a = 360.0 / (rim_spokes // 2) * i
        stem = CrossSection.square((90, 26), center=True).translate((95, 0))
        for s in (-1, 1):  # the two branches of each Y
            arm = (CrossSection.square((110, 24), center=True)
                   .translate((55, 0)).rotate(s * 14).translate((130, 0)))
            spokes.append(arm.rotate(a))
        spokes.append(stem.rotate(a))
    pocket = ring - CrossSection.batch_boolean(spokes, OpType.Add)
    depth = rim_w * 0.30
    cut = to_xz(pocket.extrude(depth + 1))
    cut = cut.translate((0, rim_w / 2 - depth - cut.bounding_box()[1], 0))
    rim = rim - cut - cut.mirror((0, 1, 0))
    hub = ycyl((0, 0, 0), 55, tyre_w * 0.95)

    parts = [tyre, rim, hub]
    # brake disc(s) and carriers
    for dy in disc_y:
        disc = ycyl((0, dy, 0), disc_r, 12)
        holes = [ycyl((math.cos(t) * (disc_r - 30), dy, math.sin(t) * (disc_r - 30)), 9, 20, 12)
                 for t in np.linspace(0, 2 * math.pi, 12, endpoint=False)]
        disc = disc - union(holes)
        parts += [disc, ycyl((0, dy * 0.75, 0), 70, abs(dy) * 0.6 + 10)]
    return union(parts).translate(tuple(center))


def front_end():
    parts = []
    lower_clamp_len, top_clamp_len = 560.0, 720.0
    for y in (-105.0, 105.0):
        # upside-down fork: thin inner tube at the bottom, fat outer up top
        parts.append(cyl_between(fork_point(-10, y), fork_point(330, y), 22, 32))
        parts.append(cyl_between(fork_point(300, y), fork_point(top_clamp_len + 20, y), 30, 32))
        parts.append(blob([fork_point(-25, y), fork_point(40, y)], 26))  # axle lug
        # radial caliper behind the disc
        dy = 78.0 * np.sign(y)
        c = FRONT_AXLE + np.array([-108, 0, 92])
        parts.append(blob([(c[0] - 35, dy, c[2] - 40), (c[0] + 30, dy, c[2] + 45)], 24))
        parts.append(capsule((c[0], dy, c[2]), fork_point(120, y), 16))
    parts.append(capsule(fork_point(-10, -105), fork_point(-10, 105), 14))  # axle

    # triple clamps and steering head
    for L, h in ((lower_clamp_len, 30), (top_clamp_len, 26)):
        a, b = fork_point(L, -140), fork_point(L, 140)
        parts.append(blob([a, b, a + np.array([-60, 0, 0]), b + np.array([-60, 0, 0])], h / 2 + 4))
    head_off = np.array([-55.0, 0, 0])
    parts.append(cyl_between(fork_point(lower_clamp_len - 40) + head_off,
                             fork_point(top_clamp_len) + head_off, 34))

    # handlebar, risers, grips, TFT dash
    top = fork_point(top_clamp_len) + head_off
    clamp = top + np.array([-10, 0, 45])
    for s in (-1, 1):
        parts.append(capsule(top + np.array([0, 30 * s, 0]), clamp + np.array([0, 30 * s, 0]), 16))
        bar = [clamp + np.array([0, 30 * s, 0]),
               clamp + np.array([-15, 220 * s, 20]),
               clamp + np.array([-45, 360 * s, 40])]
        parts.append(chain(bar, 13))
        parts.append(capsule(bar[-1] + np.array([0, -60 * s, 0]),
                             bar[-1] + np.array([-5, 40 * s, 3]), 19))  # grip
        parts.append(blob([bar[1] + np.array([0, 20 * s, 5]),
                           bar[1] + np.array([30, 70 * s, 10])], 18))  # switchgear / lever perch
        parts.append(capsule(bar[1] + np.array([40, 60 * s, 5]),
                             bar[2] + np.array([90, 15 * s, 0]), 12))  # lever
    parts.append(capsule(clamp + np.array([0, -60, 0]), clamp + np.array([0, 60, 0]), 18))
    dash_c = top + np.array([60, 0, 85])
    parts.append(hull(box(-18, 12, -75, 75, -45, 45).rotate((0, -25, 0)).translate(tuple(dash_c)),
                      sphere(top + np.array([20, 0, 20]), 30)))

    # front face: compact LED projector under a sharp nacelle
    hl = fork_point(640) + np.array([90, 0, 0])
    nacelle = blob([hl + np.array([-60, -95, -40]), hl + np.array([-60, 95, -40]),
                    hl + np.array([-50, -110, 70]), hl + np.array([-50, 110, 70]),
                    hl + np.array([25, -55, 70]), hl + np.array([25, 55, 70]),
                    hl + np.array([45, -70, -10]), hl + np.array([45, 70, -10]),
                    hl + np.array([30, 0, -85])], 10)
    lamp = blob([hl + np.array([50, -45, -15]), hl + np.array([50, 45, -15]),
                 hl + np.array([40, 0, -60])], 14)
    drls = [capsule(hl + np.array([30, 35 * s, 55]), hl + np.array([45, 80 * s, 10]), 11)
            for s in (-1, 1)]
    parts += [nacelle, lamp] + drls
    parts.append(blob([hl + np.array([-60, -60, 0]), hl + np.array([-60, 60, 0]),
                       fork_point(600) + head_off], 25))

    # front fender (short, sporty)
    prof = CrossSection.square((30, 180), center=True).translate((322, 0))
    fender = to_xz(prof.revolve(SEG * 2, 85)).rotate((0, -140, 0))
    parts.append(fender.translate(tuple(FRONT_AXLE)))
    for y in (-88, 88):
        parts.append(capsule(fork_point(200, y), fork_point(130, y) + np.array([60, 0, 120]), 14))
    return union(parts)


def engine():
    parts = []
    # CP2 crankcase and gearbox
    parts.append(blob([(560, -130, 190), (560, 130, 190), (880, -120, 175), (880, 120, 175),
                       (540, -145, 420), (540, 145, 420), (870, -160, 430), (870, 160, 430),
                       (700, -120, 150), (700, 120, 150)], 25))
    # sump guard / belly
    parts.append(blob([(620, -100, 150), (620, 100, 150), (900, -90, 170), (900, 90, 170)], 20))
    # cylinder block leaning forward with cooling fins
    cyl_axis = np.array([math.sin(math.radians(40)), 0, math.cos(math.radians(40))])
    base = np.array([800.0, 0, 400])
    head = base + cyl_axis * 250
    block = blob([base + np.array([-80, -150, 0]), base + np.array([80, -150, 0]),
                  base + np.array([-80, 150, 0]), base + np.array([80, 150, 0]),
                  head + np.array([-80, -145, 0]), head + np.array([80, -145, 0]),
                  head + np.array([-80, 145, 0]), head + np.array([80, 145, 0])], 18)
    fins = []
    for k in range(5):
        p = base + cyl_axis * (70 + 34 * k)
        fins.append(cyl_between(p + np.array([0, -175, 0]), p + np.array([0, 175, 0]), 1, 4))
    fin_slots = union([capsule(f_a, f_b, 8, 12) for f_a, f_b in
                       [(base + cyl_axis * (70 + 34 * k) + np.array([-140, s * 172, 0]),
                         base + cyl_axis * (70 + 34 * k) + np.array([140, s * 172, 0]))
                        for k in range(5) for s in (-1, 1)]])
    parts.append(block - fin_slots)
    cover = head + cyl_axis * 25
    parts.append(blob([cover + np.array([-70, -135, 0]), cover + np.array([70, -135, 0]),
                       cover + np.array([-70, 135, 0]), cover + np.array([70, 135, 0]),
                       cover + cyl_axis * 45 + np.array([-40, -110, 0]),
                       cover + cyl_axis * 45 + np.array([40, 110, 0]),
                       cover + cyl_axis * 45 + np.array([-40, 110, 0]),
                       cover + cyl_axis * 45 + np.array([40, -110, 0])], 14))
    # side covers: clutch (right), alternator (left), sprocket cover (left)
    parts.append(ycyl((700, 160, 310), 105, 60))
    parts.append(ycyl((700, 190, 310), 80, 20))
    parts.append(ycyl((620, -160, 290), 95, 60))
    parts.append(ycyl((620, -185, 290), 70, 16))
    parts.append(blob([(520, -150, 330), (570, -150, 360)], 45))
    # radiator behind the front wheel, raked like the forks
    rad_bot = np.array([985.0, 0, 420])
    rad_top = rad_bot + FORK_DIR * 330
    parts.append(hull(box(rad_bot[0] - 25, rad_bot[0] + 25, -165, 125, rad_bot[2], rad_bot[2] + 5),
                      box(rad_top[0] - 25, rad_top[0] + 25, -165, 125, rad_top[2] - 5, rad_top[2])))
    # radiator to engine hose / mount
    parts.append(capsule(rad_bot + np.array([0, -60, 40]), (870, -100, 420), 22))
    return union(parts)


def exhaust():
    parts = []
    port = np.array([965.0, 0, 640])  # exhaust ports at the front of the head
    for s in (-1, 1):
        y = 55 * s
        pts = [port + np.array([0, y, 0]), (1040, y * 1.1, 520), (1060, y * 1.0, 330),
               (1000, y * 0.6, 190), (880, 15, 120)]
        parts.append(chain(pts, 23))
    # collector running back under the engine to the under-slung silencer
    parts.append(chain([(880, 15, 120), (700, 60, 115), (560, 120, 150)], 32))
    can = blob([(570, 95, 160), (570, 190, 170), (580, 110, 300), (575, 185, 300),
                (360, 110, 210), (360, 185, 215), (380, 110, 300), (380, 185, 305)], 18)
    parts.append(can)
    parts.append(ycyl((350, 145, 255), 30, 70))  # tip
    parts.append(cyl_between((340, 145, 255), (320, 145, 262), 26))
    return union(parts)


def frame_and_rear():
    parts = []
    head = fork_point(600) + np.array([-55, 0, 0])
    # steel backbone: twin spars from the steering head over the engine
    for s in (-1, 1):
        y = 95 * s
        parts.append(chain([head + np.array([0, y * 0.5, 30]), (900, y, 780),
                            (690, y * 1.2, 700), (600, y * 1.35, 560)], 24))
        parts.append(chain([head + np.array([0, y * 0.5, -60]), (940, y * 1.05, 640),
                            (920, y * 1.05, 560)], 20))  # engine hanger
        # pivot plates
        parts.append(blob([(540, y * 1.35, 380), (630, y * 1.35, 380),
                           (560, y * 1.35, 640), (650, y * 1.35, 640)], 14))
        # subframe rails under the seat
        parts.append(chain([(620, y * 1.25, 640), (350, y * 0.9, 770), (120, y * 0.6, 820)], 15))
        parts.append(capsule((560, y * 1.3, 480), (330, y * 0.95, 760), 13))
        # footpegs and heel guards
        parts.append(capsule((560, y * 1.4, 345), (560, y * 2.15, 335), 17))
        parts.append(blob([(520, y * 1.45, 330), (610, y * 1.45, 370), (560, y * 1.45, 470)], 12))
        parts.append(capsule((380, y * 1.2, 520), (370, y * 1.95, 520), 13))  # pillion peg
        parts.append(capsule((380, y * 1.2, 520), (450, y * 1.13, 614), 13))  # peg hanger
    parts.append(ycyl(SWINGARM_PIVOT, 26, 290))

    # aluminium swingarm, banana profile, braced ahead of the tyre
    for s in (-1, 1):
        y = 118 * s
        parts.append(hull(box(540, 610, y - 18, y + 18, 395, 490),
                          box(330, 360, y - 16, y + 16, 335, 425),
                          ycyl((10, y, 315), 34, 30)))
    parts.append(box(380, 450, -118, 118, 370, 440))
    parts.append(capsule((-15, -150, 315), (-15, 150, 315), 15))  # axle
    for s in (-1, 1):  # chain adjusters
        parts.append(box(-45, 40, 118 * s - 22, 118 * s + 22, 300, 330))

    # rear shock and linkage
    parts.append(capsule((640, 0, 690), (470, 0, 430), 30))
    parts.append(capsule((640, 0, 690), (520, 0, 510), 40, 32))  # spring
    parts.append(capsule((470, 0, 430), (410, 0, 405), 25))

    # chain run and rear sprocket (left side)
    parts.append(ycyl((0, -95, 315), 105, 14))
    parts.append(capsule((0, -95, 420), (600, -150, 335), 9))
    parts.append(capsule((0, -95, 210), (600, -150, 265), 9))
    # caliper on the rear disc
    parts.append(blob([(-60, 92, 210), (40, 92, 205)], 20))
    return union(parts)


def bodywork():
    parts = []
    # fuel tank with a sharp crease and recess for the rider's knees
    tank = blob([(700, -125, 820), (700, 125, 820), (720, -110, 900), (720, 110, 900),
                 (930, -150, 955), (930, 150, 955), (1080, -120, 950), (1080, 120, 950),
                 (1110, -110, 840), (1110, 110, 840), (900, -160, 800), (900, 160, 800)], 22)
    parts.append(tank)
    # angular air-intake shrouds flanking the tank
    for s in (-1, 1):
        sh = blob([(870, s * 150, 920), (1150, s * 140, 930), (1210, s * 185, 860),
                   (1170, s * 215, 760), (1070, s * 220, 640), (960, s * 205, 600),
                   (870, s * 170, 680), (920, s * 210, 820),
                   (900, s * 120, 700), (1120, s * 120, 700)], 9)
        parts.append(sh)
        # inlet vent pocket on each shroud
        vent = blob([(1120, s * 230, 820), (1180, s * 230, 790), (1110, s * 230, 700),
                     (1080, s * 205, 760)], 8)
        parts[-1] = parts[-1] - vent
        # side panel / airbox cover sweeping from the tank back under the seat
        parts.append(blob([(730, s * 150, 810), (740, s * 140, 640), (620, s * 128, 610),
                           (470, s * 140, 700), (430, s * 145, 770), (600, s * 155, 790),
                           (640, s * 70, 640)], 10))
        # tail-cowl side flank with a sharp lower crease
        parts.append(blob([(470, s * 142, 770), (450, s * 132, 700), (250, s * 112, 750),
                           (60, s * 75, 820), (-55, s * 48, 860), (40, s * 85, 870),
                           (300, s * 128, 830), (250, s * 40, 760)], 8))
    # rider seat: dished, narrow at the tank, wider at the rear
    parts.append(blob([(730, -95, 830), (730, 95, 830), (640, -140, 808), (640, 140, 808),
                       (470, -150, 800), (470, 150, 800), (430, -140, 815), (430, 140, 815),
                       (740, -110, 760), (740, 110, 760), (440, -140, 745), (440, 140, 745)], 18))
    # pillion pad stepped up above the rider seat
    parts.append(blob([(420, -120, 860), (420, 120, 860), (180, -95, 885), (180, 95, 885),
                       (440, -130, 800), (440, 130, 800), (170, -85, 820), (170, 85, 820)], 16))
    # tail unit: short and upswept, ending in a slim LED light bar
    parts.append(blob([(200, -100, 870), (200, 100, 870), (-55, -45, 900), (-55, 45, 900),
                       (-75, -35, 870), (-75, 35, 870), (200, -95, 790), (-20, -40, 830),
                       (200, 95, 790), (-20, 40, 830)], 10))
    parts.append(blob([(-80, -42, 885), (-80, 42, 885), (-95, -28, 875), (-95, 28, 875)], 9))
    # licence plate hugger arm and plate
    parts.append(capsule((80, 0, 760), (-140, 0, 610), 22))
    plate = box(-12, 12, -90, 90, -65, 65).rotate((0, 25, 0)).translate((-160, 0, 590))
    parts.append(plate)
    for s in (-1, 1):  # indicators
        parts.append(capsule((-135, s * 40, 640), (-150, s * 110, 650), 12))
    # belly pan / engine skid
    parts.append(blob([(620, -110, 150), (620, 110, 150), (960, -80, 200), (960, 80, 200)], 10))
    return union(parts)


def fill_voids(m):
    """Remove sealed internal cavities (inside-out shells) left by overlapping parts."""
    return Manifold.compose([p for p in m.decompose() if p.volume() > 0])


def motorcycle():
    front = wheel(FRONT_AXLE, 300, 120, 149, (-78, 78))
    rear = wheel(REAR_AXLE, 315, 180, 122, (92,))
    return fill_voids(union([front, rear, front_end(), engine(), exhaust(),
                             frame_and_rear(), bodywork()]))


# simple stroke font, glyph cell 0.6 wide x 1 tall
GLYPHS = {
    "M": [[(0, 0), (0, 1), (0.3, 0.45), (0.6, 1), (0.6, 0)]],
    "T": [[(0, 1), (0.6, 1)], [(0.3, 1), (0.3, 0)]],
    "-": [[(0.1, 0.5), (0.5, 0.5)]],
    "0": [[(0, 0), (0.6, 0), (0.6, 1), (0, 1), (0, 0)]],
    "7": [[(0, 1), (0.6, 1), (0.2, 0)]],
}


def lettering(text, x0, y0, height, stroke, depth):
    """Raised stroke lettering lying in the xy plane, reading along +x."""
    strokes, r = [], stroke / 2
    for i, ch in enumerate(text):
        for line in GLYPHS[ch]:
            for (u0, v0), (u1, v1) in zip(line, line[1:]):
                a = CrossSection.circle(r, 16).translate((x0 + (i * 0.9 + u0) * height, y0 + v0 * height))
                b = CrossSection.circle(r, 16).translate((x0 + (i * 0.9 + u1) * height, y0 + v1 * height))
                strokes.append(CrossSection.batch_hull([a, b]))
    return CrossSection.batch_boolean(strokes, OpType.Add).extrude(depth)


def display_base(bike):
    (x0, _, _), (x1, _, _) = bike.bounding_box()[:3], bike.bounding_box()[3:]
    xmin, xmax = min(-330.0, x0 - 60), max(1720.0, x1 + 60)
    w = 430.0
    slab = CrossSection.square((xmax - xmin - 2 * 60, w - 2 * 60)).offset(60, 0, circular_segments=32)
    slab = slab.translate((xmin + 60, -w / 2 + 60)).extrude(40).translate((0, 0, -36))
    # small raised kerb stripes for a bit of character
    stripes = union([box(xmin + 120 + i * 110, xmin + 170 + i * 110, w / 2 - 45, w / 2 - 25, 0, 10)
                     for i in range(int((xmax - xmin - 240) / 110) + 1)])
    label = lettering("MT-07", 720, -w / 2 + 28, 75, 16, 14)
    return slab + stripes + label


PIN_HOLES = [(720.0, 300.0), (930.0, 880.0)]  # (x, z) positions on the y=0 plane


def halves(bike, scale):
    pin_r = 1.05 / scale  # 2.1 mm hole for 1.75 mm filament pins
    pin_d = 3.5 / scale   # 3.5 mm deep in each half
    pins = union([ycyl((x, 0, z), pin_r, 2 * pin_d, 24) for x, z in PIN_HOLES])
    right = bike.trim_by_plane((0, 1, 0), 0.0) - pins
    left = bike.trim_by_plane((0, -1, 0), 0.0) - pins
    # lay each half on its cut face
    right = right.rotate((90, 0, 0))
    left = left.rotate((-90, 0, 0))
    return left, right


def drop_to_bed(m):
    (x0, y0, z0, x1, y1, z1) = m.bounding_box()
    return m.translate((-(x0 + x1) / 2, -(y0 + y1) / 2, -z0))


def write_stl(m, path):
    mesh = m.to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3].astype(np.float32)
    f = np.asarray(mesh.tri_verts)
    tri = v[f]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    data = np.zeros(len(f), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    data["n"], data["v"] = n, tri
    with open(path, "wb") as fh:
        fh.write(b"Yamaha MT-07 (2025) stylized scale model".ljust(80, b" "))
        fh.write(np.uint32(len(f)).tobytes())
        fh.write(data.tobytes())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scale", type=float, default=18, help="scale denominator, e.g. 18 for 1:18")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "stl"))
    ap.add_argument("--preview", action="store_true", help="also render preview.png")
    args = ap.parse_args()
    s = 1.0 / args.scale
    os.makedirs(args.out, exist_ok=True)

    bike = motorcycle()
    assert bike.status().name == "NoError", bike.status()
    left, right = halves(bike, s)
    full = bike.translate((0, 0, 4)) + display_base(bike)  # tyres sink 4 mm into the base

    outputs = {
        "mt07_full.stl": drop_to_bed(full.scale((s, s, s))),
        "mt07_half_left.stl": drop_to_bed(left.scale((s, s, s))),
        "mt07_half_right.stl": drop_to_bed(right.scale((s, s, s))),
    }
    for name, m in outputs.items():
        path = os.path.join(args.out, name)
        write_stl(m, path)
        b = m.bounding_box()
        print(f"{name:22s} {b[3]-b[0]:6.1f} x {b[4]-b[1]:5.1f} x {b[5]-b[2]:5.1f} mm  "
              f"{m.volume()/1000:5.1f} cm3  {m.num_tri()} tris  genus {m.genus()}")
    if args.preview:
        from preview import render
        render(bike.scale((s, s, s)), os.path.join(os.path.dirname(args.out), "preview.png"))


if __name__ == "__main__":
    main()
