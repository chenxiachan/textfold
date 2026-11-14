# A page from the reader's own browser

Status: in TextFold 0.10.40; the extension in the Chrome Web Store (lkfemlafmabkdjedldmjebdpccdaeglf).

TextFold fetches a web page without running its scripts and without the reader's sign-in. That misses a page the
reader can read and TextFold cannot: signed in, past a paywall they pay for, behind a bot wall (Medium, Stack
Overflow, The Economist), drawn by scripts, or kept in shadow roots. The page as the reader's browser drew it is the
one to read. Reading how to parse and show such a page comes after; this is how it comes.

## The way in

```
the reader clicks TextFold's button  ->  extension/background.js
  asks the page's MathJax, in the page's own world, for each formula's source (mathSources)
  runs extension/snapshot.js in the tab's frames (activeTab, scripting)
  reads the snapshot's article in the tab, as reading views do (extension/reader.js: Defuddle and Readability)
  sends {format: textfold/snapshot, version, url, title, html, read, taken} by native messaging
the browser starts TextFold's host  ->  python3 -m textfold capture-host (textfold/capture.py)
  checks it (a web page's address, a page, at most 64 MB, a make of message it knows)
  stores it as a web page of the library: web-<host>-<hash>, snapshot.html beside it, never over a page already there;
    its article the reading view's (webpage.view_of, read_view; article.html beside it), else TextFold's own reading
  asks the TextFold that runs for its levels, in the reader's language, as its landing does
  answers {ok, paper, new, open, job}: open is the reader's address when TextFold is running (server.json)
the extension shows ✓ (or ! and why) and opens the reader beside the page; no TextFold to reach: setup.html
```

The snapshot (`extension/snapshot.js`): shadow roots spelled out, closed ones too (`chrome.dom.openOrClosedShadowRoot`,
an extension's), their slots filled; each frame the tab lets it into put where its iframe is; an element the browser
does not show marked `data-tf-css-hidden`. Reading it, a hidden element hanging on a mark (1, [12], *) is a note, and
the rest stays out (`webpage._hover_marks`, `_hidden_out`).

## The article, as a reading view reads it

A browser's own reading view (Chrome's, Edge's, Safari's; Firefox's about:reader) cannot be asked for what it read.
What such views run can be: Readability is Firefox's, Defuddle is Obsidian's Web Clipper's. Both are in the
extension (extension/vendor, built by tools/defuddle/build.mjs) and read the snapshot in the tab, so that what its
shadow roots and frames hold is read too. Before they read it, what the browser did not show goes, but for a note
hanging on a mark, which becomes a footnote both know; and a formula MathJax drew (MathJax 4 leaves no MathML in the
page) becomes its source, which the page's MathJax gave in the page's own world: TeX, which TextFold draws, or
MathML with its LaTeX beside it.

TextFold takes Defuddle's reading, unless Readability's has half as many words again; neither, below 80 words. It
then drops what a publisher's page leaves at the article's head (a heading with nothing under it, buttons, a logo),
the bullets list items write themselves, and keeps a reference list Defuddle took for footnotes as a list.

Defuddle 0.19.5 is changed in five places (each in build.mjs, with why, and each to offer upstream):
a container that failed its check of footnotes gave its parts as footnotes all the same (Elsevier's whole text came
twice); a footnote list went even when no footnote was found in it (the references); an element of running text, or
one called an abstract, went because its class matched a byline's (div.abstract.author); a link in a sentence went
because its class matched a heading's permalink (a.anchor: the citations, (Fig. 1)); and a figure's caption was the
first element any caption selector matched (the download link before Elsevier's caption).

Tried on 24 pages read before with a recipe each (tools/web-recipes) and two the reader sent: the share of the
recipe's text kept went from 0.38–0.99 to 0.49–1.00 (react's documentation 0.43 to 0.97), headings, figures and
formulas are kept where TextFold's own reading lost them (transformer-circuits: 1 heading and 20 formulas, now 76 and
264; ScienceDirect: none of either, now 32 headings and 9 figures). What it does worse: a page of thumbnails drawn as
one figure (goodfire's) keeps few of them, sspai's news digest takes a quarter more words than its article, and a
paywall's teaser seen without a subscription (The New Yorker's) is read as the other text on its page.

## Reaching TextFold

