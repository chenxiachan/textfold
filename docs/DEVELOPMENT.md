# TextFold: development notes

How the reader works, how to run and extend it. For what it is and how to use it, see the [README](../README.md).
The notes format is [notes-format.md](notes-format.md).

The same semantic zoom as ThoughtDAG's plaques, applied to a paper's HTML (LaTeXML) version:

| Level | What a paragraph shows | Source |
|---|---|---|
| **Full** | the paragraph as written; key sentences in darker ink | author, verbatim |
| **Brief** | 1–3 of its sentences | author, verbatim (chosen by the model) |
| **Key** | takeaway + one sentence of reason | model, written |
| **Takeaway** | one headline sentence, on an outline with role dots | model, written |
| **Topic** | a noun phrase; the paper becomes a map of section cards | model, written |

Serif text is the author's; sans text is a summary. At every level the words that survive the next zoom-out are darker, and a level change moves each surviving word (and formula) to its new place instead of swapping the text.

## Use

```bash
python3 -m pip install -r requirements.txt   # beautifulsoup4, lxml, Pillow
python3 -m textfold serve                    # http://localhost:3017  (ThoughtDAG keeps 3001)
```

The same code is a Python package (`pyproject.toml`): `python3 -m pip wheel . --no-deps` builds it, with the reader's
pages (`textfold/web/`) inside, and `pip install textfold` gives the `textfold` command. The docs write `python3 -m textfold` instead: macOS's own Python
installs a user's packages with their commands in `~/Library/Python/3.9/bin`, which is not on the PATH, so `textfold`
alone is not found there, and `python3 -m textfold` always is. Run from a clone, papers are
kept in the clone; installed, in the system's folder for app data (`store.app_data()`, shared with the desktop app).

