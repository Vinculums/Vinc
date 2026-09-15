#!/usr/bin/env python3
"""Keyframe boards for the three prototype chapters: still compositions of the
stage at chosen beats, designed before any motion exists. Not shipped; a design
aid the owner reviews in a browser.

  python -X utf8 boards.py        -> out-local/boards/ch01.html, ch15.html, ch17.html, index.html

Composition system (vinc.canon.book-motion-grammar, revised 2026-09-16):
  every form has volume: the stage is a three-dimensional space seen by one
  camera (yaw, pitch, distance), masses are slabs with three faces and a cast
  shadow, points are shaded spheres, rows recede in depth, type stands on
  planes in that space, particles carry depth in size and ink;
  one focal element and one operation per frame; point, line, mass and type,
  never a box; nothing persists unless the frame says so; every word on the
  stage is the chapter's own. The prose column is the page's own, unchanged.
Stage: 1440 x 900. Prose column x 312..880 under a paper veil. Free area x 880..1440,
anchor (1160, 450) is the world origin on the ground plane.
"""
import json, math, os, random

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out-local", "boards")
W, H = 1440, 900
INK, SLATE, GRAIN, PAPER, FOCUS, TINT, META, SOFT = "#17171A", "#747790", "#D1D2D6", "#F2F2F0", "#7E86D3", "#E4E6F6", "#82838D", "#55565E"
FONT = "Pretendard, ui-sans-serif, system-ui, sans-serif"
MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace"
AX, AY = 1160, 450                    # where the world origin lands on the stage

# ---------- the camera ----------
YAW, PITCH, DIST = math.radians(-32), math.radians(6), 1700
ZN, ZF = -320, 1000                   # depth range mapped onto ink -> grain


def proj(x, y, z, k0=1.0, ax=AX, ay=AY):
    """World (x right, y up, z toward the back) to stage. Returns (sx, sy, k, depth)."""
    cy, sy = math.cos(YAW), math.sin(YAW)
    x1, z1 = x * cy + z * sy, -x * sy + z * cy
    cp, sp = math.cos(PITCH), math.sin(PITCH)
    y2, z2 = y * cp + z1 * sp, -y * sp + z1 * cp
    k = DIST / (DIST + z2) * k0
    return ax + x1 * k, ay - y2 * k, k, z2

HORIZON = AY - DIST * math.tan(PITCH)  # where the ground plane meets the sky


def mix(a, b, t):
    t = max(0.0, min(1.0, t))
    A = [int(a[i:i + 2], 16) for i in (1, 3, 5)]; B = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(A[i] + (B[i] - A[i]) * t):02x}" for i in range(3))

def tone(depth, near=INK, far=GRAIN):
    return mix(near, far, (depth - ZN) / (ZF - ZN))

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def load(slot):
    return json.load(open(os.path.join(HERE, "ch", f"ch{slot}.json"), encoding="utf-8"))

def wrap(s, n):
    out, cur = [], ""
    for w in s.split(" "):
        if len(cur) + len(w) + 1 > n and cur:
            out.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur: out.append(cur)
    return out


# ---------- 2D primitives ----------
def text(x, y, s, size=18, fill=INK, weight=400, anchor="start", mono=False, ls=None, extra=""):
    fam = MONO if mono else FONT
    lsp = f' letter-spacing="{ls}"' if ls else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{fam}" font-size="{size:.2f}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}"{lsp}{extra}>{esc(s)}</text>')

def block(x, y, lines, size, lh, fill=INK, weight=400, anchor="start"):
    return "".join(text(x, y + i * lh, l, size, fill, weight, anchor, ls="-0.01em" if size >= 24 else None) for i, l in enumerate(lines))

def hair(pts, stroke=SLATE, sw=1.0, op=1.0):
    d = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)
    return f'<path d="{d}" stroke="{stroke}" stroke-width="{sw}" fill="none" stroke-linecap="round" stroke-linejoin="round" opacity="{op}"/>'

