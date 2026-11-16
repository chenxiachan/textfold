"""A research blog read as fetched (no script run): its interactive figures and its formulas. An interactive figure that
is a page of its own in an <iframe> (al-folio's and Distill's blogs) was left out; it is kept in its place now, as a
link the reader draws in a sandboxed frame. A formula written in the text for MathJax or KaTeX to draw ($$...$$) came
out with its _ escaped (\\sigma\\_i), and ran into the paragraph after it; it is kept as written, a paragraph of its
own. $...$ is a formula only where the page's own settings say so. No network: the page is given.

A page sent from the browser has no <iframe>: its snapshot has each frame where the page shows it, as what the frame
showed with its scripts out, and its interactive figures were read as a still of their labels and numbers (a reader's
bug report, 2026-11-16, on the blog these tests copy). Each is the figure it was now, in either reading.

Run from the repo root:
    python3 -m unittest discover tests
"""
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from textfold import adapt, store, webpage
from textfold.parse import parse

FILL = ("Agents in a debate copy their peers, and a small bias in each of them can become a consensus of all of them "
        "when they talk for long enough, which is what the model below predicts round after round. ")


def page(body, head=""):
    return f"<html><head><title>Innocent agents</title>{head}</head><body><article><h1>Innocent agents</h1>{body}</article></body></html>"


BLOG = page(f"""<p>{FILL}</p>
<iframe class="post-embed" src="/assets/html/sim.html" title="Interactive simulator: temperature and consensus" style="width:100%; height:720px; border:0" loading="lazy"></iframe>
<p><em>You set the temperature; each run lasts thirty rounds.</em></p>
<p>{FILL}Writing it out, the ratio is a hyperbolic tangent:</p> $$ \\langle \\sigma_i \\rangle = 2p_i - 1 = \\tanh(f_i/T) $$ <p>{FILL}After the formula.</p>
<p>{FILL}A display in its paragraph, \\[ m_{{t+1}} = \\tanh(\\lambda m_t) \\] and the words after it.</p>
<iframe src="https://tracker.example.net/pixel" width="1" height="1"></iframe>
<iframe src="http://localhost:8080/admin" height="400"></iframe>
<iframe src="javascript:alert(1)" height="400"></iframe>
<p>{FILL}It costs $5 and $10 to run, and a_b stays a_b in words.</p>""")

MATHJAX = '<script>window.MathJax = { tex: { inlineMath: [["$", "$"], ["\\\\(", "\\\\)"]] } };</script>'


def read(html, url="https://blog.example.org/2026/innocent/"):
    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(store, "DATA", Path(tmp)), \
            mock.patch.object(webpage, "_get", lambda u: (html.encode(), url, "utf-8")), mock.patch.object(webpage.local, "prefetch", lambda srcs: {}), \
            mock.patch.object(webpage.local.Images, "_read", side_effect=OSError("no network in the tests")):
        pid = webpage.register(url)
        webpage.fetch(pid)
        d = store.pdir(pid)
        return (d / "source.md").read_text(encoding="utf-8"), (d / "source.html").read_text(encoding="utf-8")


class Figures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md, cls.html = read(BLOG)

    def test_an_iframe_of_another_page_is_kept_in_its_place(self):
        links = re.findall(r'<a class="pf-embed pf-h-(\d+)" href="([^"]+)" title="([^"]*)"', self.html)
        self.assertEqual(links, [("720", "https://blog.example.org/assets/html/sim.html", "Interactive simulator: temperature and consensus")])
        at, cap = self.md.index("pf-embed"), self.md.index("You set the temperature")
        self.assertLess(self.md.index(FILL.strip()[:40]), at)
        self.assertLess(at, cap)   # (where it was: after the first paragraph, before its caption)

    def test_it_is_a_figure_of_the_text(self):
        doc = parse(self.html, {"id": "web-x", "title": "Innocent agents"})
        figs = [b for b in doc["blocks"] if b.get("k") == "fig" and "pf-embed" in b.get("html", "")]
        self.assertEqual(len(figs), 1)

    def test_a_tracker_this_computer_and_a_script_are_not_framed(self):
        self.assertNotIn("tracker.example.net", self.html)
        self.assertNotIn("localhost", self.html)
        self.assertNotIn("javascript:", self.html)

    def test_a_display_formula_is_kept_as_written_a_paragraph_of_its_own(self):
        self.assertIn("\n\n$$\n\\langle \\sigma_i \\rangle = 2p_i - 1 = \\tanh(f_i/T)\n$$\n\n", self.md)
        self.assertNotIn("\\_", self.md.split("It costs")[0])
        self.assertIn("\n\n$$\nm_{t+1} = \\tanh(\\lambda m_t)\n$$\n\n", self.md)
        self.assertIn("and the words after it.", self.md)

    def test_dollars_are_money_unless_the_page_says_otherwise(self):
        words = self.md[self.md.index("It costs"):]
        self.assertNotIn("TFTEX", words)
        self.assertRegex(words, r"\\?\$5 and \\?\$10")


