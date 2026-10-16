"""The pictures of the README, made from a real paper in the reader.

    python3 tools/readme_assets.py [paper id] [stills]   (default 2512.23916; its static page must exist: python3 -m textfold export <id>)

Writes docs/assets/: logo.svg, zoom.gif and zoom-zh.gif (Full to Topic and back), notes.png (a highlight and a note),
keep.png (the highlight kept at Takeaway); with "stills", only the two pictures. Each frame of the animations is taken with the zoom held at an exact
position (the reader's scrubTo), so the motion is even whatever the machine. Uses a fresh browser profile: notes made
for the pictures stay in it. Needs playwright (with Chrome), Pillow and ffmpeg.
"""
import math
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "assets"
PID = sys.argv[1] if len(sys.argv) > 1 else "2512.23916"
STILLS = "stills" in sys.argv[2:]
PAGE = (ROOT / "out" / PID / "index.html").as_uri()

LOGO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="96" height="96">
  <rect x="2" y="3" width="16" height="2.4" rx="1.2" fill="#6B5CE7"/>
  <rect x="2" y="8.8" width="11" height="2.4" rx="1.2" fill="#6B5CE7" fill-opacity=".4"/>
  <rect x="2" y="14.6" width="5" height="2.4" rx="1.2" fill="#E08A3C"/>
</svg>
"""


def ease(x):
    return x * x * (3 - 2 * x)


def path():
    """The zoom positions of the animation: a pause at Full, out to Topic, a pause, back to Full, a pause."""
    out = [4.0] * 10
    out += [4 - 4 * ease(i / 44) for i in range(1, 45)]
    out += [0.0] * 16
    out += [4 * ease(i / 44) for i in range(1, 45)]
    out += [4.0] * 8
    return out


def hold(pg, p):
    pg.evaluate(f"__reader.scrubTo({p})")
    t0 = time.time()
    while time.time() - t0 < 0.6 and abs(pg.evaluate("__reader.pos") - p) > 0.002:
        time.sleep(0.02)
    time.sleep(0.05)


def gif(browser, lang, name, size=(1180, 740), width=1000):
    pg = browser.new_page(viewport={"width": size[0], "height": size[1]}, device_scale_factor=2)
    pg.goto(f"{PAGE}#lang={lang}")
    pg.mouse.move(size[0] - 4, size[1] // 2)   # out of the column: no hover
    time.sleep(4.0)                            # the keyframes are prepared while the page is still
    with tempfile.TemporaryDirectory() as tmp:
        for k, p in enumerate(path()):
            hold(pg, p)
            pg.screenshot(path=f"{tmp}/f{k:03d}.png")
        pg.evaluate("__reader.letGo()")
        pal = "split[a][b];[a]palettegen=max_colors=160:stats_mode=full[p];[b][p]paletteuse=dither=sierra2_4a"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", "18", "-i", f"{tmp}/f%03d.png",
                        "-vf", f"scale={width}:-1:flags=lanczos,{pal}", str(OUT / name)], check=True)
    pg.close()
    print(name, f"{(OUT / name).stat().st_size / 1e6:.1f} MB")


SELECT = """([skip, n]) => {   // n words (outside formulas) of the paragraph with the most words on screen
  const wordsOf = (tx) => { const out = [], w = document.createTreeWalker(tx, NodeFilter.SHOW_TEXT);
    for (let t = w.nextNode(); t; t = w.nextNode()) { if (t.parentElement.closest('math, .katex')) continue;
      for (const m of t.data.matchAll(/[A-Za-z]{3,}/g)) out.push([t, m.index, m.index + m[0].length]); } return out; };
  const cands = [...document.querySelectorAll('#doc .u')].filter((u) => { const r = u.getBoundingClientRect(); return r.top > 90 && r.top < innerHeight - 250; });
  const u = cands.sort((a, b) => wordsOf(b._tx).length - wordsOf(a._tx).length)[0], words = wordsOf(u._tx);
  const A = words[skip], B = words[skip + n - 1], r = document.createRange();
  r.setStart(A[0], A[1]); r.setEnd(B[0], B[2]); getSelection().removeAllRanges(); getSelection().addRange(r);
  document.getElementById('doc').dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
  return u.dataset.u;
}"""


def shot(pg, name, clip, width):
    pg.screenshot(path=str(OUT / name), clip=clip)
    im = Image.open(OUT / name)
    im.resize((width, round(im.height * width / im.width)), Image.LANCZOS).save(OUT / name, optimize=True)
    print(name, im.size, "->", width)


SPAN = """(u) => { const el = document.getElementById(u), b = el.getBoundingClientRect();
  const ms = [...el.querySelectorAll(':scope > .side, :scope > .lside')].map((m) => m.getBoundingClientRect()).filter((m) => m.width);
  return [b.top, b.bottom, Math.max(0, Math.min(b.left, ...ms.map((m) => m.left)) - 24), Math.min(innerWidth, Math.max(b.right, ...ms.map((m) => m.right)) + 24)]; }"""


def notes(browser):
    pg = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    pg.goto(f"{PAGE}#lang=en")
    time.sleep(2.0)
    uid = pg.evaluate(SELECT, [0, 4])
    time.sleep(0.3)
    pg.click('.selbar button[data-act="hl"]')
    pg.evaluate(SELECT, [15, 6])
    time.sleep(0.3)
    pg.click('.selbar button[data-act="note"]')
    time.sleep(0.3)
    pg.keyboard.type("Test this against the scaling results in Section 5.")
    time.sleep(0.9)
    pg.keyboard.press("Escape")
    pg.mouse.move(1430, 450)
    time.sleep(1.0)
    r = pg.evaluate(SPAN, uid)   # the paragraph with its margins: the note on the left, the links on the right
    top = max(0, r[0] - 120)
    shot(pg, "notes.png", {"x": r[2], "y": top, "width": r[3] - r[2], "height": min(900 - 130 - top, r[1] - top + 60)}, 1000)
    pg.click(".keepbtn")
    pg.keyboard.press("4")
    time.sleep(2.0)
    r = pg.evaluate("(u) => { const b = document.getElementById(u).getBoundingClientRect(); return [b.top, b.bottom]; }", uid)
    top = max(0, r[0] - 160)
    shot(pg, "keep.png", {"x": 300, "y": top, "width": 1110, "height": min(900 - top, r[1] - top + 120)}, 1000)
    pg.close()


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "logo.svg").write_text(LOGO, encoding="utf-8")
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome")
        if not STILLS:
            gif(b, "en", "zoom.gif")
            gif(b, "zh", "zoom-zh.gif")
        notes(b)
        b.close()