def poly(pts, fill, op=1.0):
    return f'<polygon points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="{fill}" opacity="{op}"/>'


# ---------- 3D primitives ----------
DEFS = ('<defs>'
        '<radialGradient id="sph" cx="36%" cy="32%" r="68%"><stop offset="0" stop-color="#8A8B94"/><stop offset=".55" stop-color="#2E2F36"/><stop offset="1" stop-color="#17171A"/></radialGradient>'
        '<radialGradient id="sphs" cx="36%" cy="32%" r="68%"><stop offset="0" stop-color="#C9CAD2"/><stop offset=".55" stop-color="#8A8CA0"/><stop offset="1" stop-color="#747790"/></radialGradient>'
        '<radialGradient id="sphg" cx="36%" cy="32%" r="68%"><stop offset="0" stop-color="#EDEDF0"/><stop offset=".6" stop-color="#D9DADF"/><stop offset="1" stop-color="#D1D2D6"/></radialGradient>'
        '<radialGradient id="sphf" cx="36%" cy="32%" r="68%"><stop offset="0" stop-color="#C6CBF2"/><stop offset=".55" stop-color="#8F97DA"/><stop offset="1" stop-color="#7E86D3"/></radialGradient>'
        '</defs>')

def sphere(x, y, z, r, mat="sph", k0=1.0, shadow=True):
    """A shaded ball of radius r resting at height y (its centre is at y + r) with a soft shadow on the ground."""
    sx, sy, k, d = proj(x, y + r, z, k0)
    gx, gy, gk, _ = proj(x, 0, z, k0)
    s = ""
    if shadow:
        s += f'<ellipse cx="{gx + 0.9 * r * gk:.1f}" cy="{gy:.1f}" rx="{1.7 * r * gk:.1f}" ry="{0.45 * r * gk:.1f}" fill="{GRAIN}" opacity="0.7"/>'
    s += f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="{r * k:.2f}" fill="url(#{mat})"/>'
    return s

def slab(x, z, w, d, h, faces=(INK, "#3D3E47", "#6B6D78"), k0=1.0, shadow=True):
    """A block standing on the ground, centred at (x, z): three visible faces and a cast shadow."""
    x0, x1, z0, z1 = x - w / 2, x + w / 2, z - d / 2, z + d / 2
    c = {n: proj(*p, k0) for n, p in {
        "flb": (x0, 0, z0), "frb": (x1, 0, z0), "brb": (x1, 0, z1), "blb": (x0, 0, z1),
        "flt": (x0, h, z0), "frt": (x1, h, z0), "brt": (x1, h, z1), "blt": (x0, h, z1)}.items()}
    F = [("front", ["flb", "frb", "frt", "flt"], faces[0]), ("right", ["frb", "brb", "brt", "frt"], faces[1]),
         ("back", ["brb", "blb", "blt", "brt"], faces[0]), ("left", ["blb", "flb", "flt", "blt"], faces[1]),
         ("top", ["flt", "frt", "brt", "blt"], faces[2])]
    s = ""
    if shadow:                                                  # light from the upper left front: the shadow falls right and back
        dx, dz = 0.42 * h, 0.30 * h
        sh = [proj(x1, 0, z0, k0), proj(x1, 0, z1, k0), proj(x1 + dx, 0, z1 + dz, k0), proj(x1 + dx, 0, z0 + dz, k0)]
        s += poly([(p[0], p[1]) for p in sh], GRAIN, 0.6)
    vis = []
    for name, ids, fill in F:
        pts = [(c[i][0], c[i][1]) for i in ids]
        area = sum(pts[i][0] * pts[(i + 1) % 4][1] - pts[(i + 1) % 4][0] * pts[i][1] for i in range(4))
        if area < 0:                                           # facing the camera (screen y points down)
            vis.append((sum(c[i][3] for i in ids) / 4, pts, fill))
    for _, pts, fill in sorted(vis, key=lambda v: -v[0]):
        s += poly(pts, fill)
    return s

