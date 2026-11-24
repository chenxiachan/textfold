"""A Markdown document's display formulas are equations, a long one too: in its own box, which scrolls it within the
column (on a phone it widened the page, and the level bar went off centre, #44), and in a list item shown at all.
From 0.10.0 the unfolding of a layout's tables (semantic.py) took a formula's table for one when its MathML had thirty
words; a document stored so (a paragraph holding the formula alone) reads its formulas as equations again, with no
new import. A layout's table still reads as text.

Run from the repo root:
    python3 -m unittest discover tests
"""
import re
import unittest

from textfold import markdown, semantic
from textfold.parse import parse

LONG = (r"R(s,a) = \lambda_1 \mathbb{1}[\text{task success}] + \lambda_2 \sum_{t=0}^{T} \gamma^t \left( r_{\text{grasp}}(s_t)"
        r" + r_{\text{insert}}(s_t) - c \cdot \lVert a_t \rVert_2^2 \right)")
IN_LIST = r"\lambda_1 > \lambda_2 \sum_{t=0}^{T} \gamma^t \max_{s} \left( r_{\text{grasp}}(s) + r_{\text{insert}}(s) \right) + \lambda_2 \, c \, T \max_{a} \lVert a \rVert_2^2"
TEXT = f"""The policy is trained with a reward that adds a sparse success term to dense shaping terms.

$$
{LONG}
$$

Two choices shape how the terms are weighed:

- The weight of the success term is chosen so that a single success outweighs any shaping.

  $$
  {IN_LIST}
  $$

- The discount is kept close to one.

A short formula: $$E = mc^2$$
"""


def imported(text):
    """The document as the Markdown path stores it (source.html) and parse reads it."""
    html, _ = markdown.to_html(text)
    stored = semantic.normalize(html, sizes=True)
    return stored, parse(stored, {"id": "md-test", "unit_ids": "text"})


def equations(doc):
    return [b["html"] for b in doc["blocks"] if b["k"] == "eq"]


class Equations(unittest.TestCase):
    def test_math_conversion_waits_for_a_closing_fence(self):
        inside, outside = r"\(inside\)", r"\(outside\)"
        for fence in ("```", "~~~"):
            with self.subTest(fence=fence):
                source = f"{fence}markdown\n{fence} literal text\n{inside}\n{fence} \t\n\n{outside}"
                html, _ = markdown.to_html(source)
                self.assertIn(f"{fence} literal text\n{inside}\n</code>", html)
                self.assertIn('<span class="math inline">outside</span>', html)

    def test_a_long_formula_keeps_its_box(self):
        _, doc = imported(TEXT)
        eqs = equations(doc)
        self.assertEqual(len(eqs), 3, [b["k"] for b in doc["blocks"]])
        self.assertTrue(all("ltx_equation" in e for e in eqs))
        self.assertIn(r"\lambda_1 \mathbb{1}", eqs[0])
        self.assertFalse([b for b in doc["blocks"] if b["k"] == "raw"])

    def test_in_a_list_item_it_is_shown(self):
        _, doc = imported(TEXT)
        self.assertTrue(any(r"\lambda_1 &gt; \lambda_2" in e for e in equations(doc)))

    def test_a_document_stored_before_reads_them_as_equations(self):
        stored, now = imported(TEXT)
        old = stored
        for latex in (LONG, IN_LIST):   # its equation's table unfolded, as 0.10.0 to 0.10.25 stored it
            start = old.index('<table class="ltx_equation')
            while latex.replace(">", "&gt;") not in old[start:old.index("</table>", start)]:
                start = old.index('<table class="ltx_equation', start + 1)
            end = old.index("</table>", start) + len("</table>")
            math = old[old.index("<math", start):old.index("</math>", start) + len("</math>")]
            old = old[:start] + f'<p class="ltx_p">{math}</p>' + old[end:]
        self.assertEqual(old.count('<table class="ltx_equation'), 1)
        was = parse(old, {"id": "md-test", "unit_ids": "text"})
        unnumbered = lambda doc: [re.sub(r'<td class="ltx_eqn_cell ltx_eqn_eqno">.*?</td>', "", e) for e in equations(doc)]
        self.assertEqual(unnumbered(was), unnumbered(now))   # (those stored before equations were numbered have none)
        self.assertEqual(was["units"].keys(), now["units"].keys())   # (the notes on the paragraphs stay where they are)

    def test_a_formula_among_words_or_in_a_table_is_left_alone(self):
        html = ('<article class="ltx_document"><section class="ltx_section" id="s1"><h2 class="ltx_title">One</h2>'
                '<p class="ltx_p">where <math display="block" alttext="x"><mi>x</mi></math> is the input.</p>'
                '<figure class="ltx_table" id="t1"><table class="ltx_tabular"><tr><td><p class="ltx_p">'
                '<math display="block" alttext="y"><mi>y</mi></math></p></td></tr></table></figure></section></article>')
        doc = parse(html, {"id": "md-test"})
        self.assertEqual(equations(doc), [])
        self.assertEqual([b["k"] for b in doc["blocks"]], ["h", "u", "fig"])

    def test_a_layout_table_still_reads_as_text(self):
        words = " ".join(["The screenshot beside this paragraph shows the reader at its Brief level, with the words"] * 2)
        _, doc = imported(f"Intro.\n\n<table><tr><td>{words}</td><td>Note</td></tr></table>\n")
        self.assertFalse([b for b in doc["blocks"] if "<table" in (b.get("html") or "")])
        self.assertTrue(any("screenshot" in " ".join(t.get("t", "") for t in u["toks"]) for u in doc["units"].values()))


if __name__ == "__main__":
    unittest.main()