The browser starts a native messaging host by its manifest, which names the program and the extensions it lets in.
On macOS and Linux the manifest is a file in each browser's folder for it; on Windows it is one file, which a key of the
registry names for each browser (HKEY_CURRENT_USER\Software\Google\Chrome\NativeMessagingHosts\app.textfold.capture,
and Edge's, Chromium's, Brave's). The program is a launcher in ~/.textfold: a shell script, or on Windows a batch
file, which cmd.exe reads in the console's code page rather than UTF-8, so the reader's own folders in it are written
as %LOCALAPPDATA%, %APPDATA% and %USERPROFILE% (a user name in Chinese would otherwise be lost).

The desktop app registers the host each time it starts (`capture install --app`), with its own Python, run as it runs
its server (its own packages alone, UTF-8) and under its name for its runtime, which says where the library was moved
to in Settings: wherever the app was moved, and whichever version it is now, the browser starts that copy. A copy of
the app not yet moved out of the Downloads folder (a Mac's App Translocation, gone when it quits) does not register.
TextFold from pip registers once, by hand. Every install lets in the extension as the store gives it (`STORE`, its id
fixed by the key in extension/manifest.json, so that a copy loaded unpacked has it too) and keeps the extensions a
manifest written before let in.

Each message says the make it is of (`version`, capture.py's `PROTOCOL`): a newer one than this TextFold knows is
refused with `update-textfold`. A hello (`textfold/hello`) is answered with TextFold's version, its make of message,
and whether it runs. When the browser cannot reach the host (no manifest, a manifest that does not let this extension
in, a program that did not run) or TextFold is older, the extension opens a page of its own that says what to do, in
the browser's language, and asks again (setup.html, also its options); it opens once the extension is installed, when
no TextFold answers.

## Why this way

- **Only an extension reaches the session the reader is using.** Since Chrome 136 the remote-debugging port no
  longer opens the default profile, because cookie stealers used it; copying a profile gives a stale copy and acts
  like one. Chrome 144's auto-connect asks every session and is Chrome's alone.
- **No `debugger`.** Tools that let an agent drive the browser (Playwright MCP's extension, chrome-devtools-mcp)
  need it, and with it every tab. Reading the page the reader clicked on needs `activeTab`; folding in place later
  needs a content script, and a site's optional permission to do it unasked. An agent works on the snapshot, never
  on the browser.
- **No port.** Native messaging: the browser starts the host and lets only the extensions named in its manifest
  reach it. Local servers without authentication have been reached by pages and other extensions.
- **The cookies stay.** What is sent is the page as drawn; it stays in the reader's library.

## Tried

`tools/capture_e2e.py`: the extension in Playwright's Chromium; before its host is registered, setup.html opened once
it is installed and again by a click, saying what to do; then the host registered in that browser's own profile as the
app registers it, setup.html saying it answers, and pages served locally: a subscriber's article (a cookie; without it, a teaser too short to read), a page drawn by its
script, text in an open and in a closed shadow root with a hidden note on a mark, a page in a frame, a publisher's
article as Elsevier draws one, formulas MathJax 4 drew, the same page again (not written over), and the reader opened
beside the page while TextFold runs, its levels asked for. `tests/test_capture.py` and `tests/extension.test.cjs`
hold the rest without a browser; among them the app's launcher run as the browser runs it, a hello in and TextFold's
answer out, which the app's build runs with the Python it ships on each system (on Windows by cmd.exe, as Chrome starts
a batch file, its key in the registry read back).

## Not yet

- Edge's Add-ons (the Chrome Web Store's extension, id lkfemlafmabkdjedldmjebdpccdaeglf, `STORE`, given by the
  manifest's key, installs in Edge too): they give an id of their own, to let in beside it. Firefox (`browser.runtime.connectNative`, the same host, its manifest
  naming allowed_extensions), Safari (an extension inside a signed app).
- Chrome on Windows itself: the registry and the batch file are tried by the tests, not yet by a browser there.
- A page read again: a newer snapshot of a page already in the library (kept beside the old, or replacing it).
- Deciding whether a page is an article at all (a feed, a front page) before reading it.
- A page sent while TextFold is not running: its levels wait until it is opened and asked for.
- Folding the live page in place: each paragraph read found again on the page (Readability keeps the page's
  attributes; Defuddle's text is found by its words, as Hypothesis anchors a note), its levels shown there.