The landing takes a link or ID (arXiv, PubMed Central, Europe PMC, bioRxiv, medRxiv, a DOI, Wikipedia), or a Markdown
file (the dashed box under the bar, or dropped anywhere on the page), and a language; a line under the bar names
the sources it reads; the sidebar lists every paper generated so far. In a paper,
the language menu switches between generated languages, generates a new one, or regenerates the current one.
One language, the reader's, any of `textfold/langs.py`: the browser's, or the one last chosen (kept in the browser,
`pf-ui-lang`; the page tells the server too, the settings' `lang`, which `GET /api/models` reports for other programs,
and `GET /api/langs` lists the languages). A paper is read in it: its text, and the names of its own parts (figures, tables, theorems, references,
equations). The interface speaks it when `web/i18n.js` has its words (English, 简体中文, 繁體中文, 日本語, 한국어, Español,
Français, Deutsch, Português, Italiano, Русский, हिन्दी), and English otherwise, as are then the names of the paper's
parts. A switch of either is a switch of both: the paper's menu at the top right, a language generated there, Settings,
or the two menus of the landing (the one beside the bar offers every language a text can be read in, the one at the
top right the interface's). A paper that has not that language yet opens in one it has, and the interface stays;
nothing is generated unasked. A question is answered in the language it is asked in, the reader's when that is
unclear.

Where things are kept: the code in the project (`textfold/`, with the reader's pages in `textfold/web/`); papers and exports in `papers/` and `out/` of the
project, or wherever `TEXTFOLD_DATA` points (`PAPERFOLD_DATA`, its earlier name, is read too); settings and keys in
`~/.textfold/`. While a server runs, `server.json` in the data folder says where it is, for the programs that
work with it (`server.announce`: format, version, url, port, pid, TextFold's version, when it started, in UTC); it is
removed when the server stops (^C, or SIGTERM, the app's `server.kill()`), unless a server started since has written its own.

The app (`desktop/`, see its README): an Electron window around the same server, with its own CPython
(python-build-standalone) and packages, so a reader installs nothing. For macOS, Apple silicon and Intel, signed and
notarized with the certificate ThoughtDAG uses, and for Windows; each built on its own system by
`.github/workflows/desktop.yml` when a version is tagged, beside the package for PyPI.

A gallery for a free static host: `tools/gallery.json` lists one paper per field, each with its field and a line on
why it is worth reading in every gallery language (en, zh, de). `python3 tools/gallery.py --make <model>` generates
what the list lacks in a folder of its own (`out/gallery/papers`; a paper already in the library starts from there,
without its notes), and `python3 tools/gallery.py` writes `out/gallery/site/`: an index in the three languages and
each paper's static reader, opened at the Topic level. A paper with a PDF (an arXiv paper) gets it beside its page, with
PDF.js in `assets/`, so Original shows its typeset pages there as in the app; a PDF over 4 MB has its large pictures
made smaller with pikepdf (`pip install -r tools/requirements-gallery.txt`; fonts and text untouched), and without it
the build stops rather than put the whole PDF online. Only
papers whose license lets anyone share a derivative go in
(CC BY, BY-SA, BY-NC, BY-NC-SA, CC0); `"consent": true` adds a paper whose authors agree. Before a paper goes public its text is proofread:
`--review` writes each paper's sentences and levels in every language to `out/gallery/review/`, and the corrections go
in `out/gallery/fixes/<id>.json`, which the build puts through the same assembly as the model's replies (only the
corrected fields change; the generated layers stay as they are). The corrections hold the papers' text, so they live in
the gallery's own repository, github.com/chenxiachan/paperfold-gallery (`fixes/`), not here; the build stops when a
paper's corrections are missing (`--unproofread` builds without them, for a look only). The gallery is served with the
website, at https://chenxiachan.github.io/textfold/gallery/: copy `out/gallery/site` and `out/gallery/fixes` into that
repository's clone (`out/gallery/deploy`) and push, then run the website's workflow (Actions › site › Run workflow), which
takes the gallery from there. The repository's own Pages keeps its old address, each page sending on to its new one.

Models (⚙ Model & API), ported from ThoughtDAG's model access:

- **Agents on this computer**, found at startup (and on Rescan) on PATH, the login shell's PATH, the usual install
  directories and the node version managers. Claude Code, Codex, Pi and CodeBuddy run generations, each listing its own models
  (Pi brings whatever providers it is configured with; CodeBuddy, WorkBuddy's copy of it first, the account's); Gemini CLI, Qwen Code, opencode, Cursor Agent, Kimi CLI, Amp,
  Goose, Crush, Droid, Copilot CLI, iFlow and Aider are reported when present. Claude Code is called without the
  user's settings and CLAUDE.md (`--setting-sources project,local` in a scratch folder; again with them if that fails
  to sign in). CodeBuddy is called the same way, but with its user settings (without them it has no sign-in and
  waits), `--tools StructuredOutput` (the one tool its structured output needs) and WorkBuddy's quiet switches (no
  memory, no reminders: ~400 tokens of its own a call); WorkBuddy's copy runs on the app's Electron as node, with
  WorkBuddy's settings folder, as WorkBuddy runs it (`agents.env_for`). WorkBuddy's copy is not a documented way in,
  so it is looked for widely and checked (`agents.locate_codebuddy`): WorkBuddy where it runs (the process list), its
  usual places, where Windows' installer recorded it or a Mac's Spotlight knows it; CodeBuddy inside it by name; the
  first copy that answers `--version` (on the app's program, else on node), else a codebuddy installed on its own;
  CodeBuddy's own sign-in when WorkBuddy's settings have none.
- **Two easy ways in** (`textfold/bridges.py`, as in ThoughtDAG):
  - **ChatGPT plan, local bridge**: openai-oauth (npm) serves the plan as an OpenAI-compatible endpoint on
    127.0.0.1:10531, signed in with Codex's login (or a browser sign-in). Running, it joins the providers on its own
    (as a running Ollama does), with its text models; not running, the providers list has a row to start it (`npx -y openai-oauth@2 --detach`; `npx openai-oauth stop` stops it). On the test
    paper: 174 s with gpt-5.5 at the recommended levels, nothing failed, no cost beyond the plan; a section takes about
    8 s against 13 s through `codex exec`.
  - **OpenRouter in one click**: OAuth with PKCE. The server makes the verifier, OpenRouter's consent page sends the
    reader back to `/oauth/openrouter` with a code, the code and verifier become a key, and the key waits in memory
    until the reader picks models and saves. Free models are listed first (the free router, then known makers, never an
    anonymous "stealth" model by default) and a new connection picks five of them. (Their limits, not shown to the
    reader: 20 a minute; 50 a day until $10 of credit has been bought, then 1,000; a paper takes about 40 calls.)
  - **The model list** of a provider shows the picked models first, and a search box narrows a long list (OpenRouter
    lists 466) as you type, keeping what is ticked. Changing the kind of a provider being edited makes a new one.
  - **Rate limits**: a 429, 502, 503 or 529 is waited out (Retry-After, else 5, 15, 30, 45 s) and asked again.
- **Thinking, per task**, set by the reader's tools and not by the reader (a reader is here to read):
  `models.DEFAULT_THINKING` gives the levels and takeaways off, the argument links low, the translation off, the
  questions low, from the model bench.
  Each caller spells a level its own way: Claude Code `--effort`, and off as `MAX_THINKING_TOKENS=0`; Codex
  `model_reasoning_effort` (off as `none`, else `low`); Pi `--thinking`; the Claude API thinking and effort by model
  (off is `between_tools` on Sonnet 5.x, `disabled` on older models, the least effort where thinking cannot be
  switched off, no thinking on Haiku); OpenAI-compatible APIs the provider's field (OpenRouter `reasoning`, GLM and
  DeepSeek and Kimi `thinking`, Qwen `enable_thinking`), then OpenAI's `reasoning_effort` (off as none, minimal, low:
  some models always think).
