"""A web page's reading checked against the page as its reader saw it (adapt.check), and three ways a reading lost
words that the check found (a Zhihu article, 2026-11-14: half of its words were not read).

- A list put straight in a list (<ul><ul>, not in an item) is drawn by a browser as a nested list: its items are read,
  a level deeper. A list's item whose words are all in links (a reference list of titles) is read too.
- A formula is never a note's mark: Zhihu keeps each formula's TeX hidden beside its drawing, and a formula that
  reads n or 0 was taken for a mark, its TeX for a note on it.
- The check compares what the reader shows, not the reading's HTML; finds the article from where the reading's
  words are on the page (comments and a site's furniture are outside it); compares formulas by their place; and
  reads Chinese, which has no spaces to count words by.

Run from the repo root:
    python3 -m unittest tests.test_web_check
"""
import unittest

from lxml import html as lhtml

from textfold import adapt, webpage
from textfold.parse import parse

URL = "https://zhuanlan.example.com/p/1"
ZH = "假设爱丽丝希望把一个可能无限长的数据集无损地传送给鲍勃，我们先看最简单的传输方法。"
EN = "The river office kept the gauges for a century, and its records show how the spring floods rose and fell. "


def drawn(tex):
    """A formula as Zhihu's page has it once MathJax drew it and the browser's button read its source: the drawing
    (its source on it, data-tf-math), its TeX hidden beside it."""
    return (f'<span class="ztext-math" data-tex="{tex}"><span></span><span><span class="MathJax_Preview"></span>'
            f'<span class="MathJax_SVG" data-tf-math="{tex}" data-tf-math-in="tex">{tex}</span>'
            f'<span class="math-holder" data-tf-css-hidden="1">{tex}</span></span></span>')


def math(tex):
    return f'<math alttext="{tex}" display="inline"><semantics><annotation encoding="application/x-tex">{tex}</annotation></semantics></math>'


PAGE = f"""<html><head><title>压缩</title></head><body>
<header><h1>压缩</h1><div class="AuthorInfo">科技猛兽 等 3203 人赞同了该文章</div></header>
<div class="Post-RichText">
<p>{ZH}这样传输一个大小为 {drawn("n")} 的数据集 {drawn("D_n")} 的代价为</p>
<ul><li>{EN}The first item.</li><ul><li>{EN}A nested item, written as Zhihu writes one.</li></ul><li>{EN}The second item.</li></ul>
<h2>Reference</h2>
<ul><li><a href="https://example.org/a">Compression for AGI</a></li><li><a href="https://example.org/b">A title</a> @someone</li></ul>
</div>
<div class="Comments"><p class="CommentContent">写得很好！不过既然认为压缩即智能，那区分这两者好像意义不大，这是一条评论。</p></div>
<div class="ContentItem-actions">赞同 3203</div>
</body></html>"""


def reading(nested=True, formula=True, byline=False):
    """The article as the reader would have it: what a reading lost taken out, as asked."""
    return f"""<article>{'<p>科技猛兽 等 3203 人赞同了该文章</p>' if byline else ''}
<p>{ZH}这样传输一个大小为 {math("n") if formula else ""} 的数据集 {math("D_n")} 的代价为</p>
<ul><li><p>{EN}The first item.</p></li>{f'<ul><li><p>{EN}A nested item, written as Zhihu writes one.</p></li></ul>' if nested else ''}
<li><p>{EN}The second item.</p></li></ul>
<h2>Reference</h2>
<ul><li><p><a href="https://example.org/a">Compression for AGI</a></p></li><li><p><a href="https://example.org/b">A title</a> @someone</p></li></ul>
</article>"""