def rod(x, y, z, length, r, k0=1.0):
    """A short horizontal bar lying along world x at height y: the point turned into a line."""
    a = proj(x - length / 2, y, z, k0); b = proj(x + length / 2, y, z, k0)
    return hair([(a[0], a[1]), (b[0], b[1])], INK, 2 * r * a[2]) + f'<circle cx="{b[0]:.1f}" cy="{b[1]:.1f}" r="{r * b[2]:.2f}" fill="#3D3E47"/>'

def curve3(pts3, stroke=SLATE, sw=1.0, k0=1.0, op=1.0):
    return hair([proj(*p, k0)[:2] for p in pts3], stroke, sw, op)

def arc3(a, b, lift, n=24, k0=1.0, stroke=SLATE, sw=1.0):
    pts = []
    for i in range(n + 1):
        t = i / n
        pts.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t + lift * 4 * t * (1 - t), a[2] + (b[2] - a[2]) * t))
    return curve3(pts, stroke, sw, k0)

def plane(o, u, v, k0=1.0):
    """An affine frame for type standing in space: local x along u, local y (down) along v."""
    ox, oy, _, d = proj(*o, k0)
    ux, uy, _, _ = proj(o[0] + u[0], o[1] + u[1], o[2] + u[2], k0)
    vx, vy, _, _ = proj(o[0] + v[0], o[1] + v[1], o[2] + v[2], k0)
    return f'matrix({ux - ox:.4f} {uy - oy:.4f} {vx - ox:.4f} {vy - oy:.4f} {ox:.2f} {oy:.2f})', d

UP_PLANE = ((1, 0, 0), (0, -1, 0))                            # a sheet standing upright along x
GROUND = ((1, 0, 0), (0, 0, 1))                                # a sheet lying on the ground, receding

def type3(o, lines, size, lh, fill=INK, weight=400, orient=UP_PLANE, k0=1.0, anchor="start"):
    m, d = plane(o, *orient, k0)
    return f'<g transform="{m}">{block(0, 0, lines, size, lh, fill, weight, anchor)}</g>'

def dust3(x, y, z, k0=1.0, near=SLATE, far=GRAIN, base=1.7):
    sx, sy, k, d = proj(x, y, z, k0)
    s = base * k
    return f'<rect x="{sx:.1f}" y="{sy:.1f}" width="{s:.2f}" height="{s:.2f}" fill="{tone(d, near, far)}"/>'

def horizon(k0=1.0):
    return hair([(0, HORIZON), (W, HORIZON)], mix(INK, GRAIN, 0.35), 1)


# ---------- the page around the stage: the real column, unchanged ----------
def column(ch, beat):
    b = ch["beats"][beat]
    kind = b.get("kind", "beat")
    rows = []                                                   # (height, svg-at-y) laid out like build.py's section
    if b.get("eyebrow"):
        rows.append((13 + 16, lambda y: text(336, y + 13, b["eyebrow"], 13, META)))
    if b.get("h"):
        if kind == "open":
            hl = wrap(b["h"], 30); rows.append((42 * len(hl) + 28, lambda y, hl=hl: block(336, y + 32, hl, 36, 42, INK, 600)))
        else:
            hl = wrap(b["h"], 42); rows.append((30 * len(hl) + 20, lambda y, hl=hl: block(336, y + 22, hl, 24, 30, INK, 600)))
    for p in b.get("paras", []):
        pl = wrap(p, 62); rows.append((27 * len(pl) + 12, lambda y, pl=pl: block(336, y + 18, pl, 17, 27, INK, 400)))
    if b.get("claim"):
        cl = wrap(b["claim"], 50); rows.append((30 * len(cl) + 28, lambda y, cl=cl: block(336, y + 28 + 18, cl, 21, 30, INK, 500)))
    total = sum(h for h, _ in rows)
    y = max(72, AY - total / 2)
    s = f'<rect x="312" y="0" width="568" height="{H}" fill="{PAPER}" fill-opacity="0.84"/>'
    for h, fn in rows:
        s += fn(y); y += h
    return s

