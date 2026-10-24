# Notes format (`textfold/notes`, version 1)

A reader's highlights, notes and questions on one paper. One file per paper: `papers/<arXiv id>/notes.json` when the
local server runs; in a static page, the same records in the browser's storage. Everything that leaves the reader
(Markdown for Obsidian, and later ThoughtDAG nodes) is converted from this format, so it is the one contract between
them. Files written under earlier names say `"format": "paperfold/notes"` or `"dynamic-reader/notes"`; it is the same
format, and a reader of it accepts all three names. It follows the W3C Web Annotation model loosely: a note has a body and a target, and the target is anchored in
more than one way, so it can be found again when the text around it changes.

```json
{
  "format": "textfold/notes",
  "version": 1,
  "made_by": "TextFold · https://github.com/chenxiachan/textfold",
  "paper": { "id": "2605.00089", "version": "v1", "title": "…", "url": "https://arxiv.org/abs/2605.00089v1" },
  "notes": [
    {
      "id": "n-k3f9a2qx",
      "kind": "highlight",
      "created": "2026-10-03T10:00:00Z",
      "updated": "2026-10-03T10:00:00Z",
      "anchor": {
        "unit": "S2.SS3.p2",
        "section": "S2.SS3",
        "lang": "en",
        "level": 4,
        "tokens": [12, 30],
        "sents": [1],
        "quote": { "exact": "…", "prefix": "…", "suffix": "…" }
      },
      "text": "",
      "thread": []
    }
  ]
}
```

## Fields

- **id**: stable for the life of the note. In Markdown it becomes the Obsidian block id (`^n-k3f9a2qx`), so other
  notes can link to it, and the reader opens at it with `#n=<id>`.
- **kind**: `highlight` (marked text, maybe with a note), `note` (written first), `ask` (a conversation with the model
  about the passage: a question, its answer, follow-up questions and theirs).
