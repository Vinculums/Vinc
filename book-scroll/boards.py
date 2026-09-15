#!/usr/bin/env python3
"""Keyframe boards for the three prototype chapters: still compositions of the
stage at chosen beats, designed before any motion exists. Not shipped; a design
aid the owner reviews in a browser.

  python -X utf8 boards.py        -> out-local/boards/ch01.html, ch15.html, ch17.html, index.html

Composition system (vinc.canon.book-motion-grammar, revised 2026-09-16):
  one focal element and one operation per frame; the stage vocabulary is point,
  line, mass and type, never a box; nothing persists from the previous frame
  unless the frame says so; every word on the stage is the chapter's own.
Stage: 1440 x 900. Prose column x 312..880 under a paper veil. Free area x 880..1440,
safe area x 944..1376 / y 64..836, anchor (1160, 450), thirds x 1088 / 1232, y 321 / 579.
"""
import json, os, random

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out-local", "boards")
W, H = 1440, 900
INK, SLATE, GRAIN, PAPER, FOCUS, TINT, META, SOFT = "#17171A", "#747790", "#D1D2D6", "#F2F2F0", "#7E86D3", "#E4E6F6", "#82838D", "#55565E"
DUST = "#C4C5CA"                      # a particle: between grain and slate so 1.6px stays visible
FONT = "Pretendard, ui-sans-serif, system-ui, sans-serif"
MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace"
AX, AY = 1160, 450                    # the anchor
SX0, SX1, SY0, SY1 = 944, 1376, 64, 836


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load(slot):
    return json.load(open(os.path.join(HERE, "ch", f"ch{slot}.json"), encoding="utf-8"))


# ---------- primitives (strings of SVG) ----------
def pt(x, y, r=4, fill=INK):
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}"/>'

def ring(x, y, r, stroke=FOCUS, sw=1.2):
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="none" stroke="{stroke}" stroke-width="{sw}"/>'

def line(x0, y0, x1, y1, stroke=SLATE, sw=1):
    return f'<path d="M{x0:.1f} {y0:.1f} L{x1:.1f} {y1:.1f}" stroke="{stroke}" stroke-width="{sw}" fill="none" stroke-linecap="round"/>'

def path(d, stroke=SLATE, sw=1):
    return f'<path d="{d}" stroke="{stroke}" stroke-width="{sw}" fill="none" stroke-linecap="round"/>'

def mass(x, y, w, h, fill=INK):
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}"/>'

def dust(x, y, fill=DUST, s=1.6):
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{s}" height="{s}" fill="{fill}"/>'

def text(x, y, s, size=18, fill=INK, weight=400, anchor="start", mono=False, ls=None):
    fam = MONO if mono else FONT
    lsp = f' letter-spacing="{ls}"' if ls else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{fam}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}"{lsp}>{esc(s)}</text>')

def wrap(s, n):
    out, cur = [], ""
    for w in s.split(" "):
        if len(cur) + len(w) + 1 > n and cur:
            out.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur: out.append(cur)
    return out

def block(x, y, lines, size, lh, fill=INK, weight=400, anchor="start"):
    return "".join(text(x, y + i * lh, l, size, fill, weight, anchor, ls="-0.01em" if size >= 24 else None) for i, l in enumerate(lines))


# ---------- the page around the stage ----------
def frame(ch, beat, stage_svg, cap, note=None):
    b = ch["beats"][beat]
    part = ch["part"]
    plabel = "Closing" if part == 5 else f"Part {part}"
    col = []
    col.append(f'<rect x="312" y="0" width="568" height="{H}" fill="{PAPER}" fill-opacity="0.84"/>')   # the quiet zone
    y = 300
    if b.get("eyebrow"):
        col.append(text(336, y, b["eyebrow"], 13, META)); y += 44
    if b.get("h"):
        hl = wrap(b["h"], 34 if b.get("kind") == "open" else 40)
        size, lh = (36, 42) if b.get("kind") == "open" else (24, 30)
        col.append(block(336, y, hl, size, lh, INK, 600)); y += lh * len(hl) + 26
    for _ in range(3):                                          # the prose stands here on the page; the board keeps it out of the way
        col.append(f'<rect x="336" y="{y - 12}" width="{random.Random(y).randint(300, 500)}" height="2" fill="{GRAIN}"/>'); y += 27
    if b.get("claim"):
        cl = wrap(b["claim"], 46)
        y += 18
        col.append(block(336, y, cl, 21, 30, INK, 500))
    header = (text(32, 31, "Vinc Book", 13, SOFT, 500) +
              text(W - 32, 31, f"{plabel}, Chapter {ch.get('no', ch['n'])}", 13, SOFT, 400, "end"))
    svg = (f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg" style="display:block;background:{PAPER}">'
           f'<rect width="{W}" height="{H}" fill="{PAPER}"/>{stage_svg}{"".join(col)}{header}</svg>')
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