class Dollars(unittest.TestCase):
    def test_a_page_whose_settings_make_dollars_formulas(self):
        md, _ = read(page(f"<p>{FILL}Each agent $i$ adds the opinions $\\sigma_j = \\pm 1$ of its peers, weighted by $\\lambda$.</p><p>{FILL}</p>", MATHJAX))
        self.assertIn("$\\sigma_j = \\pm 1$", md)
        self.assertNotIn("\\_", md)

    def test_without_them_a_dollar_is_a_dollar(self):
        md, _ = read(page(f"<p>{FILL}A run costs $5 and the next $6, a_b in words.</p><p>{FILL}</p>"))
        self.assertNotIn("TFTEX", md)
        self.assertIn("costs", md)


PANELS = page(f"""<p>{FILL}</p>
<figure class="post-fig row"><img src="/img/a.png" alt="(a) Sparse"> <img src="/img/b.png" alt="(b) Top-p"> <img src="/img/c.png" alt="(c) Confidence"> <img src="/img/d.png" alt="(d) Sycophancy"></figure>
<p><em>Each panel varies one knob and reports the final consensus.</em></p>
<figure><img src="data:image/gif;base64,R0lGODdhAQABAPAAAP///wAAACwAAAAAAQABAAACAkQBADs=" width="1" height="1"><img src="/img/lazy.png" alt="One picture, lazily loaded"></figure>
<figure><img src="/img/e.png" alt="e"><p>{FILL}A paragraph written inside a figure stays a paragraph.</p><img src="/img/f.png" alt="f"></figure>
<p>{FILL}</p>""")


class Panels(unittest.TestCase):
    """Several pictures in one <figure> (a figure's panels, side by side on its page) stay one figure, laid out as an
    arXiv paper's subfigures; trafilatura made each a figure of its own, one under the other, at the column's width."""
    @classmethod
    def setUpClass(cls):
        cls.md, cls.html = read(PANELS)

    def test_a_figures_panels_stay_one_figure_side_by_side(self):
        self.assertEqual(self.md.count('<div class="ltx_flex_figure">'), 1)
        group = self.md[self.md.index("ltx_flex_figure"):]
        group = group[:group.index("</figure>")]
        self.assertEqual(re.findall(r'alt="([^"]+)"', group), ["(a) Sparse", "(b) Top-p", "(c) Confidence", "(d) Sycophancy"])
        self.assertNotIn("ltx_flex_break", group)   # (four in one row)
        doc = parse(self.html, {"id": "web-x", "title": "Innocent agents"})
        self.assertEqual(sum(1 for b in doc["blocks"] if b.get("k") == "fig" and "ltx_flex_figure" in b.get("html", "")), 1)

    def test_a_lazy_loaders_stand_in_is_no_panel(self):
        self.assertIn("lazy.png", self.md)   # (one picture: a figure of its own, as before)
        self.assertEqual(self.md.count("ltx_flex_figure"), 1)   # (the four panels' alone)

    def test_a_figure_with_words_of_its_own_is_read_as_before(self):
        self.assertIn("A paragraph written inside a figure stays a paragraph.", self.md)

    def test_six_panels_are_two_rows_of_three(self):
        html = webpage._panels_html({"pics": [(f"/p{k}.png", "") for k in range(6)], "cap": ""})
        self.assertEqual(html.count("ltx_flex_break"), 1)
        self.assertEqual(html.split("ltx_flex_break")[0].count("ltx_flex_cell"), 3)


URL = "https://blog.example.org/2026/innocent/"
SIM = "Interactive simulator: temperature and consensus"
# what the frame showed, its scripts out: a simulator's controls and numbers, a still of what it draws
STILL = ('<div class="controls"><label>sampling temperature T</label><span>0.35</span><button>Run again</button></div>'
         '<p>conformity λ1.50 round 1 -1 0 +1 out of the tanh curve</p>')