- **A level is a request, never a condition.** These spellings change as APIs change, so a request refused for one
  (a 400 or 422, or an answer left in the reasoning) is sent the next way, and last without any reasoning field; the
  way that worked is remembered for the run. Under every caller, a call that fails with a level set is made once more
  at the model's default, and the reply records why (`note`). Tested against a fake endpoint that refuses the fields,
  answers empty, refuses JSON mode or takes one spelling only, and live on Zhipu and OpenRouter.
- **Reasoning costs time, hardly quality, for this work** (measured on one paper, 2026-10-04): DeepSeek flash
  through Pi took 44 s without thinking and 386 s at xhigh, with the same blind grades; Sonnet 108 s against 395 s,
  +0.14 on a 5-point scale; Haiku 163 s against 1545 s. A weaker model costs more quality than switching thinking off.
  Without thinking a reply's JSON fails to parse now and then (asked for once more), and a long section's
  translation came back short: translations are made in parts of about 5,000 characters.
- **API providers**: keys from the environment, or a `.env` here or in `~/.textfold/`: Zhipu, Qwen, OpenAI,
  Anthropic, Google, DeepSeek, Kimi, OpenRouter, Ollama. (Until v0.9 ThoughtDAG's `.env` was read too; its providers
  were copied once into the settings.) Others are added in the dialog
  from ThoughtDAG's presets (OpenRouter, Z.ai, 智谱, GLM Coding, Kimi Code, MiniMax, DeepSeek, OpenAI, Google AI Studio,
  Moonshot, Ollama, a ChatGPT bridge, Anthropic, custom): the endpoint's `/models` is fetched and you pick which to offer.
  Keys added here stay in `~/.textfold/settings.json` (mode 600), outside the project folder. (Under earlier
  names they were in `~/.paperfold/` (PaperFold) or `~/.dynamic-reader/`: copied from there once, and left in place.)

The landing and Settings open from saved configuration and the last discovery results. `GET /api/models` reads
that snapshot without contacting local services; `POST /api/models/refresh` starts one shared background refresh.
The page asks for progress only while discovery or the startup agent scan runs, then stops. Opening Settings refreshes
again. Failed service probes keep that service's previous results and show an error with retry, including on first
use without results. Agent Rescan, provider Fetch models and the actual model Connection test keep their separate jobs;
generation still resolves its model when it runs. `tests/test_model_discovery.py` covers opening and refresh failures.

Command line, without the server:

```bash
python3 -m textfold 2512.23916 --lang zh --lang ja   # generate its own levels + translations, then export
python3 -m textfold export 2512.23916                # one self-contained HTML file in out/<id>/
```

### Is the same paper always the same?