RULES = ("one focal element and one operation per frame · the stage draws with point, line, mass and type, never a box · "
         "nothing persists from the previous frame unless named · every word on the stage is the chapter's own · "
         "ink for the subject, slate for the picture, grain for what has receded, one focus colour per chapter")


# ---------- Chapter 1: the mass on a horizon, the point, the carriers ----------
def ch01():
    ch = load("01")
    HZ = 579                                                        # the horizon on the lower third
    frames = []
    # 1.0 the mass
    s = line(0, HZ, W, HZ, INK) + mass(1172, HZ - 300, 56, 300)
    frames.append(frame(ch, 0, s, "Ch 1 · 1.0 · One mass stands on a horizon. The camera pushes in, slowly, and nothing else happens.",
                        "Cinematic: the black rectangle and the empty paper are the whole frame. No object is depicted; the mass is only a mass."))
    # 1.1 one point, one cut
    s = line(0, HZ, W, HZ, INK) + mass(1172, HZ - 300, 56, 300) + pt(1140, HZ, 4)
    s += path("M1140 575 C 1130 380, 1080 220, 1000 215", SLATE) + line(978, 215, 1022, 215, INK, 3)
    frames.append(frame(ch, 1, s, "Ch 1 · 1.1 · A point appears at the foot of the mass, rises on an arc, and at the top it is a horizontal line. That is the cut.",
                        "The bone and the ship are not drawn. The vertical mass and the point that turns horizontal carry the film's grammar: scale, one arc, one cut."))
    # 1.2 passed hand to hand
    s = line(0, HZ, W, HZ, INK) + mass(1172, HZ - 300, 56, 300, GRAIN)
    for i in range(7):
        s += pt(960 + i * 62, HZ, 4, INK if i < 3 else SLATE if i < 5 else GRAIN)
    frames.append(frame(ch, 2, s, "Ch 1 · 1.2 · Points light along the horizon one after another. The mass has receded to grain.",
                        "Fire passed hand to hand is a row of points lit in sequence: three in ink, two arriving, two not yet."))
    # 1.3 travel: the record outlives its maker
    s = line(0, HZ, W, HZ, INK) + mass(932, HZ - 300, 56, 300, GRAIN)
    for i in range(7):
        s += pt(720 + i * 62, HZ, 4, GRAIN)
    s += pt(1232, HZ, 5, INK)
    frames.append(frame(ch, 3, s, "Ch 1 · 1.3 · The stage travels left by 240. What was drawn passes under the text and turns to grain. One point stays, alone, in ink.",
                        "Travel is the operation. The carriers leave; the record remains."))
    # 1.5 / 1.6 three readings and their relations
    s = line(0, HZ, W, HZ, GRAIN) + mass(932, HZ - 300, 56, 300, GRAIN)
    for i in range(7):
        s += pt(720 + i * 62, HZ, 4, GRAIN)
    s += pt(1232, HZ, 5, INK)
    ends = [(1130, 400), (1232, 380), (1334, 400)]
    for ex, ey in ends:
        s += line(1232, HZ - 6, ex, ey + 4, SLATE) + pt(ex, ey, 3, SLATE)
    s += path("M1130 400 Q 1181 340 1232 380", SLATE) + path("M1232 380 Q 1283 340 1334 400", SLATE)
    frames.append(frame(ch, 6, s, "Ch 1 · 1.6 · From the one record, three thin readings leave at three angles; two arcs relate their ends.",
                        "Interpretation is lines, not marks: the same point read three ways, and the relations between the readings."))
    # 1.7 pull back: the chain of carriers
    k = 0.7
    hz = AY + (HZ - AY) * k
    s = line(0, hz, W, hz, INK)                                   # the mass has left the frame; the chapter has moved on from it
    names = ["Cave walls", "clay tablets", "paper", "books", "libraries", "computers", "the internet", "AI Agent"]
    for i, nm in enumerate(names):
        x = 736 + i * 91
        last = i == len(names) - 1
        s += pt(x, hz, 5 if last else 3.5, FOCUS if last else INK) + text(x, hz + 26, nm, 11, INK if last else META, 400, "middle", True)
    frames.append(frame(ch, 7, s, "Ch 1 · 1.7 · The camera pulls back to 0.7. The horizon carries the carriers as a sparse row; the newest one, at the right, takes the chapter's one focus colour.",
                        "Chronology: the words are the chapter's own list of tools for connecting memory. The first two sit under the text, in the fade."))
    return page("Vinc Book · Keyframe boards · Chapter 1 · Cinematic into Chronology", frames, RULES)


