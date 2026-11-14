"""A research blog read as fetched (no script run): its interactive figures and its formulas. An interactive figure that
is a page of its own in an <iframe> (al-folio's and Distill's blogs) was left out; it is kept in its place now, as a
link the reader draws in a sandboxed frame. A formula written in the text for MathJax or KaTeX to draw ($$...$$) came
out with its _ escaped (\\sigma\\_i), and ran into the paragraph after it; it is kept as written, a paragraph of its
own. $...$ is a formula only where the page's own settings say so. No network: the page is given.

Run from the repo root:
    python3 -m unittest discover tests
"""
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from textfold import store, webpage
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


if __name__ == "__main__":
    unittest.main()