Only because it is stored. The model samples differently on every run, so two generations of one paper differ in
wording, chosen key sentences, topics and links (Full is the author's text and never changes). Each paper keeps
versioned layers in `papers/<id>/layers/` (`en.json`, `tr-<lang>.json`); the reader is assembled from them with no
model call, so it reads the same every time it is opened. Regenerate writes a new version; a translation built on an
older English version is marked outdated. Model replies are also cached by prompt in `papers/<id>/llm-cache/`.

In the page: pinch or ⌘/Ctrl-scroll zooms around the pointer, scrolling over the level bar or dragging it zooms too,
keys `1`–`5` jump to a level, `-`/`=` step, `L` cycles languages, clicking a paragraph below Full opens just that one,
`Esc` closes it. The zoom is continuous: it can stop half way or turn back, and it lays nothing out while it runs. When
the reader is still, the screen is laid out at each level ahead of time (in a hidden copy, one level per idle moment),
and a zoom plays those keyframes; the page takes over again at rest.

Notes: select text anywhere (a paragraph at any level, a section's summary or its title) to highlight it, write a note,
or ask the model about it (the answer, in the reading language, cites the paragraphs it rests on; a cite opens that
paragraph to its full text in place). The reader's own notes sit in the left margin, the conversations with the model
and the links in the right. Full and Brief show whole cards, Key shows them compact, and Takeaway and Topic show only a
mark, with the notes on hover. Highlights zoom with the words. `#n=<note id>` opens the reader at a note (`&t=<turn>`: a conversation at that turn), `#u=<id>` at a paragraph (`&to=<id>`: the run of them to that one, flashed together), `#s=<id>` at a section (the whole of it flashed; with `#level=`, at a level), `&eq=<id>` with either at an equation set apart; one no longer in the text opens the
section it was in, else the top, and the page says so (`ids.py`). ⌘F (Ctrl+F) finds words in the text (reader.js, `findAll`): in each paragraph's pieces at its level (the model's line at Key, Takeaway and Topic), then in its Full pieces for what that level leaves out, and in section titles, case and accents aside. The words found are marked through `pieces()` (`fd`, the one stepped to `fdc`), so a redraw keeps them; a step to words left out opens their paragraph at Full in place, one paragraph at a time, and a level or language changed finds them again. A paragraph, a heading, a section's summary or a message of a conversation (by the handle beside it on hover), a card of the Topic map (grabbed anywhere), a note's card or a selection, dragged into another app, carries its words and a `textfold://` link back to it ([drag-format.md](drag-format.md)).

What the model is given for a question (`textfold/notes.py`, `prompt`): first, the paper in full, each paragraph in the
authors' words after its number, when it comes to at most `FULL_TOKENS` (60,000, counted roughly: a CJK character one
token, four other characters one), the same beginning for every question about it, which a service that keeps a
prompt's beginning (DeepSeek, OpenAI) reads once; then a line per section (with where the reader is); the level they
read at and whose words it shows (at Key and below, a warning that the selection is the model's wording, not the
authors'); the selection and its paragraph in the authors' words (or, for a section, its summary and each paragraph's
point); the earlier turns; and the paragraphs it may cite (any, in the paper in full). A paper longer than that, or one
the model says is too long for it (`TOO_LONG`, asked again at once), is given as its title, abstract and a line per
section, the selection's paragraph, and up to five passages from anywhere in it that share the question's telling
words (IDF over the text's own and the reading-language text; symbols such as D=4 kept whole, CJK matched by
character pairs, question words dropped), with the paragraphs it may cite listed. The system prompt asks it to
answer from the paper, say where, and keep general knowledge apart.

An agent on this computer answers as a one-shot text generator, with no tools, unless the switch beside the question
is on: in a passage's question and its follow-ups, and in **Ask the whole text** (`reader.js` composer; off whenever a
text is opened, shown only where questions go to an agent: `app.js` askAgent; `/api/ask` `tools`, `models.ask_spec`).
Then the agent looks it up on the web with its own tools, and nothing else (`agents.py`): Claude Code with
`--tools WebSearch,WebFetch`, allowed without asking and no MCP server; CodeBuddy the same, with its StructuredOutput;
Codex with `--search` in its read-only sandbox; Pi with `--no-builtin-tools`, its extensions' tools alone. None of them
changes a file (Claude Code, CodeBuddy and Pi read none and run no command either; Codex may read, in its sandbox); each
keeps its own system prompt, ours appended. The prompt adds that what
comes from outside the paper ends with [[?]], each page drawn on given as a Markdown link (the reader draws links in
answers: `reader.js`, `mdLite`); such an answer may take up to 15 minutes, is kept apart from one without the web
(`llm.call`'s key), and its turn keeps `tools: true`. A service (an API) has no tools of its own: there is no switch
for it.

The notes button opens the list of every highlight, note and question, by section and filtered by kind: a click flies
to the passage (opening its paragraph in place where the zoom hides it) and opens the note; × deletes it after a
second click. Below the list, **Save…** opens the save dialog, which the **Save** button beside the notes opens too:
every export is there. The whole text: one HTML file (`POST /api/export`: `build.page` with the notes of the kinds
chosen, in the language being read or all of them; the file opens in that language and keeps its notes apart from any
other page's in the viewer's browser, `D.saved`; with the original PDF if chosen, and PDF.js, so that the file opens
the side-by-side view by itself: `D.pdf`, `D.pdfjs`, and a converted PDF's page map, `D.pdfmap`), or a PDF at the level being read (the column as it stands, paragraphs
opened in place included, every paragraph drawn at that level first, the notes of the kinds chosen under their
paragraphs, the level named on top and the TextFold mark at the end; ⌘P prints the same). Only the marks: one small
HTML page made in the browser (the passages quoted, or each mark in its whole paragraph if chosen; the notes, the
questions and answers with their citations as labels, formulas as MathML, a link to the source, the mark), a PDF,
Markdown for Obsidian (block ids, callouts, links back) or the notes data (JSON, with `made_by`). The dialog says, for
every file, that TextFold is a tool and that a file shared follows the terms of the original text; it judges no
license. Where a file goes, the reader chooses: the desktop app's own save dialog (`save-file`, in `desktop/main.js`),
Chrome's file picker, else the browser's downloads; a PDF through the system's print dialog. While the file is made (seconds,
for a whole text with its PDF), the mark folds and unfolds in place of the dialog's buttons. A page that is a file
(saved, or published) has its mark lead to the repository and ends with it.

The chat (the button in the corner, in the app) asks about the whole paper. Each conversation is a note with
`on: "paper"`; the model is given the paper in full, or, for a paper longer than `FULL_TOKENS`, the abstract, every
paragraph's point section by section (at most 60,000 characters: a long paper keeps every section and fewer
paragraphs each) and eight passages found for the question.
The answer cites up to five paragraphs; a cite opens its paragraph in place.

