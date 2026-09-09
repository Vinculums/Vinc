#!/usr/bin/env python3
"""Emit one .dc.html artboard per chapter from ch/*.json + engine.js.

Never hand-edit out/*.dc.html. The engine is shared so the chapters cannot
drift apart the way Ch1/Ch3/Ch7 did; regenerate instead.

  python -X utf8 build.py            build every chapter
  python -X utf8 build.py --check    prose-volume report only
"""
import json, os, re, sys, glob

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

# Prose volume band, per the owner's instruction that chapters were too thin.
# Measured against the reference artboards: their middle beats ran 35-60 words,
# which is what read as too little.
MIN_WORDS, MAX_WORDS = 80, 150
MIN_PARAS = 3

FONT = "Pretendard, ui-sans-serif, system-ui, sans-serif"
INK, PAPER, META, LINK = "#17171A", "#F2F2F0", "#82838D", "#4A4F8C"
SOFT = "#55565E"


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def words(beat):
    return sum(len(re.findall(r"\S+", p)) for p in beat.get("paras", []))


def section(b, i, last):
    kind = b.get("kind", "beat")
    parts = []
    if kind == "open":
        minh, pad = "92vh", "96px 0 64px"
    elif kind == "close":
        minh, pad = "70vh", "64px 0 128px"
    else:
        minh, pad = "92vh", "64px 0"
    sty = (f"min-height: {minh}; display: flex; flex-direction: column; "
           f"justify-content: center; max-width: 32rem; padding: {pad}; "
           f"transition: opacity .3s cubic-bezier(.22,1,.36,1);")
    parts.append(f'      <section data-step="{i}" data-screen-label="{b.get("label", i)}" style="{sty}">')
    if b.get("eyebrow"):
        parts.append(f'        <p style="margin: 0 0 16px; font-size: 13px; color: {META}; '
                     f'font-variant-numeric: tabular-nums;">{esc(b["eyebrow"])}</p>')
    if b.get("h"):
        if kind == "open":
            parts.append(f'        <h1 style="margin: 0 0 28px; font-size: 36px; line-height: 1.15; '
                         f'font-weight: 600; letter-spacing: -0.022em; text-wrap: pretty; '
                         f'word-break: keep-all;">{esc(b["h"])}</h1>')
        else:
            parts.append(f'        <h2 style="margin: 0 0 20px; font-size: 24px; line-height: 1.25; '
                         f'font-weight: 600; letter-spacing: -0.01em; text-wrap: pretty;">{esc(b["h"])}</h2>')
    for p in b.get("paras", []):
        parts.append(f'        <p style="margin: 0 0 12px; font-size: 17px; line-height: 1.6;">{esc(p)}</p>')
    if b.get("claim"):
        parts.append(f'        <p style="margin: 28px 0 0; font-size: 21px; line-height: 1.45; '
                     f'font-weight: 500;">{esc(b["claim"])}</p>')
    if b.get("nextLabel"):
        parts.append(f'        <a href="{b["nextHref"]}" style="font-size: 15px; line-height: 1.5; '
                     f'color: {SOFT};">{esc(b["nextLabel"])}</a>')
    parts.append("      </section>")
    return "\n".join(parts)


PROPS = ('{&#39;textSide&#39;:{&#39;editor&#39;:&#39;enum&#39;,&#39;options&#39;:[&#39;left&#39;,&#39;right&#39;],'
         '&#39;default&#39;:&#39;SIDE&#39;,&#39;tsType&#39;:&#39;\\&#39;left\\&#39; | \\&#39;right\\&#39;&#39;,'
         '&#39;section&#39;:&#39;Layout&#39;},&#39;triggerLine&#39;:{&#39;editor&#39;:&#39;range&#39;,&#39;default&#39;:0.5,'
         '&#39;min&#39;:0.3,&#39;max&#39;:0.7,&#39;step&#39;:0.05,&#39;tsType&#39;:&#39;number&#39;,&#39;section&#39;:&#39;Scroll&#39;}}')