class Lists(unittest.TestCase):
    def doc(self, body):
        return parse(f'<article class="ltx_document">{body}</article>', {"id": "web-lists", "title": "Lists"})

    def test_a_list_put_straight_in_a_list_is_read_a_level_deeper(self):
        doc = self.doc('<ul><li><p class="ltx_p">One.</p></li><ul><li><p class="ltx_p">Under one.</p></li>'
                       '<ul><li><p class="ltx_p">Under that.</p></li></ul></ul><li><p class="ltx_p">Two.</p></li></ul>')
        got = [(u["toks"][0]["t"], u["depth"]) for u in doc["units"].values()]
        self.assertEqual(got, [("One", 0), ("Under", 1), ("Under", 2), ("Two", 0)])
        self.assertEqual([b["k"] for b in doc["blocks"]].count("u"), 4)

    def test_an_item_all_links_is_read_in_a_list_with_words_and_a_formula_alone_is_shown(self):
        doc = self.doc('<ul><li><p class="ltx_p"><a href="https://example.org/a">Compression for AGI</a></p></li>'
                       '<li><p class="ltx_p"><a href="https://example.org/c">A title</a> @someone</p></li>'
                       f'<li><p class="ltx_p">{math("x^2")}</p></li></ul>'
                       '<p class="ltx_p"><a href="https://example.org/b">A link alone, outside a list</a></p>')
        first, second = doc["units"].values()
        self.assertEqual((first["role"], doc["atoms"][first["toks"][0]["a"]]["text"]), ("item", "Compression for AGI"))
        raws = [b["html"] for b in doc["blocks"] if b["k"] == "raw"]
        self.assertEqual(len(raws), 2)   # (the formula alone, in its list; the link alone outside one, as before)
        self.assertIn("x^2", raws[0])

    def test_a_list_of_links_alone_is_not_read(self):
        """A menu, a site's row of buttons, its previous and next articles: as before."""
        doc = self.doc('<ul><li><p class="ltx_p"><a href="https://example.org/prev">Previous article in this issue</a></p></li>'
                       '<li><p class="ltx_p"><a href="https://example.org/next">Next article in this issue</a></p></li></ul>')
        self.assertEqual(doc["units"], {})
        doc = self.doc('<ol><li><p class="ltx_p">Gauge, A. Spring floods. Rev. Rivers 80, 1275 (2008).</p>'
                       '<p class="ltx_p"><a href="https://example.org/ads">ADS</a> <a href="https://example.org/gs">Google Scholar</a></p></li>'
                       '<li><p class="ltx_p"><a href="https://example.org/t">A bare title</a></p></li></ol>')
        got = [doc["atoms"][u["toks"][0]["a"]]["text"] if "a" in u["toks"][0] else u["toks"][0]["t"] for u in doc["units"].values()]
        self.assertEqual(got, ["Gauge", "A bare title"])   # (the reference's row of buttons is not; its bare title is)

    def test_a_list_of_code_is_read(self):
        doc = self.doc('<ul><li><p class="ltx_p"><code>drain3==0.9.11</code></p></li><li><p class="ltx_p"><code>numpy==2.4.5</code></p></li></ul>')
        self.assertEqual(len(doc["units"]), 2)

    def test_a_list_of_places_on_the_page_is_not_read_again(self):
        """A page's contents, or a list of its sentences to go to: its words are the page's own, read where they are."""
        doc = parse('<article class="ltx_document"><ul><li><p class="ltx_p"><a href="#intro">Introduction</a></p></li>'
                    f'<li><p class="ltx_p"><a href="{URL}#s2">Go to the sentence: the floods rose.</a></p></li></ul></article>',
                    {"id": "web-toc", "title": "Contents", "abs_url": URL})
        self.assertEqual(doc["units"], {})