In the app the mark (in the bar and in the sidebar's rail) leads home; the rail's second button opens the list of
papers. A paper is deleted from the list after a prompt that says what goes with it: its folder (layers, model
replies, notes) moves to `papers/.trash/<id>-<time>/`, from where it can be moved back for 30 days: the server erases
older ones as it starts (`store.empty_trash`). A paper being generated cannot be deleted. The list is searched (⌘K or
/), ordered by kind, by when each document came in or by when it was
last opened, and kept in folders, one level deep, a document in one at most: dragged there, or moved from the menu a
right click opens on it. Folders and dates are in `library.json` beside `papers/` (`library.py`). The format, the contract for every export, is `docs/notes-format.md`; the server keeps notes in
`papers/<id>/notes.json`, a static page in the browser.

Links: the author's cross-references (parsed) and argument links (one model call: supports / builds on / qualifies /
explains) show as margin notes at Full, Brief and Key; on the Topic map, hovering a chip
keeps its linked chips lit and lets the rest step back. Hovering a reference previews its target; clicking jumps there
and a Back pill returns.

## Other sources: one parser, through semantic HTML

arXiv's LaTeXML page is read by `parse.py`. Every other source is first written as plain semantic HTML (headings,
paragraphs, lists, tables, figures with captions, math, footnotes), and `semantic.normalize` rewrites that HTML in
the shape LaTeXML gives arXiv's pages: sections with titles, `ltx_p` paragraphs, figures and tables with their
captions and labels, equation tables, footnotes inside the text, a references section as a bibliography. So every
source is cut into units by the same code and drawn by the same reader; a new format is a converter to semantic HTML,
nothing more. Text before the first heading becomes an untitled first section with the id `abstract`, so it is the
context every model call is given, as an abstract is.

`normalize` is also where a document is made safe. The reader runs on the local server's origin, so a script in a
file could drive that server: only the elements and attributes a document needs are kept, links only to `#`, http(s)
and mailto, images only as files the import stored. Markdown is rendered with HTML in it shown as text.