def build(ch, engine):
    # `no` is the part-local chapter number the index calls canonical;
    # `n` is the document slot, which the index warns is not a chapter number
    n, part = ch.get("no", ch["n"]), ch["part"]
    plabel = "Closing" if part == 5 else f"Part {part}"
    secs = []
    for i, b in enumerate(ch["beats"]):
        secs.append(section(b, i, i == len(ch["beats"]) - 1))
    body = "\n\n".join(secs)

    data = [
        f"const PART = {part}, CHAPTER = {json.dumps(ch['title'])};",
        f"const MARKS = {json.dumps(ch.get('marks', []))};",
        f"const ANCHORS = {json.dumps(ch.get('anchors', []))};",
        f"const LINES = {json.dumps(ch.get('lines', []))};",
        f"const FIGURES = {json.dumps(ch.get('figures', []))};",
    ]
    stage = "\n".join(ch.get("stage", []))
    cls = [
        "class Component extends BookChapter {",
        f"  PART = PART; MARKS = MARKS; ANCHORS = ANCHORS; LINES = LINES; FIGURES = FIGURES;",
        f"  LAST = {len(ch['beats']) - 1};",
        f"  VIEWBOX = {json.dumps(ch.get('viewBox', '0 0 640 640'))};",
        f"  SIDE = {json.dumps(ch.get('side', 'left'))};",
    ]
    if ch.get("focusAt") is not None:
        cls.append(f"  FOCUS_AT = {ch['focusAt']};")
    if ch.get("focusScrub") is False:
        cls.append("  FOCUS_SCRUB = false;")
    # part registers: the chapter declares one and the engine's default
    # stage() dispatches on it (Part 1 conveyor, Part 2 pair, Part 3 ring)
    for key, name in (("conveyor", "CONVEYOR"), ("pair", "PAIR"), ("ring", "RING"), ("hub", "HUB"), ("ground", "GROUND")):
        if ch.get(key):
            cls.append(f"  {name} = {json.dumps(ch[key])};")
    if stage:
        cls.append(stage)
    cls.append("}")

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
  <style>
    html, body {{ margin: 0; background: {PAPER}; color: {INK}; }}
    a {{ color: {INK}; text-decoration: none; }}
    a:hover {{ color: {LINK}; }}
    [data-step] p {{ text-wrap: pretty; word-break: keep-all; }}
    @media (max-width: 900px) {{
      [data-layout="main"] {{ grid-template-columns: minmax(0, 1fr) !important; }}
      [data-layout="canvas"] {{ order: 0 !important; height: 46vh !important; padding: 56px 24px 8px !important; background: {PAPER}; z-index: 1; }}
      [data-layout="text"] {{ order: 1 !important; padding: 0 24px !important; }}
      [data-layout="text"] [data-step] {{ min-height: 60vh !important; }}
    }}
    @media (prefers-reduced-motion: reduce) {{ [data-step], [data-mark], [data-line], [data-fig], [data-wash], [data-spoke] {{ transition: none !important; }} }}
  </style>
</helmet>
<div style="font-family: {FONT}; background: {PAPER}; color: {INK}; min-height: 100vh; -webkit-font-smoothing: antialiased;">
  <header style="position: fixed; top: 0; left: 0; right: 0; height: 52px; display: flex; align-items: center; justify-content: space-between; padding: 0 32px; font-size: 13px; color: {SOFT}; z-index: 2; pointer-events: none;">
    <span style="font-weight: 500;">Vinc Book</span>
    <span style="font-variant-numeric: tabular-nums;">{plabel}, Chapter {n}</span>
  </header>
  <main data-side="{{{{ side }}}}" data-layout="main" style="display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: start;">
    <div data-layout="text" style="padding: 0 48px 0 max(48px, 7vw); order: {{{{ textOrder }}}};">

{body}

    </div>
    <div data-layout="canvas" style="position: sticky; top: 0; height: 100vh; display: flex; align-items: center; justify-content: center; padding: 64px 48px; box-sizing: border-box; order: {{{{ canvasOrder }}}};">
      {{{{ canvas }}}}
    </div>
  </main>
</div>
</x-dc>
<script data-dc-script data-props='{PROPS.replace("SIDE", ch.get("side", "left"))}'>
{engine}

{chr(10).join(data)}

{chr(10).join(cls)}
</script>
</body>
</html>
"""


def main():
    engine = open(os.path.join(HERE, "engine.js"), encoding="utf-8").read()
    files = sorted(glob.glob(os.path.join(HERE, "ch", "*.json")))
    if not files:
        print("no chapter data in ch/"); return 1
    os.makedirs(OUT, exist_ok=True)
    check = "--check" in sys.argv
    bad = 0
    for f in files:
        ch = json.load(open(f, encoding="utf-8"))
        mids = [b for b in ch["beats"] if b.get("kind", "beat") == "beat"]
        wc = [words(b) for b in mids]
        pc = [len(b.get("paras", [])) for b in mids]
        thin = [i for i, w in enumerate(wc) if w < MIN_WORDS]
        fat = [i for i, w in enumerate(wc) if w > MAX_WORDS]
        few = [i for i, c in enumerate(pc) if c < MIN_PARAS]
        avg = sum(wc) / max(1, len(wc))
        flag = ""
        if thin: flag += f" THIN{thin}"
        if fat: flag += f" FAT{fat}"
        if few: flag += f" PARAS{few}"
        if flag: bad += 1
        slot = ch["n"] if isinstance(ch["n"], str) else f"{ch['n']:02d}"
        print(f"ch{slot} part{ch['part']} beats={len(ch['beats'])} "
              f"avg={avg:.0f}w range={min(wc) if wc else 0}-{max(wc) if wc else 0} "
              f"marks={len(ch.get('marks', []))} lines={len(ch.get('lines', []))}{flag}")
        if not check:
            name = ch.get("file") or f"Ch{slot}.dc.html"
            # newline="\n" or Windows text mode writes CRLF, which the canvas
            # keeps verbatim and which broke the render probe's line matching
            with open(os.path.join(OUT, name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(build(ch, engine))
    print(f"--- {len(files)} chapters, {bad} outside the prose band "
          f"({MIN_WORDS}-{MAX_WORDS} words, >={MIN_PARAS} paragraphs per middle beat)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
