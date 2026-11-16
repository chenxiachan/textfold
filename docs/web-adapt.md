# A web page read with a recipe for its site

Status: a first version, in TextFold 0.10.40, for agents (`python3 -m textfold adapt`).

TextFold reads any web page with one generic reader (`webpage.py`: hidden text out, notes made footnotes, the article
taken out by trafilatura). Some pages it reads badly: a table whose numbers are buttons comes out empty, a chart's
labels run into the text, a site's headings are lost. Writing a rule for each site would never end. A **recipe** is
written for a site instead, by a model (an agent on this computer, or any model), and kept for the site's other
pages.

## A recipe holds selectors, never words

```json
{"format": "textfold/web-recipe", "version": 1, "site": "distill.pub", "pages": "*",
 "article": "d-article", "drop": ["#thread-nav", "d-contents"], "notes": [], "content": [],
 "figures": ["d-article figure:has(svg):not(:has(img))"], "why": "..."}
```

`article` is the element that holds the article; `drop`, its furniture; `notes`, a note shown on hover and its mark,
when the page does not mark them in a way TextFold already reads; `content`, controls that hold the article's words
(a table's numbers drawn as buttons); `figures`, things drawn by scripts, kept as their caption with a link to them on
the page. TextFold's code takes every word from the page (`adapt.apply`) and checks them (`adapt.check`): words the
page does not show (none, by construction; the check guards the code), blocks of the article left out, paragraphs a
browser could not find again by their words. A model that writes a recipe can make a reading worse, never unfaithful.

The skill an agent follows is [textfold/skills/web-adapt/SKILL.md](../textfold/skills/web-adapt/SKILL.md). The
command it runs:

```
python3 -m textfold adapt <url> [--snapshot page.html [--rendered]]          # what it is given
python3 -m textfold adapt <url> [--snapshot page.html] --try recipe.json     # the page read with a recipe, checked
```

## The check, in the reader

A page sent from the browser keeps its snapshot, and the reader checks its reading against it (`adapt.check`, through
`adapt.stored` and `GET /api/check/<id>`): a folded line under the authors, with examples that open the page at their
place. The check needs no recipe and is the same for every site:

- **It compares what the reader shows**, block by block (`adapt.reading`): units, headings, figures, raw blocks, the
  title, authors and date in the header. Not the reading's HTML, which can hold words no unit shows.
- **It finds the article on the page** from where the reading's words are: the deepest element holding nine tenths
  of them. The page's comments, recommendations and menus are outside it; what the reading took from outside it (a
  byline, a count of likes) is said apart, as what may not be the article's.
- **A formula is compared by its place** (`∮`), on both sides: one drawn (MathML, MathJax, KaTeX, one whose source the
  extension read), and TeX the page writes in its text for a script to draw, as the reading draws it. A block whose
  words are there but not its formulas is said as such.
- **Words, not spaces.** A block is there when its words are, whatever a section's number, a dash or a footnote's digit
  does; Chinese and Japanese are counted by characters, having no spaces to count words by. A list of links to places
  on the same page (its contents) is neither the reading's nor missing from it, and a short label is counted apart.
- **Text that does not fold**: how much of the reading's running text is in raw blocks (not code, not a table), as when
  a reading view wraps the article's paragraphs in one.
- **An interactive figure is a figure.** A frame of another page on the web, as tall as a figure, is the page's figure
  (`webpage._frames`): the snapshot holds what the frame showed with its scripts out, a still of a simulator's labels and
  numbers, which is neither the article's words nor missing from the reading. The check says which of the article's
  interactive figures the reading does not show, by their names.

**The reading kept is the one closest to the page.** A page sent from the browser comes with two readings, Defuddle's
and Readability's, and TextFold reads the snapshot itself too. All three are checked against the same article (found
from the words of all of them, so that one that took a part of the article is not measured against that part alone),
and the one that differs least is kept (`adapt.choose`, `adapt.cost`, `capture.reading_of`): blocks it lacks, formulas
not in their places, formulas and pictures it lacks, words the page does not show, text that does not fold; a short
label a quarter, a block from outside the article half; an interactive figure it lacks counts as a picture does.
Defuddle first when they are as close. Before, Defuddle's was
kept unless Readability's had half as many words again: of the 18 pages sent from the browser in the library on
2026-11-14, six are now read otherwise (Defuddle left out 47 blocks of a Wikipedia article, Readability 2), and the sum
of what the check says against them halved (677.7 to 312.9).