# ---------- Chapter 15: the sentence itself breaks ----------
def ch15():
    ch = load("15")
    S0 = ch["beats"][0]["claim"]
    S1 = ch["beats"][3]["paras"][0].split(". ")[0] + "."
    L0 = wrap(S0, 27)
    frames = []
    # 15.0 the sentence
    s = block(SX0, 330, L0, 28, 38, INK, 500)
    frames.append(frame(ch, 0, s, "Ch 15 · 15.0 · The chapter's first claim stands on the stage as type. Nothing else.",
                        "Fragmentation begins with an intact sentence set well: the words are the material."))
    # 15.1 the original stays
    small = block(SX0, 700, wrap(S0, 60), 14, 20, GRAIN)
    s = block(SX0, 300, L0, 28, 38, INK, 500) + small
    frames.append(frame(ch, 1, s, "Ch 15 · 15.1 · The original settles to the foot of the stage in grain, intact; a working copy stays above.",
                        "Preservation is a second layer, not a shadow: the original has to be fully recoverable, so it is always on the stage."))
    # 15.2 a: the sentence breaks into fragments
    frags = ["So processing", "a document", "isn't about replacing", "the original text", "with a graph:",
             "it's about preserving", "the original", "while adding", "a reusable structure", "on top of it."]
    rnd = random.Random(15)
    pos = []
    for i, f in enumerate(frags):
        cx = (SX0 + 20) if i % 2 == 0 else 1176
        cy = 150 + (i // 2) * 108
        pos.append((cx + rnd.randint(-16, 16), cy + rnd.randint(-12, 12)))
    s = small + "".join(text(x, y, f, 18, INK, 500) for (x, y), f in zip(pos, frags))
    frames.append(frame(ch, 2, s, "Ch 15 · 15.2 · The copy breaks where its clauses break. Ten fragments take their places on the stage, still as type, with air between them.",
                        "No boxes. A fragment is a few words with space around them; the spacing is the structure."))
    # 15.2 b: fragments become nodes
    s = small + pt(AX, AY, 7, TINT) + ring(AX, AY, 7, FOCUS, 1.5)
    for (x, y), f in zip(pos, frags):
        px, py = x + 6, y - 6
        s += line(px, py, AX, AY, SLATE) + pt(px, py, 3.5, INK) + text(px, py + 18, f, 11, META, 400, "start", True)
    frames.append(frame(ch, 2, s, "Ch 15 · 15.2 · Each fragment contracts to a point and its words drop to a small label. Hairlines run to one centre, the concept.",
                        "Convergence: the words do not vanish, they become labels of what was extracted; the centre carries the chapter's one focus colour."))
    # 15.3 the second document lands on existing nodes
    s = small + block(SX0, 100, wrap(S1, 44), 18, 24, INK, 500) + pt(AX, AY, 7, TINT) + ring(AX, AY, 7, FOCUS, 1.5)
    for i, ((x, y), f) in enumerate(zip(pos, frags)):
        px, py = x + 6, y - 6
        s += line(px, py, AX, AY, SLATE) + pt(px, py, 3.5, INK) + text(px, py + 18, f, 11, META, 400, "start", True)
        if i in (1, 2, 6, 8):
            s += ring(px, py, 8, FOCUS, 1.2)
    frames.append(frame(ch, 3, s, "Ch 15 · 15.3 · A second sentence arrives whole at the top. Its pieces land on four points that already exist: a ring around each, no new point.",
                        "Reuse over creation: the evidence on an existing node grows; nothing is remade from scratch."))
    # 15.4 pull back
    k = 0.7
    def sc(x, y): return AX + (x - AX) * k, AY + (y - AY) * k
    s = block(SX0, 640, wrap(S0, 60), 14, 20, GRAIN) + block(SX0, 740, wrap(S1, 60), 14, 20, GRAIN)
    s += pt(AX, AY, 7 * k, TINT) + ring(AX, AY, 7 * k, FOCUS, 1.5)
    for i, ((x, y), f) in enumerate(zip(pos, frags)):
        px, py = sc(x + 6, y - 6)
        s += line(px, py, AX, AY, SLATE) + pt(px, py, 3, INK)
        if i in (1, 2, 6, 8):
            s += ring(px, py, 6.5, FOCUS, 1.2)
    frames.append(frame(ch, 4, s, "Ch 15 · 15.4 · The camera pulls back to 0.7. Both originals lie intact at the foot; the structure stands above them.",
                        "Document to node does not compress a document into a node: the sentences are still there, the structure is on top of them."))
    return page("Vinc Book · Keyframe boards · Chapter 15 · Fragmentation", frames, RULES)


# ---------- Chapter 17: the field ----------
def ch17():
    ch = load("17")
    phrase = ch["beats"][2]["claim"].split(", ")[-1]              # as long as they sit close together in semantic space.
    last = ch["beats"][7]["paras"][2]                               # It's a coordinate for finding memory.
    others = [ch["beats"][i]["h"] for i in (1, 3, 4, 5, 6, 7)]
    frames = []
    def scatter(n, seed, fill=DUST, x0=0, x1=W, y0=0, y1=H):
        r = random.Random(seed)
        return "".join(dust(r.uniform(x0, x1), r.uniform(y0, y1), fill) for _ in range(n))
    def cloud(n, seed, cx, cy, sx, sy, fill="#9A9CAE"):
        r = random.Random(seed)
        return "".join(dust(r.gauss(cx, sx), r.gauss(cy, sy), fill) for _ in range(n))
    # 17.0 the field
    s = scatter(1400, 17)
    frames.append(frame(ch, 0, s, "Ch 17 · 17.0 · A field of particles over the whole stage, under the text too. No shape, no label.",
                        "The space before meaning: even, undirected, faint."))
    # 17.1 four chunks
    r = random.Random(171)
    four = [(r.uniform(SX0 + 40, SX1 - 40), r.uniform(SY0 + 80, SY1 - 80)) for _ in range(4)]
    s = scatter(1400, 17) + "".join(pt(x, y, 3, INK) for x, y in four)
    frames.append(frame(ch, 1, s, "Ch 17 · 17.1 · Four particles are in ink: the chunks that have to exist before anything can be found.",
                        "Chunks are not boxes. They are four points in the same field as everything else."))
    # 17.2 a: gathering
    s = scatter(500, 17) + cloud(900, 172, AX, 480, 120, 46) + "".join(pt(x, y, 3, INK) for x, y in [(AX - 40, 470), (AX + 30, 492), (AX - 90, 498), (AX + 80, 466)])
    s += text(SX0, 140, phrase, 18, INK, 500)
    frames.append(frame(ch, 2, s, "Ch 17 · 17.2 · The clause stands at the top. Below it the particles gather into one dense region; the four chunks sit inside it.",
                        "Gathering is the operation. The phrase is type with air above the cloud, never inside a box."))
    # 17.2 b: the space appears (pull back)
    k = 0.6
    s = scatter(400, 17) + cloud(900, 172, AX, AY, 120 * k, 46 * k)
    r = random.Random(173)
    import math
    for i, h in enumerate(others):                                  # six regions on an ellipse, evenly spaced, kept inside the safe area
        a = -1.2 + i * (2 * math.pi / len(others)) + r.uniform(-0.18, 0.18); rad = r.uniform(230, 290)
        cx, cy = AX + rad * 0.72 * math.cos(a), AY + rad * 0.62 * math.sin(a)
        cx = min(max(cx, SX0 + 110), SX1 - 110)
        lab = h if len(h) <= 30 else wrap(h, 30)
        s += cloud(60, 200 + i, cx, cy, 14, 8, "#B0B2BF")
        s += text(cx, cy + 30, lab, 11, META, 400, "middle", True) if isinstance(lab, str) else block(cx, cy + 30, lab, 11, 14, META, 400, "middle")
    s += text(SX0, 140, phrase, 18, INK, 500)
    frames.append(frame(ch, 2, s, "Ch 17 · 17.2 · The camera pulls back to 0.6. The cloud is small now, and six other regions appear around it, each named by one of the chapter's headings.",
                        "The space is the picture: neighbourhoods of meaning, not a diagram of them."))
    # 17.3 re-embed
    g = "".join(line(x, SY0, x, SY1, GRAIN) for x in range(SX0, SX1 + 1, 36)) + "".join(line(SX0, y, SX1, y, GRAIN) for y in range(SY0, SY1 + 1, 36))
    s = f'<g opacity="0.6">{g}</g>' + scatter(1400, 174, DUST, SX0 - 120, SX1 + 120, SY0 - 40, SY1 + 40)
    frames.append(frame(ch, 3, s, "Ch 17 · 17.3 · The dimension changes: a new grid, and every particle has left its place and not yet found the next. No labels survive the move.",
                        "Re-embedding is shown as loss of place, not as a stack of steps. The stack diagram stays in the prose."))
    # 17.7 a coordinate for finding memory
    s = scatter(600, 17, GRAIN) + block(SX0, 430, wrap(last, 26), 28, 38, INK, 500)
    s += pt(1300, 560, 4, INK) + line(1280, 560, 1320, 560, INK) + line(1300, 540, 1300, 580, INK)
    frames.append(frame(ch, 7, s, "Ch 17 · 17.7 · The field has receded to grain. One sentence from the prose stands in type, and one point with a crosshair: a coordinate.",
                        "The boundary between vectors and meaning is typographic, not a box: the words carry it, the point is what a vector is."))
    return page("Vinc Book · Keyframe boards · Chapter 17 · Field", frames, RULES)


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
    print("boards:", ", ".join(pages), "in", OUT)


if __name__ == "__main__":
    main()