Markdown (`markdown.py`): CommonMark with GitHub's tables and strikethrough, footnotes, definition lists, YAML front
matter (title, authors, license) and math, `$...$` and `$$...$$` by Pandoc's rules and `\(...\)`, `\[...\]` as chat
models write them; formulas become MathML with their LaTeX as `alttext`, as LaTeXML writes them. A lone image is a
figure, its caption the paragraph after it ("Figure 1: ...") or its alt text; a "Table 1: ..." paragraph next to a
table is its caption; a numbered heading ("2.1 Methods") keeps its number apart. A diagram written in Mermaid
(` ```mermaid `, Quarto's ` ```{mermaid} `, or a web page's `<pre class="mermaid">`) keeps its source alone as
`<pre class="pf-mermaid">` (`semantic.py`, `_diagrams`); the reader draws it with Mermaid, loaded only for a text that
has one, in the light or dark colours of the page, and where it cannot, the source shows as code (`reader.js`,
`drawDiagrams`). Like code, it shows at Full and Brief.

A document is stored by `local.py` as a paper is: `papers/md-<title words>-<hash>/` with `source.html` (normalized),
`source.md`, `meta.json` and `img/`, the images copied from beside the file, downloaded, or decoded, once (a large
PNG or JPEG kept as WebP, at most 3200 px: `picture.py`, as for an arXiv paper's figures). The id
carries a hash of the text, so the same file opened twice is one paper and an edited file a new one; a stored document
is never rewritten, since its layers belong to the text they were made from. `python3 -m textfold notes.md` does the same
from the command line (images found beside the file); the landing sends the file's text (`POST /api/import`), so
images given by a relative path are left out there and shown by name.

A program that keeps the text where it is written (a note app's plugin) can fold it again after an edit without losing
the document: `POST /api/import` with the document's `id` replaces its text in place (`local.replace_text`), and
`/api/generate` with `force` folds it again. The id, the folder, the notes and the model's replies stay. A Markdown
document's paragraphs are named by their words (`p-<hash>`, `"unit_ids": "text"` in its meta), and every paragraph keeps
its id for the life of its document (`ids.py`): the ids are written into `source.html` when a text is stored (`data-u`
on a paragraph, `data-s` on a section; one stored before keeps the ids it is read with), and a text replaced, or a PDF
converted again, keeps the ids of the paragraphs and sections still there, edited, moved or renamed, so a link to one
(`#u=`, `#s=`) still lands. A paragraph new in the text gets an id no paragraph had before (a PDF's are named by their
places, `u<n>`: never the place of one that was there before); one that is gone is kept in `ids.json` with the section
it was in, where a link to it opens, the page saying so. `GET /api/ids/<id>?u=<id>,…&s=<id>,…` says what each names now:
`here`, `now` (a section whose paragraphs went to another), `gone` (a paragraph with its `section`) or `unknown`. The
notes are found again in the new text (notes-format.md, "When the text changes"). The old text and its levels go
to `previous/` (the last one replaced only), and the levels are made again from the replies kept in `llm-cache/`: the
model is asked only for the sections whose prompts changed (a section's text; the title, the opening or a heading
changes them all), and for the argument links when a takeaway changed. A translation's prompt numbers the atoms of the
whole text, so a section after a formula or a link added or taken out is translated again too. A text being generated
is not replaced (409).

JATS XML (`jats.py`), the format of PubMed Central, Europe PMC, bioRxiv and medRxiv, PLOS and eLife: sections keep
their titles (and their markup), figures and tables their labels and captions, formulas the MathML the publisher wrote
(its TeX, where given, as alttext), citations and cross-references link to their targets, the reference list becomes the
bibliography. Figures an archive keeps apart (`<floats-group>`) are placed after the paragraph that first cites them.

Europe PMC (`epmc.py`) is where papers beyond arXiv are fetched: a PMC id (an open-access article), a PPR id (a
preprint whose full text it holds: bioRxiv, medRxiv, Research Square and others), any link holding one, or a DOI, looked
up there (`no-fulltext` when it holds no open full text). One request for the XML through its public REST API; figures
from PubMed Central's CDN (the XML names each one's blob in a processing instruction) or, for a preprint, Europe PMC's
file service. Stored as `papers/pmc<n>/` or `papers/ppr<n>/`, with `source.xml`, once. A full text it does not hold
comes back as an HTTP 500, read as `no-fulltext`.

bioRxiv and medRxiv (`biorxiv.py`) are read from the servers themselves: Europe PMC holds the full text of about one
bioRxiv preprint in seventeen. A link or DOI (prefixes 10.1101 and 10.64898) is looked up in their API, which gives each
version's JATS; the newest version whose XML has a body is read (a version just posted carries the abstract alone).
Their XML draws tables and display formulas as pictures, shown as such; a formula pictured inside a line stays in it
(`pf-inline`, an atom). Figures are served under their HighWire ids (`F1.large.jpg`), other pictures from the site's
`embed/` folder. Every request to www.biorxiv.org and www.medrxiv.org waits seven seconds after the last, as their
robots.txt asks (faster, their firewall blocks the reader's address for a while), so a paper with many pictures takes a
minute or two; the job's progress counts them. Stored as `papers/biorxiv-<DOI suffix>/` or `papers/medrxiv-…/`. When a site
still turns this computer away (HTTP 403 for the XML), the preprint is read from Europe PMC if it holds the full text,
under the same id; else the job says so.

Wikipedia (`wiki.py`), any language edition: the title is resolved by the Action API (redirects followed to the article
itself) and the article's Parsoid HTML read from the MediaWiki REST API; a Chinese article is asked for in zh-cn, since
its source mixes scripts and leaves the words it must not convert empty until it is converted. Left out: infoboxes,
navigation boxes and sidebars, hatnotes, maintenance notices, inline icons, layout tables, and the sections that only
list links ("See also", "External links", "Further reading", in the larger editions' words). Links to other articles
are read as their words, so the model can rephrase and translate them; a formula keeps its MathML (its TeX without
Wikipedia's `{\displaystyle …}` as alttext), and one alone in its line is a display formula; the reference lists
become one bibliography the marks [1] point into. Stored as `papers/wiki-<language>-<page id>/` with the revision read and
the day it was read (`snapshot`, shown under the title and carried into the notes' exports: the article may have changed
since), under CC BY-SA 4.0.

The model is still told it reads a paper. A text's levels are written in its own language (`store.text_lang`: what
its ladder, `en.json`, says since 0.10.33, else read from its words, `langs.detect`), and the language menu calls the
ladder by it: a Chinese document's levels are in Chinese, and reading it in Chinese is its ladder, not a translation of
it into Chinese again. Another language, English too, is a translation (`tr-<lang>.json`). A translation into the text's
own language made before then is still read while the ladder it was made from stands. A note made on the text's own
words before said `en`: it is read as the text's language until the text is first translated into English, and
written so then (`notes.settle_base`).

## PDFs and web pages

A PDF dropped on the landing is read in the browser first (pdf.js, `refsOf` in app.js): an arXiv id from its margin
stamp, a DOI from its metadata or its first page (only the first: the second is often the references). `POST /api/pdfs`
takes the file with what was found (`X-Refs`): a paper that resolves is generated from its online version, the file
kept as its `source.pdf` once it is fetched; an arXiv paper without HTML falls back to the file (or, with none, to
arXiv's PDF). While the engine is not installed, a PDF that has its words in it is read by TextFold itself
(`pdftext.py`): pdf-inspector's Markdown, page by page, into the engine's blocks, and each figure found by its caption
(the drawings and pictures above or below it, up to the prose around them) and cut from its page with pypdfium2
(`engine.crop_here`). A scanned PDF, and every PDF once the engine is there, goes to
the PDF engine (`engine.py`): MinerU 4, pinned, in `engine/` beside the library (`<app data>/engine`, or in the folder
the library was moved to) or in an empty folder the reader chooses in Settings (`engine install <folder>`), installed
from the landing, the settings or `python3 -m textfold engine install`. The folder it was installed in is kept in the
settings (`engine_path`) and stays when the library moves, since its Python cannot move; removing it removes the folder
(a PaperFold install in its old place is used too). Its command line only (`mineru-kit
parse --format zip`), never the `mineru` document library. Fast is MinerU's flash tier (seconds, no model; formulas as
pictures); accurate is advanced (a vision model, several seconds a page), with `--disable-image-analysis`.
`pdfdoc.py` turns `structured_content.json` into the document: running heads and page numbers out, the title from the
page or the file, heading depth from numbering, a figure's panels grouped under the caption that names it, figures from
after the references put before them, short front matter dropped. A file that waits for the engine is kept in
`uploads/` with a note of its name and language beside it, and converted once the engine is installed.

Other files take the same road (`POST /api/files`, `take_file`; `doc-<file hash>`, the file kept as `original.<ext>`).
With the engine installed, it reads them all: Word, PowerPoint, Excel, OpenDocument, RTF, EPUB, HTML and MHTML, CSV,
OFD (its fast tier only: no model is needed) and pictures (either tier, as a scanned page). Without it, TextFold reads
the common ones itself, at once (`office.py`): .docx through mammoth, its formulas turned from OMML into LaTeX first
(`omml.py`, written for what latex2mathml and KaTeX both read); .pptx, .xlsx and .epub from their own XML; .csv and
.tsv; a saved page's article (.html, .mhtml, its pictures from inside the file); and on a Mac .doc, .rtf and .odt
through `textutil`. It writes the engine's shape (`structured_content.json` and `images/`), so `pdfdoc.py` stores both
alike; a file whose structure is its own keeps its headings' depths, its captions and its notes. A picture, an old
PowerPoint or Excel file, an OpenDocument sheet or deck still wait for the engine; a file the engine fails on falls back
to `office.py`.

The server has two queues (`Jobs`): generation (model calls) and the engine (installs, conversions), each one job at a
time; a conversion's `next` is the generation that follows it. A converted PDF opens as soon as it is stored.

Any other link is a web page (`webpage.py`): fetched with TextFold's own user agent, its hidden elements removed (they
are where text meant for a model is put), its notes written inside sentences and shown on hover or in the margin taken
out (an ARIA tooltip under its mark, DPUB-ARIA's doc-footnote, Distill's d-footnote, Tufte's sidenote) and put back as
footnotes under the page's own marks, its article taken out by trafilatura as Markdown and stored through the
Markdown path. Too little text (`web-notext`), a refusal (`web-blocked`), and a link to a PDF (fetched, then read as a
dropped PDF) each have their own answer.

A Zotero library (`zotero.py`): **Zotero library**, under the landing's drop zone and at the foot of the sidebar,
opens the library chosen (the reader's own, or a group) as Zotero shows it, its collections at the left and the items
of the one chosen at the right, through Zotero Desktop's local API (`localhost:23119`, read only, on once the reader
allows other applications to talk to Zotero). `POST /api/zotero/read` reads an item by the best text it has: its arXiv
paper when it names one, handed back to the page to generate as a pasted link is; else its file (a PDF, an EPUB, a saved
page, a Word file), where Zotero says it is or in its `storage/`, given to `take_file` as a dropped file is, with its
DOI as the PDF's `X-Refs`; else its DOI. A file is read only when it is one TextFold reads (a PDF by its first bytes,
any other by its name), and a file name that leaves `storage/` is not followed. `zotero.json`, in the data folder,
says which item became which document, so the library marks what is open, and keeps the item's record: a document read
from the item's own file takes Zotero's title, authors, date, DOI and citekey (`zotero.apply`, once it is stored), and
lists among the papers. The page may change the library chosen and
nothing else: Zotero's address and data folder (`"zotero": {"base", "data_dir"}`) are set in the settings file only.
`tests/test_zotero.py` runs it all against a fake Zotero.

`TEXTFOLD_NO_MODELS=1` makes a server find no model at all (no agent, no bridge, no provider): for test servers and CI,
so that nothing calls a model by accident. Without a model a document is still fetched and parsed, and reads as written.

## No other server

A page asks no other server for anything: no font service, no CDN. The fonts (Inter, Source Serif 4, as woff2 subsets),
KaTeX and Mermaid (11.17.2) live in `textfold/web/vendor/`. The app serves them from `/static/vendor/`; a gallery copies them
into its `assets/` beside the pages; a single exported file carries the Latin faces inside, KaTeX too when the model wrote
math that matches no formula of the paper, and Mermaid (3.5 MB) when the text has a diagram. (Loading Google Fonts sends a visitor's IP address to Google, which a German court
found unlawful without consent in 2022.)

## Smoothness is a rule

No change may make the zoom less smooth. Measure before and after with the GPU at 2x pixels, reading the frames the
screen actually showed, on a dense paper (2512.23916):

```bash
python3 -m textfold export 2512.23916
python3 tools/zoombench.py 2512.23916                 # out/compare/<id>/<version>.html pages
```

Frame intervals from requestAnimationFrame in a default headless browser hide most of what a reader feels.

## Layout

```
textfold/fetch.py    arXiv HTML + metadata, figure images; urlread, every download of TextFold's, tried again when the network drops it
textfold/picture.py  a picture kept smaller as it comes in: a large PNG or JPEG as WebP (quality 90, at most 3200 px)
textfold/parse.py    LaTeXML → units (paragraph, list item, caption) as token streams; math, citations, refs stay atomic
textfold/latexml_safe.py  what an arXiv page may carry into the reader: scripts, handlers and script URLs out, figures kept
textfold/semantic.py any semantic HTML → the HTML parse.py reads; the sanitizing boundary
textfold/markdown.py a Markdown file → semantic HTML (front matter, footnotes, math)
textfold/local.py    documents from this computer: stored as papers (md-…), with their images
textfold/jats.py     JATS XML → semantic HTML (PubMed Central, Europe PMC, bioRxiv, PLOS, eLife)
textfold/epmc.py     papers from Europe PMC by PMC or PPR id, link or DOI (pmc…, ppr…)
textfold/biorxiv.py  preprints from bioRxiv and medRxiv, from their own JATS (biorxiv-…, medrxiv-…)
textfold/wiki.py     Wikipedia articles, any language edition (wiki-<lang>-<page id>)
textfold/github.py   a GitHub repository's README (gh-<owner>--<repo>)
textfold/webpage.py  any other web page: its article (trafilatura), hidden text removed and in-sentence notes made footnotes first (web-<host>-<hash>)
textfold/engine.py   the PDF engine: MinerU in a Python of its own under the app data folder; install, removal, conversion
textfold/pdftext.py  a PDF with its own text, read without the engine (pdf-inspector); figures found by their captions, cut with pypdfium2
textfold/pdfdoc.py   a converted PDF as a document: MinerU's structured output into Markdown and HTML (pdf-<file hash>;
                     another file, doc-<file hash>)
textfold/office.py   Word, PowerPoint, Excel, CSV, EPUB and saved web pages read without the engine, in its shape
textfold/omml.py     Office's formulas (OMML) as LaTeX
textfold/pdf.py      a paper's original PDF for the side-by-side view (source.pdf)
textfold/pdffigs.py  an arXiv drawing whose words the browser set (TikZ): cut from the paper's PDF when the reader asks
textfold/zotero.py   a Zotero library through Zotero Desktop's local API; an item read as a link or a dropped file
textfold/tokens.py   tokenizer, sentence splitter, LCS alignment
textfold/ladder.py   one model call per section; validation; written words aligned to source words for the morph
textfold/links.py    argument links, one call over the whole paper
textfold/translate.py  any language: sentence-aligned translation + short levels, one call per section (langs.py: the languages a text can be read in)
textfold/store.py    versioned layers per paper; assembling a reader from them
textfold/pipeline.py fetch → parse → ladder + links in the text's own language (once) → translation, with progress
textfold/server.py   local server: pages, /api/papers, /api/jobs (and /api/jobs/stop), /api/generate, /api/settings
textfold/agents.py   local agents: the scan, model catalogs, one-shot runs (claude -p, codex exec, pi -p)
textfold/providers.py API providers: ThoughtDAG's env registry and presets, /models probing
textfold/models.py   one catalog of agent and API models; settings; resolving a model id
textfold/llm.py      the call (cached) for every kind of model; llm_openai.py: OpenAI-compatible endpoints
textfold/build.py    reader pages: app mode (served) and static mode (one file)
textfold/library.py  the sidebar's library: folders, when each document came in and was last opened (library.json)
textfold/notes.py    the reader's notes (docs/notes-format.md) and questions answered by the model
textfold/fold.py     the TextFold format (docs/format.md) and a document at one level as Markdown (python3 -m textfold get)
textfold/web/   reader.js (render + FLIP morph), app.js (sidebar, landing, settings, jobs), i18n.js, CSS, templates
tests/          python3 -m unittest discover tests: the arXiv page's safety pass, the Zotero library (a fake Zotero),
                agents on Windows
desktop/       the app (its README): Electron around the same server, with its own Python; desktop/scripts/smoke.py
                checks that Python before it ships (.github/workflows/desktop.yml)
tools/zoombench.py  zoom smoothness as the screen sees it (GPU, 2x, Chrome's frame trace), on out/compare/<id>/ pages
tools/readme_assets.py  the README's pictures, made from a real paper in the reader
```

Atoms (formulas, citations, cross-references, footnotes) are numbered in reading order, and the stored layers name
them by number. A parser change that reads more of a page (description lists, October 2026) renumbers every atom after
the new ones, so a paper generated before it needs its layers made again, or its atom numbers mapped from the old parse
to the new one by content and order.