def frame(ch, beat, stage_svg, cap, note=None):
    part = ch["part"]
    plabel = "Closing" if part == 5 else f"Part {part}"
    header = (text(32, 31, "Vinc Book", 13, SOFT, 500) +
              text(W - 32, 31, f"{plabel}, Chapter {ch.get('no', ch['n'])}", 13, SOFT, 400, "end"))
    svg = (f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg" style="display:block;background:{PAPER}">'
           f'{DEFS}<rect width="{W}" height="{H}" fill="{PAPER}"/>{stage_svg}{column(ch, beat)}{header}</svg>')
    n = note and f'<p class="note">{esc(note)}</p>' or ""
    return f'<figure><figcaption>{esc(cap)}</figcaption>{svg}{n}</figure>'

def page(title, frames, rules):
    css = ("body{margin:0;background:#E9E9E6;font-family:" + FONT + ";color:" + INK + "}"
           "main{max-width:1180px;margin:0 auto;padding:40px 24px 96px}"
           "h1{font-size:22px;font-weight:600;margin:0 0 6px}"
           ".rules{font-family:" + MONO + ";font-size:12px;color:" + SOFT + ";line-height:1.7;margin:0 0 32px;white-space:pre-wrap}"
           "figure{margin:0 0 40px}figcaption{font-family:" + MONO + ";font-size:12px;color:" + SOFT + ";margin:0 0 8px}"
           "svg{box-shadow:0 1px 0 rgba(0,0,0,.06),0 8px 28px rgba(0,0,0,.08)}"
           ".note{font-size:13px;color:" + SOFT + ";margin:8px 0 0;line-height:1.5}")
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{esc(title)}</title><style>{css}</style></head>'
            f'<body><main><h1>{esc(title)}</h1><p class="rules">{esc(rules)}</p>{"".join(frames)}</main></body></html>')

RULES = ("every form has volume: one camera, slabs with faces and shadows, shaded spheres, rows that recede, type on planes in space, particles with depth · "
         "one focal element and one operation per frame · point, line, mass and type, never a box · nothing persists unless named · "
         "every word on the stage is the chapter's own · the prose column is the page's, untouched")


