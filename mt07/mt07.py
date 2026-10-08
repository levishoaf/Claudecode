#!/usr/bin/env python3
"""
Stylized 3D-printable scale model of a 2025 Yamaha MT-07.

All geometry is authored in real-world millimetres (rear axle at x=0,
ground at z=0, +x toward the front, +y to the rider's right) and scaled
down at export time. Real dimensions used as reference:
  wheelbase 1395 mm, length ~2065 mm, seat height 805 mm,
  120/70-17 front and 180/55-17 rear tyres, 24.3 deg rake, 94 mm trail,
  298 mm front discs, 245 mm rear disc, 140 mm ground clearance.
Small parts are thickened so they survive printing at 1:18.

Every part is tagged with a colour group (paint, dark, metal, light) so the
same geometry can be exported as one solid or as multi-material sections.

Outputs (in ./stl):
  mt07_full.stl            one piece on a display base, print upright with supports
  mt07_half_left/right     the whole bike split down the middle, print flat, no supports
  kit/                     wheels as separate parts on 1.75 mm filament axles
  colour/                  four aligned sections for multi-material printers
  mt07_mirrors.stl         optional mirrors that peg into the handlebar mounts

Usage: python3 mt07.py [--scale 18] [--out stl] [--preview]
"""
import argparse
import math
import os

import numpy as np
from manifold3d import CrossSection, Manifold, Mesh, OpType

SEG = 64
SCALE = 18.0  # scale denominator; set from the command line before building
GROUPS = ("light", "metal", "paint", "dark")  # highest priority first
COLOURS = {"paint": (0.13, 0.30, 0.78), "dark": (0.17, 0.17, 0.19),
           "metal": (0.70, 0.72, 0.75), "light": (0.95, 0.95, 0.90)}


def pm(mm):
    """Convert a printed size in mm to real-world model units at the current scale."""
    return mm * SCALE


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


def yspan(center, r, y0, y1, seg=SEG):
    """Cylinder along y from y0 to y1 at the (x, z) of `center`."""
    return cyl_between((center[0], y0, center[2]), (center[0], y1, center[2]), r, seg)


def box(x0, x1, y0, y1, z0, z1):
    return Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))


def union(parts):
    parts = [p for p in parts if p is not None and not p.is_empty()]
    if not parts:
        return Manifold()
    return Manifold.batch_boolean(parts, OpType.Add)


def to_xz(m):
    """Map a revolve()-made part (axis = z) so that its axis becomes y."""
    return m.rotate((-90, 0, 0))


def fill_voids(m):
    """Remove sealed internal cavities (inside-out shells) left by overlapping parts."""
    return Manifold.compose([p for p in m.decompose() if p.volume() > 0])


class Parts:
    """Parts sorted into colour groups."""

    def __init__(self):
        self.g = {k: [] for k in GROUPS}

    def add(self, group, *ms):
        self.g[group] += ms
        return self

    def merge(self, other):
        for k in GROUPS:
            self.g[k] += other.g[k]
        return self

    def map(self, fn):
        out = Parts()
        for k in GROUPS:
            out.g[k] = [fn(m) for m in self.g[k]]
        return out

    def solid(self):
        return fill_voids(union([m for k in GROUPS for m in self.g[k]]))

    def sections(self):
        """Non-overlapping solids per group; higher-priority groups win overlaps."""
        out, taken = {}, Manifold()
        for k in GROUPS:
            m = union(self.g[k])
            if not m.is_empty():
                out[k] = m - taken
                taken = taken + m
        return out


# --------------------------------------------------------------- layout

REAR_AXLE = np.array([0.0, 0.0, 315.0])
FRONT_AXLE = np.array([1395.0, 0.0, 300.0])
RAKE = math.radians(24.3)  # Yamaha spec: 24.3 deg rake, 94 mm trail
FORK_DIR = np.array([-math.sin(RAKE), 0.0, math.cos(RAKE)])
FORK_OFFSET = 300 * math.sin(RAKE) - 94 * math.cos(RAKE)  # gives the 94 mm trail
HEAD_OFF = -FORK_OFFSET * np.array([math.cos(RAKE), 0.0, math.sin(RAKE)])  # fork plane -> steering axis
LOWER_CLAMP, TOP_CLAMP = 560.0, 720.0
SWINGARM_PIVOT = np.array([575.0, 0.0, 440.0])
FORK_Y = 112.0  # fork leg centre lines
ARM_Y = 118.0   # swingarm arm centre lines

# Wheel specs. hub/boss/collar are half-widths along the axle; discs are
# (sides, radius, inner face, outer face) with sides "both" or "right";
# sprocket is (radius, inner face, outer face) on the left.
FRONT = dict(center=FRONT_AXLE, outer_r=300, tyre_w=120, hub=57, boss=64,
             discs=[("both", 149, 64, 76)], collar=80, sprocket=None)
REAR = dict(center=REAR_AXLE, outer_r=315, tyre_w=180, hub=80, boss=80,
            discs=[("right", 122, 80, 92)], collar=98, sprocket=(105, 80, 94))