Measured on the 157 pages of the OpenShelf set as fetched, and on 18 pages sent from the browser (2026-11-14): the
first Zhihu article read had half of its words missing (126 blocks: Zhihu writes a nested list as `<ul><ul>`, which the
reader skipped) and 19 one-symbol formulas taken for footnotes (the extension read a formula reading `n` as a note's
mark, its hidden TeX as the note); with both fixed it checks clean but for 5 blocks of the site's furniture.

**Interactive figures from the browser (2026-11-17).** A research blog's interactive figures, each a page of its own
in an `<iframe>`, were kept in their place for a page TextFold fetches (0.10.53) but not for one sent from the browser:
its snapshot has no `<iframe>`, only each frame's content where the page shows it, and both reading views read that
still as words (a simulator's labels and numbers). Now a frame of another page is the `<iframe>` it was again, in the
reading TextFold makes of the snapshot and in a reading view's that kept the frame's place (Readability keeps it;
Defuddle drops its attributes, so its reading lacks the figures and costs more), its height the snapshot's. A frame
holding more than half of the text is the article itself, read as words as before. The blog's 6 figures: 0 to 6; the
other 21 pages sent from the browser in the library read as before (their frames are adverts, trackers and blank
ones: none of them in a reading, none in an article).

## Toward a layer over the live page (a browser extension)

The recipe and the snapshot are made so that the same work can run in a reader's browser, over the page itself:

- **Selectors both sides read.** CSS Selectors Level 3 with `:is()`, `:not()` and `:has()`: soupsieve here,
  `querySelectorAll` there. A recipe never names generated class names (styled-components, CSS modules, Tailwind's
  hashed builds), which change with each build of a site.
- **The recipe is written onto the page** (`data-tf-article`, `data-tf-drop`, `data-tf-mark`/`data-tf-note`,
  `data-tf-content`, `data-tf-figure`), and the reading follows the marks: what a content script would do on the live
  DOM, before folding the paragraphs in place.
- **A snapshot is what the reader's browser shows** (`extension/snapshot.js`, run in the page by the extension, and by `tools/web_snapshot.py` for trying pages): shadow roots spelled
  out with their slots filled, frames put in place, an element the browser does not show (computed `display: none`,
  `visibility: hidden`, `opacity: 0`) marked `data-tf-css-hidden`. Hidden words hanging on a mark become a note; the
  rest stays out. This is the general answer to text hidden by a page's own CSS, which a fetched page cannot give.
- **Paragraphs found again by their words.** A note or a fold on the live page is anchored by its text (as notes are
  now), not by a path through the DOM that the page's scripts redraw. The check counts the paragraphs whose first
  words the page repeats.
- **Paywalls.** What the reader can see is what is read: a subscriber's or a signed-in reader's snapshot holds the
  whole article, and TextFold reads all of it, as it reads a PDF the reader downloaded. Nothing is unlocked for a
  reader without access: a part a meter removed in their browser, or text the snapshot hides, stays out, and the
  reading says the page is partial. The reader's own view comes from a browser extension on the signed-in page, from
  the app's own browser (signed in once, its session kept), or from a page saved as HTML and dropped on TextFold. It
  stays in the reader's library, on their computer, and is not published from it. A site that refuses TextFold's own
  fetch (`web-blocked`) can be read this way too.
- **The snapshot is kept** (`snapshot.html` beside the reading), so a better recipe or reader can read it again
  without fetching the page, which may have changed or gone.

## What the trial showed (2026-10-26)

24 pages of different makes, each read with no recipe and with one written from its brief alone (the writer saw the
brief, as an agent would, and ran `--try`). Check: words the page does not show / blocks of the article left out.

| Page | Make | Snapshot | No recipe | With a recipe |
|---|---|---|---|---|
| already-clean.goodfire.com | interactive essay | fetched | 880 paragraphs, chart labels in the text, 4 tables empty | 322 paragraphs, 345 table cells, 42 figures as links, 41 notes; 0 / 1 |
| distill.pub (zoom-in) | Distill template | rendered | 20 headings lost | headings kept, 27 notes (17 citations from shadow roots); 0 / 0 |
| transformer-circuits.pub (toy models) | Distill template, KaTeX | rendered | 2 sections, 0 formulas, 125 sentences off the page | 70 sections, 269 formulas, 169 notes; 0 / 0 |
| lilianweng.github.io | Hugo, MathJax | both | 13 captions lost, formulas lost | captions kept; rendered: 16 formulas (MathML); 0 / 0 |
| paulgraham.com | 1990s tables, `<br><br>` | fetched | 110 paragraphs, spacer images and `[ [1]]` in the text | 236 paragraphs, its 29 notes at its end; 0 / 0 |
| docs.python.org | Sphinx | fetched | code run into lines, `__init__` read as bold `init` | 103 paragraphs, 32 code blocks, `__init__` kept; 0 / 0 |
| r4ds.hadley.nz | Quarto | fetched | code and its output run together | 119 paragraphs, 3 footnotes; 0 / 0 |
| arstechnica.com | WordPress, Tailwind | fetched | 199 of 850 words, 3 paragraphs | 514 words, 13 paragraphs; 0 / 0 |
| theguardian.com | generated classes (dcr-) | fetched | 27 paragraphs, its furniture with them | 29 paragraphs (5, until a figure used as a box of text was read as one); 0 / 0 |
| bbc.com | styled-components | fetched | the story, with links around it | 12 paragraphs; 0 / 0 |
| oneusefulthing.org | Substack | fetched | 8 blocks of the post missing | 24 paragraphs, subscribe and share out; 0 / 0 |
| huggingface.co/blog | Svelte, Tailwind | fetched | the post with upvotes, cards, comments | 66 paragraphs; 0 / 0 |
| react.dev/blog | Next.js, CodeMirror | fetched | 3,426 words | 4,989 words, 118 paragraphs, code kept; 0 / 0 |
| github.blog | WordPress | fetched | the post with its contents table | 52 paragraphs; 0 / 0 |
| developer.mozilla.org | MDN | fetched | the page with its sidebars | 33 paragraphs; 0 / 0 |
| zenn.dev | Next.js, CSS modules | fetched | the guide with its cards | 242 paragraphs, 2 footnotes; 0 / 0 |
| sspai.com | Vue | fetched | the post with its readers' comments | 44 paragraphs, comments out; 0 / 0 |
| ruanyifeng.com | Movable Type | fetched | the post with related posts | 102 paragraphs; 0 / 0 |
| cnblogs.com | 博客园 | fetched | the post | 18 paragraphs; 0 / 0 |
| observablehq.com | notebook drawn by scripts | rendered | the notebook (480 words) once shadow roots are spelled out; fetched: refused | no recipe needed |
| edwardtufte.github.io | Tufte CSS | fetched | 6 side and margin notes as footnotes | no recipe needed |
| newyorker.com | metered paywall | both | fetched: the article; rendered in a new browser signed in to nothing: its first paragraphs | the same, the player out; the partial snapshot said so |
| medium.com, stackoverflow.com | bot walls | both | refused when fetched; a challenge page in a headless browser | needs the reader's own browser |

What the pages taught, in rules for every page (not for a site):

- A browser's snapshot spells out shadow roots (Observable's notebook, Distill's citations and formulas), puts frames
  in place, and marks what the browser does not show: hidden words on a mark become a note, the rest stays out.
- Words broken by two line breaks are paragraphs (Paul Graham), around any block among them (a quotation).
- A figure with nothing drawn in it and paragraphs in it is a box of text (The Guardian's explainers).
- A link to an anchor on the page stays one (footnotes of Pandoc, Quarto and markdown-it found their marks again).
- The article's own element is read as a box, whatever it is (a table cell, a font, a custom element).
- Class names a build makes are told apart for the writer of a recipe: styled-components (`BodyWrapper-cuqCAc`),
  emotion (`dcr-1qu42i0`), CSS modules (`VeVOXq_container`).

And of the reading with no recipe: trafilatura's Markdown loses what HTML keeps (code blocks and their underscores,
MathML, headings in custom elements, captions). With the article's element known, reading its HTML directly keeps
them. A reading with no recipe could look for that element first by the marks pages put on it on purpose (`article`,
`main`, `role="main"`, `itemprop="articleBody"`, microformats' `h-entry` and `hentry`) and keep trafilatura for the
rest.

Found on the way:

- A page larger than `webpage.LIMIT` (8 MB) is cut off when fetched (distill.pub: 15 MB rendered).
- A fetched page whose formulas MathJax or KaTeX draws keeps them as TeX text (`$a_t$`).
- A figure drawn by scripts is a paragraph holding a link; the reader should show it as a card of its own.
- A figure of a picture and a drawing together (goodfire's `v-figure`) loses part of its caption.

## Not yet

- Recipes kept per site and used by `webpage.fetch`; the reader's "Adapt this page" button; an agent run from the
  app with the command above as its only tool.
- The rendered snapshot in the app (Electron, scripts on, in a partition of its own) and in an extension.
- The trial's recipes are in `tools/web-recipes/`.