# ---------- Chapter 1: the slab on the ground, the ball, the carriers ----------
def ch01():
    ch = load("01")
    fr = []
    # 1.0 the mass
    s = horizon() + slab(0, 0, 56, 40, 300)
    fr.append(frame(ch, 0, s, "Ch 1 · 1.0 · One slab stands on the ground, three faces and a shadow. The camera pushes in, slowly, and nothing else happens.",
                    "Cinematic: a monumental mass, a low camera, the empty ground. No object is depicted; the slab is only a slab."))
    # 1.1 one ball, one cut
    s = horizon() + slab(0, 0, 56, 40, 300) + sphere(-92, 0, -6, 6)
    s += arc3((-92, 6, -6), (-270, 235, -110), 120) + rod(-270, 235, -110, 46, 2.2)
    fr.append(frame(ch, 1, s, "Ch 1 · 1.1 · A ball rests at the foot of the slab, rises on an arc through the space, and at the top of its flight it is a horizontal bar. That is the cut.",
                    "The bone and the ship are not drawn. A vertical mass and a ball that becomes a horizontal line carry the film's grammar: scale, one arc, one cut."))
    # 1.2 passed hand to hand
    s = horizon() + slab(0, 0, 56, 40, 300, faces=(mix(INK, GRAIN, .72), mix(INK, GRAIN, .8), mix(INK, GRAIN, .86)), shadow=False)
    for i in range(7):
        x, z = -330 + i * 92, -130                                # in front of the slab, along a line that recedes to the right
        s += sphere(x, 0, z, 6, "sph" if i < 3 else "sphs" if i < 5 else "sphg")
    fr.append(frame(ch, 2, s, "Ch 1 · 1.2 · Balls light along a line that runs back into the space, one after another: three in ink, two arriving, two not yet. The slab has receded.",
                    "Fire passed hand to hand is a row in depth, not a row on a line: each carrier stands a little farther back than the last."))
    # 1.3 travel: the record outlives its maker
    T = -320
    s = horizon() + slab(T, 0, 56, 40, 300, faces=(mix(INK, GRAIN, .78), mix(INK, GRAIN, .84), mix(INK, GRAIN, .9)), shadow=False)
    for i in range(7):
        s += sphere(T - 330 + i * 92, 0, -130, 6, "sphg", shadow=False)
    s += sphere(150, 0, 20, 7)
    fr.append(frame(ch, 3, s, "Ch 1 · 1.3 · The whole ground travels left by 320. What was drawn passes under the text and fades. One ball stays alone in the free space, in ink.",
                    "Travel is the operation. The carriers leave; the record remains where the reader can see it."))
    # 1.6 three readings and their relations
    s = horizon() + slab(T, 0, 56, 40, 300, faces=(mix(INK, GRAIN, .82), mix(INK, GRAIN, .88), mix(INK, GRAIN, .92)), shadow=False)
    for i in range(7):
        s += sphere(T - 330 + i * 92, 0, -130, 6, "sphg", shadow=False)
    s += sphere(150, 0, 20, 7)
    ends = [(20, 210, -90), (150, 250, 60), (290, 210, -70)]
    for e in ends:
        s += curve3([(150, 14, 20), e]) + sphere(e[0], e[1] - 4, e[2], 4, "sphs", shadow=False)
    s += arc3(ends[0], ends[1], 40) + arc3(ends[1], ends[2], 40)
    fr.append(frame(ch, 6, s, "Ch 1 · 1.6 · From the one record, three thin readings rise into the space at three angles; two arcs relate their ends.",
                    "Interpretation is lines in depth, not marks: the same ball read three ways, and the relations between the readings."))
    # 1.7 pull back: the chain of carriers
    k = 0.8
    names = ["Cave walls", "clay tablets", "paper", "books", "libraries", "computers", "the internet", "AI Agent"]
    s = horizon(k)
    for i, nm in enumerate(names):
        x, z = -640 + i * 128, -40 + i * 42
        last = i == len(names) - 1
        s += sphere(x, 0, z, 10 if last else 7, "sphf" if last else "sph", k)
        gx, gy, gk, _ = proj(x, 0, z, k)
        s += text(gx, gy + 28, nm, 12, INK if last else META, 400, "middle", True)
    fr.append(frame(ch, 7, s, "Ch 1 · 1.7 · The camera pulls back to 0.8. The carriers stand in a long row that runs from under the text into the depth on the right; the newest takes the chapter's one focus colour.",
                    "Chronology in space: the words are the chapter's own list of tools for connecting memory."))
    return page("Vinc Book · Keyframe boards · Chapter 1 · Cinematic into Chronology", fr, RULES)