def fork_point(length, y=0.0):
    p = FRONT_AXLE + FORK_DIR * length
    return np.array([p[0], y, p[2]])


# --------------------------------------------------------------- parts


def wheel_core(spec, rim_spokes=10):
    """Tyre, rim and hub, centred on the origin with the axle along y."""
    outer_r, tyre_w = spec["outer_r"], spec["tyre_w"]
    rim_r = 216.0  # 17 inch bead seat
    crown = CrossSection.circle(tyre_w / 2, 48).translate((outer_r - tyre_w / 2, 0))
    bead = CrossSection.square((10, tyre_w * 0.82), center=True).translate((rim_r + 5, 0))
    profile = CrossSection.batch_hull([crown, bead])
    profile = profile ^ CrossSection.square((outer_r - rim_r + 1, tyre_w + 2)).translate((rim_r, -tyre_w / 2 - 1))
    tyre = to_xz(profile.revolve(SEG * 2))

    # rim with Y-spoke pockets cut from both faces, leaving a central web
    rim_w = tyre_w * 0.78
    rim = to_xz(Manifold.cylinder(rim_w, rim_r + 8, rim_r + 8, SEG * 2, center=True))
    ring = CrossSection.circle(rim_r - 14, SEG * 2) - CrossSection.circle(62, SEG)
    spokes = []
    for i in range(rim_spokes // 2):
        a = 360.0 / (rim_spokes // 2) * i
        spokes.append(CrossSection.square((90, 26), center=True).translate((95, 0)).rotate(a))
        for s in (-1, 1):  # the two branches of each Y
            arm = (CrossSection.square((110, 24), center=True)
                   .translate((55, 0)).rotate(s * 14).translate((130, 0)))
            spokes.append(arm.rotate(a))
    pocket = ring - CrossSection.batch_boolean(spokes, OpType.Add)
    depth = rim_w * 0.30
    cut = to_xz(pocket.extrude(depth + 1))
    cut = cut.translate((0, rim_w / 2 - depth - cut.bounding_box()[1], 0))
    rim = rim - cut - cut.mirror((0, 1, 0))
    hub = ycyl((0, 0, 0), 55, 2 * spec["hub"]) + ycyl((0, 0, 0), 70, 2 * spec["boss"])
    return Parts().add("dark", tyre).add("paint", rim, hub)


def rotors(spec):
    """Brake discs and sprocket with their axle collars, centred on the origin."""
    p = Parts()
    for side, r, y0, y1 in spec["discs"]:
        for sgn in ((-1, 1) if side == "both" else (1,)):
            disc = yspan((0, 0, 0), r, sgn * y0, sgn * y1)
            holes = [ycyl((math.cos(t) * (r - 30), 0, math.sin(t) * (r - 30)), 9, 400, 12)
                     for t in np.linspace(0, 2 * math.pi, 12, endpoint=False)]
            # floating-disc look: six windows through the inner carrier
            win = union([blob([(math.cos(t + d) * rr, 0, math.sin(t + d) * rr)
                               for d in (-0.22, 0.22) for rr in (r * 0.52, r * 0.74)], 9, 12)
                         .scale((1, 40, 1)) for t in np.linspace(0, 2 * math.pi, 6, endpoint=False)])
            disc = disc - union(holes) - win
            collar = yspan((0, 0, 0), 28, sgn * y1, sgn * spec["collar"], 32)
            p.add("metal", disc, collar)
    if spec["sprocket"]:
        r, y0, y1 = spec["sprocket"]
        teeth = CrossSection.circle(r, 40) + CrossSection.batch_boolean(
            [CrossSection.square((22, 16), center=True).translate((r, 0)).rotate(a)
             for a in np.arange(0, 360, 9)], OpType.Add)
        sprocket = to_xz(teeth.extrude(y1 - y0))
        sprocket = sprocket.translate((0, -y1 - sprocket.bounding_box()[1], 0))  # y from -y1 to -y0
        p.add("metal", sprocket, yspan((0, 0, 0), 28, -y1, -spec["collar"], 32))
    return p


def wheel(spec):
    return wheel_core(spec).merge(rotors(spec)).map(lambda m: m.translate(tuple(spec["center"])))


def bar_points(s):
    """Handlebar centre line on side s (-1 left, +1 right): clamp, perch, bar end.
    The 2025 bar is wider, lower and further back than before."""
    top = fork_point(TOP_CLAMP) + HEAD_OFF
    clamp = top + np.array([-15, 0, 35])
    return [clamp + np.array([0, 30 * s, 0]),
            clamp + np.array([-25, 230 * s, 12]),
            clamp + np.array([-60, 375 * s, 28])]


def mirror_mount(s):
    """Top centre of the mirror boss on the handlebar perch (side s = -1 left, +1 right)."""
    return bar_points(s)[1] + np.array([15, 40 * s, 60])


def front_end():
    p = Parts()
    for s in (-1, 1):
        y = FORK_Y * s
        # 41 mm KYB upside-down fork: slim inner tube below, fat outer tube above
        p.add("metal", cyl_between(fork_point(-10, y), fork_point(330, y), 22, 32))
        p.add("dark", cyl_between(fork_point(300, y), fork_point(TOP_CLAMP + 20, y), 30, 32),
              cyl_between(fork_point(296, y), fork_point(320, y), 33, 32))  # dust seal ring
        p.add("metal", cyl_between(fork_point(TOP_CLAMP + 12, y), fork_point(TOP_CLAMP + 38, y), 22, 24))  # cap, sunk into the tube
        # axle clamp with pinch-bolt slot, and a small fork guard
        lug = blob([fork_point(-25, y), fork_point(40, y)], 30)
        p.add("dark", lug - box(-30, 30, y + s * 26 - 4, y + s * 26 + 4, -60, -30).translate(tuple(FRONT_AXLE)))
        p.add("dark", blob([fork_point(60, y) + np.array([28, 0, 0]), fork_point(220, y) + np.array([30, 0, 0])], 10))
        # radial four-piston caliper behind the leg, bolted on two in-line ears
        c = FRONT_AXLE + np.array([-112, 0, 96])
        body = blob([(c[0] - 38, 80 * s, c[2] - 46), (c[0] + 32, 80 * s, c[2] + 50),
                     (c[0] - 10, 80 * s, c[2] - 55), (c[0] + 12, 80 * s, c[2] + 58)], 20)
        pistons = [ycyl((c[0] - 18 + 32 * k, 96 * s, c[2] - 22 + 40 * k), 16, 10, 16) for k in (0, 1)]
        ears = [capsule((c[0] - 22 + 50 * k, 92 * s, c[2] - 30 + 60 * k), fork_point(70 + 85 * k, y), 13)
                for k in (0, 1)]
        p.add("dark", body, *ears)
        p.add("metal", *pistons)
        p.add("dark", chain([(c[0] + 10, 80 * s, c[2] + 60), fork_point(260, y) + np.array([-35, 0, 0])], 7))  # brake line
    p.add("metal", capsule(fork_point(0, -FORK_Y - 28), fork_point(0, FORK_Y + 28), 14))  # axle

    # triple clamps and steering head
    for L, h in ((LOWER_CLAMP, 30), (TOP_CLAMP, 26)):
        a, b = fork_point(L, -145), fork_point(L, 145)
        p.add("dark", blob([a, b, a + HEAD_OFF * 1.4, b + HEAD_OFF * 1.4], h / 2 + 4))
    p.add("dark", cyl_between(fork_point(LOWER_CLAMP - 40) + HEAD_OFF,
                              fork_point(TOP_CLAMP) + HEAD_OFF, 34))
    p.add("metal", cyl_between(fork_point(TOP_CLAMP) + HEAD_OFF, fork_point(TOP_CLAMP + 12) + HEAD_OFF, 20, 24))

    # handlebar, risers, grips, switchgear, levers, mirror mounts
    holes = []
    top = fork_point(TOP_CLAMP) + HEAD_OFF
    for s in (-1, 1):
        bar = bar_points(s)
        p.add("dark", capsule(top + np.array([0, 30 * s, 0]), bar[0], 16))
        p.add("dark", chain(bar, 13))
        p.add("dark", capsule(bar[2] + np.array([5, -75 * s, -5]), bar[2] + np.array([0, 25 * s, 2]), 19))  # grip
        p.add("metal", sphere(bar[2] + np.array([0, 32 * s, 2]), 18))  # bar-end weight
        p.add("dark", blob([bar[1] + np.array([-5, 15 * s, 0]), bar[1] + np.array([25, 75 * s, 10]),
                            bar[1] + np.array([-20, 60 * s, -15])], 18))  # switch cube / perch
        p.add("metal", capsule(bar[1] + np.array([40, 70 * s, 5]), bar[2] + np.array([75, 10 * s, 0]), 11))  # lever
        if s > 0:
            p.add("dark", blob([bar[1] + np.array([10, 40, 50]), bar[1] + np.array([30, 40, 70])], 22))  # brake reservoir
        boss = mirror_mount(s)
        p.add("dark", cyl_between(boss - np.array([0, 0, 45]), boss, pm(1.6), 32))
        holes.append(cyl_between(boss - np.array([0, 0, pm(2.6)]), boss + np.array([0, 0, 5]), pm(0.85), 24))
    p.add("dark", capsule(bar_points(-1)[0], bar_points(1)[0], 18))

    # 5-inch TFT on a stalk, under a short tinted visor
    dash_c = top + np.array([75, 0, 80])
    tft = box(-15, 15, -68, 68, -42, 42).rotate((0, -28, 0)).translate(tuple(dash_c))
    p.add("dark", hull(tft, sphere(top + np.array([30, 0, 15]), 25)))
    p.add("dark", hull(box(-6, 6, -72, 72, -6, 6).rotate((0, -40, 0)).translate(tuple(dash_c + np.array([-8, 0, 46]))),
                       box(-6, 6, -60, 60, -6, 6).rotate((0, -40, 0)).translate(tuple(dash_c + np.array([20, 0, 76])))))

    # 2025 face: angular black mask with a central LED flanked by two "eye"
    # DRLs, and a small "forehead" position light above
    hl = fork_point(625) + np.array([95, 0, 0])
    mask = blob([hl + np.array([-60, -88, -30]), hl + np.array([-60, 88, -30]),
                 hl + np.array([-45, -95, 65]), hl + np.array([-45, 95, 65]),
                 hl + np.array([30, -45, 72]), hl + np.array([30, 45, 72]),
                 hl + np.array([45, -72, 5]), hl + np.array([45, 72, 5]),
                 hl + np.array([38, -30, -78]), hl + np.array([38, 30, -78]),
                 hl + np.array([-30, 0, -95])], 9)
    socket = cyl_between(hl + np.array([0, 0, 14]), hl + np.array([90, 0, 14]), 28, 32)
    p.add("dark", mask - socket)
    p.add("light", cyl_between(hl + np.array([0, 0, 14]), hl + np.array([44, 0, 14]), 28, 32))  # central LED
    for s in (-1, 1):
        p.add("light", blob([hl + np.array([40, 38 * s, 34]), hl + np.array([47, 76 * s, 16]),
                             hl + np.array([42, 70 * s, -2]), hl + np.array([38, 40 * s, 8])], 7))  # eye DRLs
    p.add("light", blob([hl + np.array([30, -18, 64]), hl + np.array([30, 18, 64])], 7))  # forehead
    p.add("dark", blob([hl + np.array([-60, -60, 0]), hl + np.array([-60, 60, 0]),
                        fork_point(600) + HEAD_OFF], 25))

    # front fender (short, sporty) on stays bolted to the fork legs
    prof = CrossSection.square((30, 180), center=True).translate((322, 0))
    fender = to_xz(prof.revolve(SEG * 2, 85)).rotate((0, -140, 0))
    p.add("paint", fender.translate(tuple(FRONT_AXLE)))
    for s in (-1, 1):
        y = (FORK_Y - 15) * s
        p.add("dark", capsule(fork_point(200, y), fork_point(130, y) + np.array([60, 0, 120]), 14))
    return p, holes


def engine():
    p = Parts()
    # CP2 crankcase and gearbox
    p.add("dark", blob([(560, -130, 190), (560, 130, 190), (880, -120, 175), (880, 120, 175),
                        (540, -145, 420), (540, 145, 420), (870, -160, 430), (870, 160, 430),
                        (700, -120, 172), (700, 120, 172)], 25))
    p.add("dark", blob([(620, -100, 168), (620, 100, 168), (900, -90, 180), (900, 90, 180)], 20))
    # crankcase parting line
    p.add("metal", blob([(545, -150, 300), (545, 150, 300), (900, -150, 300), (900, 150, 300)], 7)
          - box(600, 860, -140, 140, 200, 400))
    # cylinder block leaning forward with deep cooling fins
    cyl_axis = np.array([math.sin(math.radians(40)), 0, math.cos(math.radians(40))])
    base = np.array([800.0, 0, 400])
    head = base + cyl_axis * 250
    block = blob([base + np.array([-80, -150, 0]), base + np.array([80, -150, 0]),
                  base + np.array([-80, 150, 0]), base + np.array([80, 150, 0]),
                  head + np.array([-80, -145, 0]), head + np.array([80, -145, 0]),
                  head + np.array([-80, 145, 0]), head + np.array([80, 145, 0])], 18)
    slots = []
    for k in range(6):
        c = base + cyl_axis * (55 + 30 * k)
        for s in (-1, 1):  # fins on both sides
            slots.append(capsule(c + np.array([-150, s * 168, 0]), c + np.array([150, s * 168, 0]), 8, 12))
        slots.append(capsule(c + np.array([-115, -125, 0]), c + np.array([-115, 125, 0]), 8, 12))
    p.add("dark", block - union(slots))
    # cam cover with raised ribs and twin spark-plug covers
    cover = head + cyl_axis * 25
    up = cyl_axis * 45
    p.add("metal", blob([cover + np.array([-70, -135, 0]), cover + np.array([70, -135, 0]),
                         cover + np.array([-70, 135, 0]), cover + np.array([70, 135, 0]),
                         cover + up + np.array([-40, -110, 0]), cover + up + np.array([40, 110, 0]),
                         cover + up + np.array([-40, 110, 0]), cover + up + np.array([40, -110, 0])], 14))
    for y in (-120, -40, 40, 120):
        p.add("dark", capsule(cover + up * 1.15 + np.array([-45, y, 0]),
                              cover + up * 1.15 + np.array([45, y, 0]), 9, 12))
    for y in (-70, 70):
        p.add("dark", cyl_between(cover + up * 0.8 + np.array([0, y, 0]),
                                  cover + up * 1.5 + np.array([0, y, 0]), 24, 24))
    # side covers: clutch (right), alternator (left), sprocket cover (left)
    p.add("metal", ycyl((700, 160, 310), 105, 60), ycyl((620, -160, 290), 95, 60))
    p.add("dark", ycyl((700, 192, 310), 80, 20), ycyl((620, -188, 290), 70, 16))
    for a in np.linspace(0, 2 * math.pi, 8, endpoint=False):  # cover bolts
        p.add("dark", sphere((700 + 92 * math.cos(a), 190, 310 + 92 * math.sin(a)), 10, 12),
              sphere((620 + 83 * math.cos(a), -190, 290 + 83 * math.sin(a)), 10, 12))
    p.add("dark", blob([(520, -150, 330), (570, -150, 360)], 45))
    # water pump and hose, oil filter, starter motor
    p.add("metal", ycyl((790, -165, 230), 45, 50))
    p.add("dark", chain([(790, -185, 250), (900, -175, 330), (985, -110, 440)], 16))
    p.add("metal", cyl_between((860, -70, 250), (960, -70, 220), 40, 32))
    p.add("dark", ycyl((690, -30, 470), 38, 160))
    # gear shift (left) and rear brake pedal (right)
    p.add("metal", ycyl((600, -175, 300), 20, 40),
          capsule((600, -192, 300), (665, -200, 365), 12),
          capsule((665, -185, 365), (665, -230, 365), 11))
    p.add("metal", capsule((555, 205, 330), (680, 210, 320), 12),
          blob([(670, 195, 318), (700, 225, 318)], 12))
    # radiator behind the front wheel, raked like the forks
    rad_bot = np.array([985.0, 0, 420])
    rad_top = rad_bot + FORK_DIR * 330
    rad = hull(box(rad_bot[0] - 25, rad_bot[0] + 25, -165, 125, rad_bot[2], rad_bot[2] + 5),
               box(rad_top[0] - 25, rad_top[0] + 25, -165, 125, rad_top[2] - 5, rad_top[2]))
    fins = union([capsule(rad_bot + FORK_DIR * t + np.array([30, -140, 0]),
                          rad_bot + FORK_DIR * t + np.array([30, 100, 0]), 6, 8)
                  for t in np.arange(30, 320, 26)])
    p.add("dark", rad - fins)
    p.add("dark", capsule(rad_bot + np.array([0, -60, 40]), (870, -100, 420), 22))
    return p


def exhaust():
    p = Parts()
    port = np.array([965.0, 0, 640])  # exhaust ports at the front of the head
    for s in (-1, 1):
        y = 55 * s
        pts = [port + np.array([0, y, 0]), np.array((1040, y * 1.1, 520)), (1060, y * 1.0, 330),
               (1000, y * 0.6, 200), (880, 15, 172)]
        p.add("metal", chain(pts, 23))
        d = (pts[1] - pts[0]) / np.linalg.norm(pts[1] - pts[0])
        p.add("metal", cyl_between(pts[0] + d * 10, pts[0] + d * 35, 34, 32))  # port flange
    p.add("metal", chain([(880, 15, 172), (700, 60, 172), (560, 120, 180)], 32))
    p.add("metal", blob([(955, 10, 200), (875, 18, 182)], 44))  # catalytic converter at the collector
    can = blob([(570, 95, 168), (570, 190, 172), (580, 110, 300), (575, 185, 300),
                (380, 110, 215), (380, 185, 220), (390, 110, 300), (390, 185, 305)], 18)
    p.add("dark", can)
    p.add("dark", blob([(560, 205, 195), (560, 205, 285), (420, 205, 230), (420, 205, 290)], 8)
          - union([capsule((540 - 35 * k, 215, 215), (540 - 35 * k, 215, 275), 7, 12) for k in range(4)]))  # shield
    p.add("metal", blob([(375, 105, 222), (375, 190, 226), (375, 110, 300), (375, 190, 303),
                         (345, 125, 240), (345, 175, 240), (345, 125, 285), (345, 175, 285)], 12))  # end cap
    for z in (250, 280):
        p.add("dark", cyl_between((350, 150, z), (320, 150, z + 4), 13, 16))  # outlets
    return p


def frame_and_rear():
    p = Parts()
    head = fork_point(600) + HEAD_OFF
    # steel backbone: twin spars from the steering head over the engine
    for s in (-1, 1):
        y = 95 * s
        p.add("dark", chain([head + np.array([0, y * 0.5, 30]), (900, y, 780),
                             (690, y * 1.2, 700), (600, y * 1.35, 560)], 28))
        p.add("dark", chain([head + np.array([0, y * 0.5, -60]), (940, y * 1.05, 640),
                             (920, y * 1.05, 560)], 23))  # engine hanger
        p.add("dark", blob([(540, y * 1.35, 380), (630, y * 1.35, 380),
                            (560, y * 1.35, 640), (650, y * 1.35, 640)], 14))  # pivot plate
        p.add("dark", chain([(620, y * 1.25, 640), (350, y * 0.9, 770), (120, y * 0.6, 820)], 15))
        p.add("dark", capsule((560, y * 1.3, 480), (330, y * 0.95, 760), 13))
        # footpegs, heel guards, pillion pegs
        p.add("metal", capsule((560, y * 1.4, 345), (560, y * 2.15, 335), 17))
        p.add("dark", blob([(520, y * 1.45, 330), (610, y * 1.45, 370), (560, y * 1.45, 470)], 12))
        p.add("metal", capsule((380, y * 1.2, 520), (370, y * 1.95, 520), 13))
        p.add("dark", capsule((380, y * 1.2, 520), (450, y * 1.13, 614), 13))
    p.add("metal", ycyl(SWINGARM_PIVOT, 26, 290))

    # new asymmetric steel swingarm with slim lines, braced ahead of the tyre
    for s in (-1, 1):
        y = ARM_Y * s
        pivot = box(545, 605, y - 15, y + 15, 400, 480)
        end = ycyl((10, y, 315), 32, 28)
        if s > 0:  # right arm arches up to clear the under-slung silencer
            mid = box(300, 330, y - 14, y + 14, 380, 440)
            p.add("dark", hull(pivot, mid), hull(mid, end))
        else:      # left arm runs straight to the axle
            p.add("dark", hull(pivot, box(330, 360, y - 14, y + 14, 345, 410), end))
        p.add("metal", box(-45, 40, y - 22, y + 22, 300, 330))  # chain adjuster
    p.add("dark", box(380, 450, -ARM_Y, ARM_Y, 370, 440))
    p.add("metal", capsule((0, -ARM_Y - 32, 315), (0, ARM_Y + 32, 315), 15))  # axle

    # rear shock and linkage
    p.add("metal", capsule((640, 0, 690), (470, 0, 430), 30))
    p.add("paint", capsule((640, 0, 690), (520, 0, 510), 40, 32))  # spring
    p.add("dark", capsule((470, 0, 430), (410, 0, 405), 25))

    # chain run (left) and rear caliper with its bracket (right)
    p.add("dark", capsule((0, -87, 420), (600, -150, 335), 9),
          capsule((0, -87, 210), (600, -150, 265), 9))
    p.add("dark", blob([(120, -95, 440), (480, -125, 400), (480, -150, 400), (120, -112, 440),
                        (300, -110, 445)], 6))  # chain guard
    p.add("dark", blob([(-60, 92, 210), (40, 92, 205)], 20),
          capsule((-10, 104, 215), (10, 112, 300), 14))
    return p


def bodywork():
    p = Parts()
    # fuel tank with a sharp crease and recess for the rider's knees
    tank = blob([(700, -92, 820), (700, 92, 820), (720, -85, 900), (720, 85, 900),
                 (930, -138, 950), (930, 138, 950), (1080, -112, 950), (1080, 112, 950),
                 (1110, -100, 840), (1110, 100, 840), (900, -148, 800), (900, 148, 800),
                 (1000, 0, 978), (800, 0, 945)], 22)
    def tank_top(x):
        return 922 + (x - 720) * 50 / 210  # flat crown line of the tank cover

    vents, grilles = [], []
    for x in (750, 820):  # four acoustic-amplifier vents, two each side of the crease
        for s in (-1, 1):
            pts = [(xx, s * yy, 0) for xx in (x, x + 50) for yy in (30, 58)]
            z = tank_top(x + 25)
            vents.append(union([blob([(px, py, z - 18) for px, py, _ in pts], 8),
                                blob([(px, py, z + 40) for px, py, _ in pts], 8)]).hull())
            grilles.append(blob([(px, py, z - 24) for px, py, _ in pts], 6))  # sits on the vent floor
    p.add("paint", tank - union(vents))
    p.add("dark", *grilles)
    p.add("metal", blob([(1060, -18, 962), (1060, 18, 962), (1035, -18, 966), (1035, 18, 966)], 8))  # filler cap
    for s in (-1, 1):
        # angular side covers flowing down from the tank (MT-09 lines)
        sh = blob([(870, s * 150, 920), (1150, s * 140, 930), (1210, s * 185, 860),
                   (1170, s * 215, 760), (1070, s * 220, 640), (960, s * 205, 600),
                   (870, s * 170, 680), (920, s * 210, 820),
                   (900, s * 120, 700), (1120, s * 120, 700)], 9)
        p.add("paint", sh)  # 2025: no side intake vents behind the forks
        p.add("dark", blob([(980, s * 150, 610), (1080, s * 165, 600), (1150, s * 180, 700),
                            (1120, s * 200, 800), (1000, s * 190, 720)], 8))  # inner layer
        p.add("dark", blob([(1010, s * 140, 560), (1060, s * 150, 450), (1000, s * 145, 430),
                            (960, s * 135, 560)], 8))  # radiator side cover
        # side panel / airbox cover sweeping from the tank back under the seat
        p.add("dark", blob([(730, s * 150, 810), (740, s * 140, 640), (620, s * 128, 610),
                            (470, s * 140, 700), (430, s * 145, 770), (600, s * 155, 790),
                            (640, s * 70, 640)], 10))
        # tail-cowl side flank with a sharp lower crease
        p.add("paint", blob([(470, s * 142, 770), (450, s * 132, 700), (250, s * 112, 750),
                             (60, s * 75, 820), (-55, s * 48, 860), (40, s * 85, 870),
                             (300, s * 128, 830), (250, s * 40, 760)], 8))
    # rider seat: dished, narrow at the tank, wider at the rear
    p.add("dark", blob([(730, -95, 830), (730, 95, 830), (640, -140, 808), (640, 140, 808),
                        (470, -150, 800), (470, 150, 800), (430, -140, 815), (430, 140, 815),
                        (740, -110, 760), (740, 110, 760), (440, -140, 745), (440, 140, 745)], 18))
    # pillion pad stepped up above the rider seat
    p.add("dark", blob([(420, -120, 860), (420, 120, 860), (180, -95, 885), (180, 95, 885),
                        (440, -130, 800), (440, 130, 800), (170, -85, 820), (170, 85, 820)], 16))
    # tail unit: short and upswept, ending in a slim LED light bar
    p.add("paint", blob([(200, -100, 870), (200, 100, 870), (-55, -45, 900), (-55, 45, 900),
                         (-75, -35, 870), (-75, 35, 870), (200, -95, 790), (-20, -40, 830),
                         (200, 95, 790), (-20, 40, 830)], 10))
    p.add("light", blob([(-80, -12, 902), (-80, 12, 902), (-84, -9, 848), (-84, 9, 848)], 7))  # vertical LED line
    # licence plate hugger arm, plate and indicators
    p.add("dark", blob([(120, -55, 800), (120, 55, 800), (60, -45, 770), (60, 45, 770),
                        (-230, -90, 690), (-230, 90, 690), (-230, -80, 660), (-230, 80, 660)], 10))  # rear fender
    p.add("light", box(-12, 12, -90, 90, -65, 65).rotate((0, 25, 0)).translate((-260, 0, 620)))
    for s in (-1, 1):
        p.add("light", capsule((-240, s * 50, 665), (-255, s * 115, 675), 12))
    # belly pan / engine skid
    p.add("dark", blob([(640, -110, 205), (640, 110, 205), (960, -80, 215), (960, 80, 215)], 10))
    return p


def bike(wheels=True):
    """All parts of the motorcycle (optionally without wheels) plus holes to drill."""
    fe, holes = front_end()
    p = Parts().merge(fe).merge(engine()).merge(exhaust()).merge(frame_and_rear()).merge(bodywork())
    if wheels:
        p.merge(wheel(FRONT)).merge(wheel(REAR))
    return p, holes


def wheel_envelope(spec):
    """Space a wheel and its rotors sweep, plus running clearance, for the kit body."""
    c = tuple(spec["center"])
    disc_r = max(d[1] for d in spec["discs"])
    return (ycyl(c, spec["outer_r"] + 8.5, spec["tyre_w"] + 13)
            + ycyl(c, disc_r + 8.5, 2 * spec["collar"] + 4.5))


def axle_hole(spec):
    return ycyl(tuple(spec["center"]), pm(1.05), 600, 24)  # 2.1 mm for 1.75 mm filament


# --------------------------------------------------------------- mirrors


def mirror_part():
    """One mirror in local coordinates: peg down -z from the origin, stalk out along +y."""
    peg = cyl_between((0, 0, -pm(2.3)), (0, 0, 5), pm(0.7), 24)
    stalk = chain([(0, 0, 0), (0, 30, 120), (0, 110, 200)], pm(0.6), 16)
    t = pm(0.6)
    head = blob([(0, 95, 175), (0, 230, 190), (0, 255, 235), (0, 120, 250), (0, 85, 215)], 10, 12)
    head = hull(head.translate((-t + 10, 0, 0)), head.translate((t - 10, 0, 0)))
    return union([peg, stalk, head]).trim_by_plane((1, 0, 0), -pm(0.45))


def mirrors_on_bike():
    out = []
    for s in (-1, 1):
        m = mirror_part()
        if s < 0:
            m = m.mirror((0, 1, 0))
        out.append(m.translate(tuple(mirror_mount(s))))
    return out


# --------------------------------------------------------------- display base


GLYPHS = {  # simple stroke font, glyph cell 0.6 wide x 1 tall
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


def display_base():
    xmin, xmax, w = -375.0, 1755.0, 430.0
    slab = CrossSection.square((xmax - xmin - 2 * 60, w - 2 * 60)).offset(60, 0, circular_segments=32)
    # slab top sits 4 mm above the ground plane so the tyres sink into it
    slab = slab.translate((xmin + 60, -w / 2 + 60)).extrude(40).translate((0, 0, -36))
    stripes = union([box(xmin + 120 + i * 110, xmin + 170 + i * 110, w / 2 - 45, w / 2 - 25, 0, 10)
                     for i in range(int((xmax - xmin - 240) / 110) + 1)])
    label = lettering("MT-07", 720, -w / 2 + 28, 75, 16, 14)
    return Parts().add("dark", slab).add("paint", stripes).add("metal", label)


# --------------------------------------------------------------- export helpers

PIN_HOLES = [(720.0, 300.0), (930.0, 880.0)]  # (x, z) positions on the y=0 plane


def halves(m):
    """Split along y=0 with filament pin holes; each half lies on its cut face."""
    pins = union([ycyl((x, 0, z), pm(1.05), 2 * pm(3.5), 24) for x, z in PIN_HOLES])
    right = (m.trim_by_plane((0, 1, 0), 0.0) - pins).rotate((90, 0, 0))
    left = (m.trim_by_plane((0, -1, 0), 0.0) - pins).rotate((-90, 0, 0))
    return left, right


def wheel_halves(spec):
    w = wheel_core(spec).solid() - ycyl((0, 0, 0), pm(1.05), 600, 24)
    a = w.trim_by_plane((0, 1, 0), 0.0).rotate((90, 0, 0))
    b = w.trim_by_plane((0, -1, 0), 0.0).rotate((-90, 0, 0))
    return a, b


def rotor_parts(spec):
    """Each disc / sprocket laid flat with the collar facing up."""
    out = []
    for m in rotors(spec).solid().decompose():
        m = m - ycyl((0, 0, 0), pm(1.05), 600, 24)
        y0, y1 = m.bounding_box()[1], m.bounding_box()[4]
        # collar sits on the outer face, so put the inner (disc) face down
        out.append(m.rotate((90, 0, 0)) if y0 + y1 > 0 else m.rotate((-90, 0, 0)))
    return out


def lay_out(parts, gap):
    """Place parts side by side along x on the bed."""
    out, x = [], 0.0
    for m in parts:
        b = m.bounding_box()
        out.append(m.translate((x - b[0], -(b[1] + b[4]) / 2, -b[2])))
        x += b[3] - b[0] + gap
    return union(out)


def drop_to_bed(m):
    (x0, y0, z0, x1, y1, z1) = m.bounding_box()
    return m.translate((-(x0 + x1) / 2, -(y0 + y1) / 2, -z0))


def float32_clean(m):
    """Snap vertices to float32 (as STL stores them) and let manifold3d rebuild the
    mesh, so sub-micron slivers collapse cleanly instead of leaving bad edges."""
    mesh = m.to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3].astype(np.float32)
    snapped = Manifold(Mesh(vert_properties=v, tri_verts=np.asarray(mesh.tri_verts, np.uint32)))
    return snapped if snapped.status().name == "NoError" and not snapped.is_empty() else m


def write_stl(m, path):
    mesh = float32_clean(m).to_mesh()
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
    global SCALE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scale", type=float, default=18, help="scale denominator, e.g. 18 for 1:18")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "stl"))
    ap.add_argument("--preview", action="store_true", help="also render preview.png")
    args = ap.parse_args()
    SCALE = args.scale
    k = 1.0 / SCALE
    for sub in ("", "kit", "colour"):
        os.makedirs(os.path.join(args.out, sub), exist_ok=True)

    parts, holes = bike()
    drill = union(holes)
    solid = parts.solid() - drill
    full_parts = Parts().merge(parts).merge(display_base())  # base top is 4 mm above z=0
    full = full_parts.solid() - drill
    left, right = halves(solid)

    body_parts, _ = bike(wheels=False)
    body = (body_parts.solid() - drill
            - wheel_envelope(FRONT) - wheel_envelope(REAR) - axle_hole(FRONT) - axle_hole(REAR))
    kit_left, kit_right = halves(body)
    gap = pm(4)
    wheels = lay_out([*wheel_halves(FRONT), *wheel_halves(REAR)], gap)
    rotor_set = lay_out(rotor_parts(FRONT) + rotor_parts(REAR), gap)
    mirrors = lay_out([mirror_part().rotate((0, -90, 0)),
                       mirror_part().mirror((0, 1, 0)).rotate((0, -90, 0))], gap)

    def scaled(m):
        return drop_to_bed(m.scale((k, k, k)))

    outputs = {
        "mt07_full.stl": scaled(full),
        "mt07_half_left.stl": scaled(left),
        "mt07_half_right.stl": scaled(right),
        "mt07_mirrors.stl": scaled(mirrors),
        "kit/body_left.stl": scaled(kit_left),
        "kit/body_right.stl": scaled(kit_right),
        "kit/wheels.stl": scaled(wheels),
        "kit/discs_and_sprocket.stl": scaled(rotor_set),
    }
    # colour sections share one transform so they stay aligned in the slicer
    b = full.bounding_box()
    shift = (-(b[0] + b[3]) / 2, -(b[1] + b[4]) / 2, -b[2])
    for name, m in full_parts.sections().items():
        outputs[f"colour/{name}.stl"] = (m - drill).translate(shift).scale((k, k, k))

    for name, m in outputs.items():
        assert m.status().name == "NoError", (name, m.status())
        write_stl(m, os.path.join(args.out, name))
        b = m.bounding_box()
        print(f"{name:28s} {b[3]-b[0]:6.1f} x {b[4]-b[1]:5.1f} x {b[5]-b[2]:5.1f} mm  "
              f"{m.volume()/1000:5.1f} cm3  {len(m.decompose())} piece(s)")
    if args.preview:
        from preview import render
        shown = parts.sections()
        shown["metal"] = shown["metal"] + union(mirrors_on_bike())
        render([(m.scale((k, k, k)), COLOURS[g]) for g, m in shown.items()],
               os.path.join(os.path.dirname(args.out), "preview.png"))


if __name__ == "__main__":
    main()