def framed(k, src, title, h, inner, host="https://blog.example.org"):
    """A frame as the browser's button writes it in its snapshot (extension/snapshot.js; background.js, withFrames)."""
    return (f'<div class="post-embed" src="{src}" title="{title}" style="width: 100%; height: {h}px; border: 0px;" scrolling="no" '
            f'loading="lazy" data-tf-frame="{k}" data-tf-frame-src="{host}{src}">{inner}</div>')


SNAP = page(f"""<p>{FILL}</p>
{framed(0, "/assets/html/sim.html", SIM, 499, STILL)}
<p><em>You set the temperature; each run lasts thirty rounds.</em></p>
<p>{FILL}After the figure.</p>
<div width="1" height="1" data-tf-frame="1" data-tf-frame-src="https://tracker.example.net/pixel"></div>
<p>{FILL}The end.</p>""")
# the article as a reading view in the browser gave it (Readability's: the page's class and style dropped)
VIEW = (f'<div><p>{FILL}</p><div src="/assets/html/sim.html" title="{SIM}" data-tf-frame="0" '
        f'data-tf-frame-src="https://blog.example.org/assets/html/sim.html">{STILL}</div>'
        f'<p><em>You set the temperature; each run lasts thirty rounds.</em></p><p>{FILL}After the figure.</p><p>{FILL}The end.</p></div>')


class FromTheBrowser(unittest.TestCase):
    def embeds(self, html):
        return re.findall(r'<a class="pf-embed pf-h-(\d+)" href="([^"]+)" title="([^"]*)"', html)

    def test_a_frame_of_the_snapshot_is_the_figure_it_was_in_its_place(self):
        _, html, _ = webpage.read(SNAP, URL)
        self.assertEqual(self.embeds(html), [("499", "https://blog.example.org/assets/html/sim.html", SIM)])
        self.assertLess(html.index(FILL.strip()[:40]), html.index("pf-embed"))
        self.assertLess(html.index("pf-embed"), html.index("You set the temperature"))
        for still in ("sampling temperature T", "Run again", "conformity"):
            self.assertNotIn(still, html)
        self.assertNotIn("tracker.example.net", html)

    def test_a_reading_views_frame_too_as_high_as_on_the_snapshot(self):
        html, _ = webpage.read_view({"html": VIEW, "title": ""}, SNAP, URL)
        self.assertEqual(self.embeds(html), [("499", "https://blog.example.org/assets/html/sim.html", SIM)])
        self.assertNotIn("conformity", html)
        from textfold import semantic
        doc = parse(semantic.normalize(html, sizes=True), {"id": "web-x", "title": "Innocent agents"})
        self.assertEqual(sum(1 for b in doc["blocks"] if b.get("k") == "fig" and "pf-embed" in b.get("html", "")), 1)

    def test_a_page_whose_article_is_in_a_frame_is_read_as_its_words(self):
        shell = ("<html><head><title>Innocent agents</title></head><body><nav>Home About</nav>"
                 + framed(0, "/reader/article.html", "Article", 2000,
                          f"<article><h1>Innocent agents</h1><p>{FILL}</p><p>{FILL}Second.</p><p>{FILL}Third.</p></article>")
                 + "</body></html>")
        _, html, _ = webpage.read(shell, URL)
        self.assertIn("Third.", html)
        self.assertEqual(self.embeds(html), [])

    def test_the_check_says_which_interactive_figure_a_reading_lacks(self):
        lacking = VIEW.replace(VIEW[VIEW.index('<div src='):VIEW.index("</div><p><em>") + 6], "")
        c = adapt.check(SNAP, URL, lacking)
        self.assertEqual((c["interactive figures"], c["interactive figures not there"]), ({"page": 1, "reading": 0}, [SIM]))
        self.assertEqual(c["missing (blocks)"], 0)   # (the still's words are the figure's, not the article's)
        html, _ = webpage.read_view({"html": VIEW, "title": ""}, SNAP, URL)
        kept = adapt.check(SNAP, URL, html)
        self.assertEqual((kept["interactive figures"], kept["interactive figures not there"]), ({"page": 1, "reading": 1}, []))
        self.assertEqual(adapt.cost(c) - adapt.cost(kept), 1)


if __name__ == "__main__":
    unittest.main()