# ---------- Chapter 15: the sentence itself breaks, in space ----------
def ch15():
    ch = load("15")
    S0 = ch["beats"][0]["claim"]
    S1 = ch["beats"][3]["paras"][0].split(". ")[0] + "."
    L0 = wrap(S0, 27)
    fr = []
    STAND = (-190, 330, 60)                                       # where the working copy stands, a sheet upright in the space
    LIE = (-200, 0, 70)                                           # where the original lies on the ground
    # 15.0 the sentence
    s = horizon() + type3(STAND, L0, 27, 37, INK, 500)
    fr.append(frame(ch, 0, s, "Ch 15 · 15.0 · The chapter's first claim stands in the space as a sheet of type. Nothing else.",
                    "Fragmentation begins with an intact sentence set well, standing where the reader can walk around it."))
    # 15.1 the original lies down, intact
    s = horizon() + type3(STAND, L0, 27, 37, INK, 500) + type3(LIE, wrap(S0, 60), 15, 21, mix(INK, GRAIN, .55), 400, GROUND)
    fr.append(frame(ch, 1, s, "Ch 15 · 15.1 · The original lies down on the ground, intact and receding; the working copy stays standing.",
                    "Preservation is a second layer in space: the original is always there, on the ground, fully recoverable."))
    # 15.2 a: the sheet breaks into fragments at different depths
    frags = ["So processing", "a document", "isn't about replacing", "the original text", "with a graph:",
             "it's about preserving", "the original", "while adding", "a reusable structure", "on top of it."]
    rnd = random.Random(15)
    pos = []
    for i, f in enumerate(frags):
        pos.append((-170 + (i % 2) * 230 + rnd.randint(-20, 20), 340 - (i // 2) * 62 + rnd.randint(-8, 8), -160 + rnd.randint(0, 340)))
    lie = type3(LIE, wrap(S0, 60), 15, 21, mix(INK, GRAIN, .55), 400, GROUND)
    s = horizon() + lie
    for p, f in zip(pos, frags):
        d = proj(*p)[3]
        s += type3(p, [f], 18, 24, tone(d, INK, mix(INK, GRAIN, .6)), 500)
    fr.append(frame(ch, 2, s, "Ch 15 · 15.2 · The standing sheet breaks where its clauses break. Ten fragments hang in the space at different depths, nearer ones darker, with air between them.",
                    "No boxes. A fragment is a few words standing in space; depth and spacing are the structure."))
    # 15.2 b: fragments become spheres and converge
    C = (0, 150, 40)
    s = horizon() + lie + sphere(C[0], C[1] - 9, C[2], 9, "sphf")
    for p, f in zip(pos, frags):
        c = (p[0] + 60, p[1] - 8, p[2])
        s += curve3([c, (C[0], C[1], C[2])], SLATE, 0.9) + sphere(c[0], c[1] - 4, c[2], 4, "sph", shadow=False)
        sx, sy, k, d = proj(*c)
        s += text(sx, sy + 16, f, 11 * k, META, 400, "middle", True)
    fr.append(frame(ch, 2, s, "Ch 15 · 15.2 · Each fragment contracts to a ball and its words drop to a small label. Hairlines run through the space to one centre, the concept, which carries the chapter's focus colour.",
                    "Convergence in depth: the words do not vanish, they become labels of what was extracted."))
    # 15.3 the second document lands on existing balls
    s = horizon() + lie + type3((-190, 470, 260), wrap(S1, 42), 18, 24, INK, 500) + sphere(C[0], C[1] - 9, C[2], 9, "sphf")
    for i, (p, f) in enumerate(zip(pos, frags)):
        c = (p[0] + 60, p[1] - 8, p[2])
        s += curve3([c, (C[0], C[1], C[2])], SLATE, 0.9) + sphere(c[0], c[1] - 4, c[2], 4, "sph", shadow=False)
        sx, sy, k, d = proj(*c)
        s += text(sx, sy + 16, f, 11 * k, META, 400, "middle", True)
        if i in (1, 2, 6, 8):
            s += f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="{9 * k:.2f}" fill="none" stroke="{FOCUS}" stroke-width="1.2"/>'
    fr.append(frame(ch, 3, s, "Ch 15 · 15.3 · A second sentence stands further back, whole. Its pieces land on four balls that already exist: a ring around each, no new ball.",
                    "Reuse over creation: the evidence on an existing node grows; nothing is remade from scratch."))
    # 15.4 pull back
    k = 0.7
    s = horizon(k) + type3(LIE, wrap(S0, 60), 15, 21, mix(INK, GRAIN, .55), 400, GROUND, k) + type3((-230, 0, 330), wrap(S1, 60), 15, 21, mix(INK, GRAIN, .55), 400, GROUND, k)
    s += sphere(C[0], C[1] - 9, C[2], 9, "sphf", k)
    for i, (p, f) in enumerate(zip(pos, frags)):
        c = (p[0] + 60, p[1] - 8, p[2])
        s += curve3([c, (C[0], C[1], C[2])], SLATE, 0.9, k) + sphere(c[0], c[1] - 4, c[2], 4, "sph", k, shadow=False)
        if i in (1, 2, 6, 8):
            sx, sy, kk, _ = proj(*c, k)
            s += f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="{9 * kk:.2f}" fill="none" stroke="{FOCUS}" stroke-width="1.2"/>'
    fr.append(frame(ch, 4, s, "Ch 15 · 15.4 · The camera pulls back to 0.7. Both originals lie intact on the ground; the structure stands above them in the space.",
                    "Document to node does not compress a document into a node: the sentences are still there, the structure is on top of them."))
    return page("Vinc Book · Keyframe boards · Chapter 15 · Fragmentation", fr, RULES)


# ---------- Chapter 17: the field in three dimensions ----------
def ch17():
    ch = load("17")
    phrase = ch["beats"][2]["claim"].split(", ")[-1]
    last = ch["beats"][7]["paras"][2]
    others = [ch["beats"][i]["h"] for i in (1, 3, 4, 5, 6, 7)]
    fr = []
    def box_dust(n, seed, k0=1.0, ext=(760, 520, 760), yo=0):
        r = random.Random(seed)
        return "".join(dust3(r.uniform(-ext[0], ext[0]), yo + r.uniform(0, ext[1]), r.uniform(-ext[2], ext[2]), k0) for _ in range(n))
    def cloud3(n, seed, c, sd, k0=1.0, near=INK, far=SLATE):
        r = random.Random(seed)
        return "".join(dust3(r.gauss(c[0], sd[0]), r.gauss(c[1], sd[1]), r.gauss(c[2], sd[2]), k0, near, far) for _ in range(n))
    # 17.0 the field
    s = box_dust(1500, 17)
    fr.append(frame(ch, 0, s, "Ch 17 · 17.0 · A field of particles fills the space over the whole stage, under the text too; nearer ones larger and darker, farther ones small and faint.",
                    "The space before meaning: even, undirected, deep."))
    # 17.1 four chunks
    r = random.Random(171)
    four = [(r.uniform(-280, 280), r.uniform(60, 380), r.uniform(-260, 260)) for _ in range(4)]
    s = box_dust(1500, 17) + "".join(sphere(x, y, z, 5, "sph", shadow=False) for x, y, z in four)
    fr.append(frame(ch, 1, s, "Ch 17 · 17.1 · Four balls in ink hang in the field: the chunks that have to exist before anything can be found.",
                    "Chunks are not boxes. They are four bodies in the same space as everything else."))
    # 17.2 a: gathering
    C = (0, 190, 0)
    s = box_dust(500, 17) + cloud3(1100, 172, C, (130, 46, 130))
    s += "".join(sphere(C[0] + dx, C[1] + dy, C[2] + dz, 5, "sph", shadow=False) for dx, dy, dz in [(-60, 10, -20), (40, -20, 30), (-20, 28, 60), (70, 0, -50)])
    s += type3((-300, 330, -60), [phrase], 18, 24, INK, 500)
    fr.append(frame(ch, 2, s, "Ch 17 · 17.2 · The clause stands on a sheet above the space. Below it the particles gather into one dense body; the four chunks are inside it.",
                    "Gathering is the operation. The phrase is type on a plane with air above the cloud, never inside a box."))
    # 17.2 b: the space appears (pull back)
    k = 0.6
    s = box_dust(500, 17, k) + cloud3(1100, 172, C, (130, 46, 130), k)
    spots = [(300, 70, -170), (330, 280, 320), (-60, 330, 420), (-300, 90, 160), (-160, 30, -300), (110, 390, -290)]
    for i, (h, (cx, cy, cz)) in enumerate(zip(others, spots)):        # six bodies at chosen depths, none on top of another
        s += cloud3(70, 200 + i, (cx, cy, cz), (26, 12, 26), k, SLATE, GRAIN)
        gx, gy, gk, _ = proj(cx, cy - 30, cz, k)
        s += block(gx, gy + 14, wrap(h, 26), 11, 14, META, 400, "middle")
    s += type3((-300, 330, -60), [phrase], 18, 24, INK, 500, UP_PLANE, k)
    fr.append(frame(ch, 2, s, "Ch 17 · 17.2 · The camera pulls back to 0.6. The body is small now, and six other bodies stand around it at their own depths, each named by one of the chapter's headings.",
                    "The space is the picture: neighbourhoods of meaning at different distances, not a diagram of them."))
    # 17.3 re-embed: a new lattice, every particle between places
    s = ""
    step, E = 173, 519                                            # a sparse lattice: four cells a side
    for a in range(-E, E + 1, step):
        for b in range(-E, E + 1, step):
            s += curve3([(a, 0, b), (a, 519, b)], GRAIN, 0.8, 1.0, 0.4)
            s += curve3([(a, b + E, -E), (a, b + E, E)], GRAIN, 0.8, 1.0, 0.32) if 0 <= b + E <= 519 else ""
            s += curve3([(-E, b + E, a), (E, b + E, a)], GRAIN, 0.8, 1.0, 0.32) if 0 <= b + E <= 519 else ""
    s += box_dust(1400, 174, 1.0, (560, 520, 560))
    fr.append(frame(ch, 3, s, "Ch 17 · 17.3 · The dimension changes: a new lattice stands in the space, and every particle has left its place and not yet found the next. No labels survive the move.",
                    "Re-embedding is shown as loss of place in a new frame, not as a stack of steps. The stack diagram stays in the prose."))
    # 17.7 a coordinate for finding memory
    s = box_dust(700, 17, 1.0, (760, 520, 760)).replace(SLATE, GRAIN)
    s += type3((-330, 330, 40), wrap(last, 26), 27, 37, INK, 500)
    P = (240, 120, -80)
    s += sphere(P[0], P[1] - 5, P[2], 5, "sph", shadow=False)
    s += curve3([(P[0] - 40, P[1], P[2]), (P[0] + 40, P[1], P[2])], INK, 1) + curve3([(P[0], P[1] - 40, P[2]), (P[0], P[1] + 40, P[2])], INK, 1) + curve3([(P[0], P[1], P[2] - 40), (P[0], P[1], P[2] + 40)], INK, 1)
    fr.append(frame(ch, 7, s, "Ch 17 · 17.7 · The field has receded to grain. One sentence from the prose stands in the space, and one ball with its three axes: a coordinate.",
                    "The boundary between vectors and meaning is typographic and spatial, not a box: the words carry it, the axes are what a vector is."))
    return page("Vinc Book · Keyframe boards · Chapter 17 · Field", fr, RULES)


def main():
    os.makedirs(OUT, exist_ok=True)
    pages = {"ch01.html": ch01(), "ch15.html": ch15(), "ch17.html": ch17()}
    for name, html in pages.items():
        with open(os.path.join(OUT, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(html)
    idx = ('<!doctype html><meta charset="utf-8"><body style="font-family:system-ui;padding:24px;background:#E9E9E6">'
           '<h1 style="font-size:20px">Vinc Book · keyframe boards</h1><p>' +
           " · ".join(f'<a href="{n}">{n[:-5]}</a>' for n in pages) + "</p></body>")
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(idx)
    print("boards:", ", ".join(pages), "in", OUT, f"(horizon at y={HORIZON:.0f})")


if __name__ == "__main__":
    main()