class FormulaNotAMark(unittest.TestCase):
    def test_a_formula_and_its_hidden_tex_are_not_a_note(self):
        root = lhtml.fromstring(f"<div><p>大小为 {drawn('n')} 的数据集，次数为 {drawn('16')}。</p>"
                                '<p>A gauge is read<sup>1</sup><span data-tf-css-hidden="1">Since 2019.</span> every hour.</p></div>')
        webpage._hover_marks(root)
        self.assertEqual(len(root.xpath("//*[@data-tf-note]")), 1)   # (the note on its mark 1, and none on n or 16)
        self.assertEqual(root.xpath("//*[@data-tf-note]")[0].text, "Since 2019.")
        self.assertEqual(root.xpath("//*[@data-tf-mark]")[0].tag, "sup")


class Check(unittest.TestCase):
    def test_a_whole_reading_is_the_page_s_article(self):
        c = adapt.check(PAGE, URL, reading())
        self.assertEqual(c["article"], "div.Post-RichText")
        self.assertEqual((c["missing (blocks)"], c["formulas not there"], c["not on the page (count)"], c["from outside the article"]), (0, 0, 0, 0))
        self.assertEqual(c["formulas"], {"page": 2, "reading": 2})
        self.assertIn("div.Comments", c["not in the reading, outside the article"])   # (the comments: not the article's)

    def test_what_a_reading_lost_is_said(self):
        c = adapt.check(PAGE, URL, reading(nested=False, formula=False, byline=True))
        self.assertEqual(c["missing (blocks)"], 1)
        self.assertIn("A nested item", [e for _, (n, eg) in c["missing"].items() for e in eg][0])
        self.assertEqual(c["formulas not there"], 1)
        self.assertEqual(c["formulas"], {"page": 2, "reading": 1})
        self.assertEqual(c["from outside the article (e.g.)"], ["科技猛兽 等 3203 人赞同了该文章"])

    def test_chinese_words_the_page_does_not_show_are_said(self):
        c = adapt.check(PAGE, URL, reading().replace(ZH, ZH.replace("最简单的", "最复杂的")))
        self.assertEqual(c["not on the page (count)"], 1)

    def test_text_that_does_not_fold_is_said(self):
        """A reading view that wraps the article's paragraphs in one (Readability's <p><span><div><p>, a Zhihu
        answer): every word is there, and none of it folds."""
        page = f"<html><body><article><p>{EN}One.</p><p>{EN}Two.</p><p>{EN}Three.</p></article></body></html>"
        wrapped = parse(f'<article class="ltx_document"><p class="ltx_p"><span><p class="ltx_p">{EN}One.</p><p class="ltx_p">{EN}Two.</p>'
                        f'<p class="ltx_p">{EN}Three.</p></span></p></article>', {"id": "web-wrapped"})
        self.assertEqual(wrapped["units"], {})
        c = adapt.check(page, URL, None, doc=wrapped)
        self.assertEqual((c["missing (blocks)"], c["not folding"]), (0, 1.0))
        c = adapt.check(page, URL, f"<article><p>{EN}One.</p><p>{EN}Two.</p><p>{EN}Three.</p></article>")
        self.assertEqual(c["not folding"], 0.0)


class Stored(unittest.TestCase):
    def test_a_page_sent_from_the_browser_is_checked_and_no_other(self):
        from pathlib import Path
        import tempfile
        from unittest import mock
        from textfold import capture, local, store
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(store, "DATA", Path(tmp)), \
                mock.patch.object(local, "prefetch", lambda srcs: {}), mock.patch.object(capture, "_server_url", lambda: None):
            read = {"defuddle": {"html": reading(nested=False), "title": "压缩", "words": 400}}
            pid = capture.receive({"format": capture.FORMAT, "version": capture.PROTOCOL, "url": URL, "html": PAGE, "read": read})["paper"]
            got = adapt.stored(pid)
            self.assertEqual((got["missing"], got["formulas"], got["edited"]), (1, 0, False))
            self.assertIn("A nested item", got["missing_eg"][0])
            self.assertIs(adapt.stored(pid), got)   # (said again only when the page or its reading changes)
            self.assertIsNone(adapt.stored("md-not-there"))


if __name__ == "__main__":
    unittest.main()
