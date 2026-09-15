#!/usr/bin/env python3
"""Look at the built pages the way a reader does. Not shipped; a dev aid.

  python -m http.server 8995 -d out-local        (in another shell)
  python -X utf8 shots.py [Main Ch15 Ch17]

For each page: scroll to chosen (beat, p) positions with the spy's own
arithmetic, check window.book.state agrees, screenshot. Then the beat sheets
whole, a reduced-motion check (p must read 1 mid-beat) and a frame-rate
reading for the canvas field while scrolling. Writes out-local/shots/*.png.
The context fixes device_scale_factor=1, so the OS display ratio of this
machine (0.8) does not move the scroll positions.
"""
import os, sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BOOK_BASE", "http://127.0.0.1:8995")   # another tree may hold 8995; serve this one elsewhere and say so
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out-local", "shots")
PAGES = {
    "Main": [(0, .4), (0, .9), (1, .35), (1, .7), (1, .98), (2, .8), (3, .9), (7, .9)],   # the open beat already reads p 0.33 at scroll 0
    "Ch15": [(0, .6), (2, .15), (2, .45), (2, .7), (2, .98), (3, .5), (3, .98), (4, .9)],
    "Ch17": [(0, .6), (1, .6), (2, .15), (2, .5), (2, .98), (3, .3), (3, .98), (4, .9), (7, .8)],
}
SCROLL = """([s, p]) => {
  const els = [...document.querySelectorAll('[data-step]')], line = innerHeight * 0.5;
  const top = el => el.getBoundingClientRect().top + scrollY, t0 = top(els[s]);
  const span = s + 1 < els.length ? top(els[s + 1]) - t0 : els[s].getBoundingClientRect().height * 0.5;
  scrollTo(0, Math.round(t0 + span * p - line));
}"""
FPS = """() => new Promise(res => {
  let n = 0; const t0 = performance.now();
  const tick = () => { scrollBy(0, 3); n++; if (performance.now() - t0 < 2000) requestAnimationFrame(tick); else res(n / 2); };
  requestAnimationFrame(tick);
})"""


def main():
    only = sys.argv[1:] or list(PAGES)
    os.makedirs(OUT, exist_ok=True)
    bad = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        page = ctx.new_page()
        for name in only:
            page.goto(f"{BASE}/{name}.html")
            page.wait_for_function("() => window.book && document.querySelector('[data-layout=canvas] svg')")
            page.wait_for_timeout(400)
            for s, p in PAGES.get(name, []):
                page.evaluate(SCROLL, [s, p])
                page.wait_for_timeout(350)
                st = page.evaluate("() => window.book.state")
                ok = st["step"] == s and abs(st["p"] - p) < 0.08
                bad += 0 if ok else 1
                path = os.path.join(OUT, f"{name}-{s}-{int(p * 100):02d}.png")
                page.screenshot(path=path)
                print(f"{'ok ' if ok else 'BAD'} {name} beat {s} p={p:.2f} -> state {st['step']} {st['p']:.2f}  {os.path.basename(path)}")
            resp = page.goto(f"{BASE}/sheets/{name}.html?pp=1")
            if resp and resp.ok:
                page.wait_for_timeout(1200)
                page.screenshot(path=os.path.join(OUT, f"sheet-{name}.png"), full_page=True)
                print(f"ok  sheet {name}")
        # a phone width: the stage in the top band, the text scrolling over it
        nctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
        np_ = nctx.new_page()
        for name, s, p in (("Main", 1, .7), ("Ch17", 2, .98), ("Ch15", 2, .98)):
            np_.goto(f"{BASE}/{name}.html")
            np_.wait_for_function("() => window.book")
            np_.wait_for_timeout(400)
            np_.evaluate(SCROLL, [s, p])
            np_.wait_for_timeout(350)
            st = np_.evaluate("() => window.book.state")
            ok = st["step"] == s
            bad += 0 if ok else 1
            np_.screenshot(path=os.path.join(OUT, f"narrow-{name}-{s}.png"))
            print(f"{'ok ' if ok else 'BAD'} narrow {name} beat {s} -> state {st['step']} {st['p']:.2f}")
        # reduced motion: mid-beat, p must read 1
        rctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1, reduced_motion="reduce")
        rp = rctx.new_page()
        rp.goto(f"{BASE}/Ch17.html")
        rp.wait_for_function("() => window.book")
        rp.wait_for_timeout(400)
        rp.evaluate(SCROLL, [2, .5])
        rp.wait_for_timeout(350)
        st = rp.evaluate("() => window.book.state")
        ok = st["p"] == 1 and st["step"] == 2
        bad += 0 if ok else 1
        rp.screenshot(path=os.path.join(OUT, "Ch17-reduced-2.png"))
        print(f"{'ok ' if ok else 'BAD'} reduced motion: beat {st['step']} p={st['p']}")
        # frame rate while scrolling through the field
        page.goto(f"{BASE}/Ch17.html")
        page.wait_for_function("() => window.book")
        page.wait_for_timeout(400)
        page.evaluate(SCROLL, [2, .1])
        page.wait_for_timeout(350)
        fps = page.evaluate(FPS)
        print(f"{'ok ' if fps >= 50 else 'LOW'} field fps while scrolling: {fps:.0f}  (under 50: WebGL points, see the ponytail note in drawField)")
        browser.close()
    print(f"--- {bad} checks failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