- **anchor**, four ways of finding the passage again, from the most precise to the most robust:
  - `unit`: the paragraph (or list item, caption) in the parsed paper. `section` is its section, for grouping. A note
    can also be on a section itself: then `unit` is null, `section` is the section, and `on` says where, `summary` (the
    section's one-line summary, written by the model; `tokens` count its words) or `title`. A conversation about the
    whole paper (the reader's chat) has `on: "paper"`, `unit` and `section` null, and an empty quote; it is listed
    first, under "The whole paper". So does a note of the reader's own on the whole paper, not on a passage (`kind:
    "note"` with its `text`, written from the notes menu): it is shown by the paper's title, at every level.
  - `lang`, `level`: the language and zoom level (4 Full, 3 Brief, 2 Key, 1 Takeaway, 0 Topic; the numbers are what is stored, the names are the reader's) it was made at.
  - `tokens`: first and last token, inclusive, in the unit's full text in `lang`. A selection in a written level
    (Key, Takeaway) maps back to the words of the full text the model's words came from; `null` when the selection
    held only words the model wrote itself.
  - `sents`: the sentences of the unit the passage lies in. Translation is sentence by sentence, so these hold in every
    language: a note made in Chinese is shown on the same sentences in English.
  - `quote`: the selected text exactly, with up to 40 characters before and after (W3C `TextQuoteSelector`). This is
    what finds the passage again when ids and token numbers no longer fit (a Markdown text replaced by its edited
    version, below; a new arXiv version of the paper). For a selection in a written level, the words of the full text
    from `tokens`.
  - `shown`, only on a note made on a written line (Key, Takeaway): the words of the line selected, the model's too,
    as `{ "words": [first, last], "exact": "…" }`, where `words` count the words of the unit's Key line in `lang`
    (Takeaway is its first sentence, so a word keeps its place in both) and `exact` is those words as the line read
    then. `exact` tells whether the line still reads so: a refold writes the line again.
  - `lost`, only on a note whose passage was not found again after the text changed: where it was, `{ "unit",
    "section", "tokens", "sents", "since" }` (and, for a note made in a translation, `quote`: its sentences in the
    text's own words, to look for). The note is kept where the reader still lists and shows it: on the paragraph it was
    on, when that is still there edited (`tokens` and `sents` null), else on its section's title (`unit` null, `on:
    "title"`). Its own `quote` stays as it was.
- **text**: the reader's own note, plain text with line breaks.
- **shade**, optional, on a `highlight` or `note`: `1` light or `3` deep, as the reader chose it; without it the
  shade is the usual one (2). A note keeps no colour of its own: its colour is the one of the level it was made at
  (below), so a reader who changes a level's colour sees all of its notes in the new one. In Markdown a shade is a tag
  under the block id, `#highlight/light` or `#highlight/deep`.
- **thread**: the turns of an `ask` note, in order, each `{ "q", "a", "takeaway", "cites", "next", "model", "lang", "at" }`:
  the question, the answer (Markdown), the answer in one sentence, the units of the paper the answer rests on (in the
  order the answer first cites them), two questions to go on with, the model id, the answer's language and when it was
  given. The answer cites inline: `[[S3.p2.1]]` right after a statement is the unit it rests on, `[[?]]` ends a sentence
  that is the model's own inference rather than the paper's, and `[[!12]]` is a citation that named no unit (kept, so it
  shows as one that went wrong). Answers from before inline citations have none and list their units in `cites` only. Each question is asked with the turns before it (the last
  six), so a follow-up can say "and why is that?". While the model is answering, the last turn has an empty `a` and
  `pending: true`; when it failed, `error`. (The first notes stored a single turn as `ask`; it reads as a thread of
  one.)

## Converting

- To Obsidian (done): one Markdown note per paper; a highlight is a quote with the block id `^<id>`; a conversation is a
  `[!question]` callout, the first question its title and each follow-up in bold; every note links back to the reader.
  A note made on a written line quotes the line's words (`shown`) and says that they are the model's, and at which level.
  The note ends with a line saying it was made with TextFold.
- To PDF (done): the browser prints the notes in reading order (each passage, the reader's note, the conversation
  with its cites and the level it was asked at), with the TextFold mark at the end.
- To ThoughtDAG (planned): a highlight or note becomes a material node quoting the passage (paper id, unit, quote); a
  conversation becomes a chain of question nodes hanging from it, one per turn.

## How a note is shown at each level

A mark is shown where it was made, in the colour of that level: Full and Brief (the author's words) share one, Key
and Takeaway have one each (yellow, purple and blue at first; the reader picks each in the Aa menu, among eight), in
the note's shade. Where marks overlap, the latest note's shade shows. The note's card, its line in the notes list and
the paragraph's mark (a band for each level its notes were made at) take the same colours. The levels leave each
other's marks alone.

- Full, Brief: the words in `tokens` of a note made there are marked when the note's language is the one being read;
  in another language, the sentences in `sents` are marked more lightly. With "Keep my highlights" on, these
  passages also stay after the written line at Key and Takeaway, in the author's words.
- Key, Takeaway: the words in `shown` of a note made on the line, all of them, the model's too, at both levels (the
  words of Key past its first sentence at Key alone), while `exact` still reads so in the language being read. In
  another language nothing is marked; nor where the line has been written again since, and the note says so. A note made
  at Key or Takeaway before `shown` was kept has the written words that came from its tokens marked, as then.
- Wherever none of the passage is on screen (a sentence the Brief level leaves out, a chip on the Topic map), the
  paragraph carries a mark, so nothing a reader wrote disappears when they zoom out.
- The reader's own notes (with text) sit in the left margin, the conversations with the model in the right margin
  above the paragraph's links: at Full and Brief in full, at Key shortened to the question and its one-line answer.
  At Takeaway and Topic only the paragraph's mark stays; hovering it shows its notes.
- A note on a section's summary is marked on the summary (shown at Key, Takeaway, Topic); its cards hang after the
  section's heading, which also carries a mark.
- A conversation keeps the level it was started at (`anchor.level`), shown with it: what was selected may not be on
  screen at another level.

## When the text changes

A Markdown document can be replaced by its edited text under the same id (`POST /api/import` with `id`), and a PDF
converted again; its notes are then found again in the new text (`notes.reanchor`), before its levels are made again.
A paragraph or section still there, edited, moved or renamed, keeps its id (`ids.py`), so a note's `unit` and a link's
`u=` still name it; one new in the text gets an id no paragraph of the document had before; one that is gone is kept in
`ids.json` beside the document, with the section it was in (`GET /api/ids/<id>`).

- A paragraph still there word for word keeps its notes as they are.
- A note on a paragraph edited or moved is found by its `quote`: the places with its exact words, scored by how much of
  `prefix` and `suffix` fits around them (of several, the paragraph it was on wins a tie, then the place nearest the old
  one; one whose surroundings do not fit is not guessed at); else, when its own words were edited, what now stands
  between its `prefix` and `suffix`. `unit`, `section`, `tokens`, `sents` and `quote` are then those of the place found.
- A note made in a translation (`lang` not the text's own) is found by the sentences it lies in, in the text's own
  words: `sents` is set again and `tokens` is null, since the translation is made again.
- A note on words the model wrote (`tokens` null) goes with its paragraph when that is still there, edited.
- A note found nowhere is kept and marked `lost` (above); the next replace looks for it again, and a note found again
  loses the mark.
- A note on a section keeps it while the section is there, renamed or not; a section gone, it follows the section's
  paragraphs. The cites of a conversation (`thread[].cites`, `[[id]]` in an answer) follow the paragraphs they name.

## Not yet

- Finding a passage again by its quote in a regenerated summary (a note on a section's summary), or in a new arXiv
  version: the quote is stored for it.
- Handwriting. When it comes it will be a body of its own (strokes) on the same anchors.

## Conversation graphs and retained revisions

New notes can include `messages: [{id, parent, role, text, ...}]` and `branches`
(leaf message IDs). `thread` is a compatibility projection of one branch; it must
not be used to reconstruct or overwrite a stored graph. All messages remain in
JSON/document exports even when the UI is viewing one branch. Legacy linear
threads convert deterministically when exposed to the new reader or sync layer.
Confirmed messages are immutable; concurrent replies preserve separate children.

The root can contain `conflicts` and `aliases`. A conflict keeps its merge base,
two revisions, target record key and deletion intent until explicitly resolved.
Aliases map duplicate equivalent-highlight IDs to the retained canonical ID.
Browser responses also provide `_base`/`_revision` edit tokens on notes; these
are transient and are not part of the persisted/exported notes format.
