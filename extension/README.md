# TextFold in the browser

A button that sends the page you are reading to TextFold on this computer, as you see it: signed in, past a paywall
you pay for, drawn by the page's scripts, with what it keeps in shadow roots and frames. Its article is read in the
browser the way reading views read a page (Defuddle, Obsidian's Web Clipper's, and Readability, Firefox's: in
vendor/), and TextFold folds it. Status: in TextFold 0.10.40, and in the Chrome Web Store
(https://chromewebstore.google.com/detail/textfold/lkfemlafmabkdjedldmjebdpccdaeglf; docs/web-capture.md).

## Use it (Chrome, Edge, Brave)

1. Install TextFold for macOS or Windows (https://chenxiachan.github.io/textfold/) and open it once. Each time it
   starts, it registers the program the browser starts to reach it (TextFold's native messaging host,
   `app.textfold.capture`) for your user, with its own Python, and lets the extension in.
   With TextFold from pip instead (Linux too): `python3 -m textfold capture install`, once.
2. Add the extension from the Chrome Web Store (Brave the same; Edge allows extensions from other stores first). To
   run this folder as it is instead (working on it): open `chrome://extensions`, turn on **Developer mode**, choose
   **Load unpacked** and pick this folder, after removing the store's copy (both have the same id: below).
3. On any page, click the TextFold button. A ✓ on it says the page is in your library; when TextFold is running, its
   reader opens beside the page. A ! says why not (hover the button). When the browser cannot reach TextFold (not
   installed, older than the extension, moved since it last ran), a page of the extension says what to do and checks
   again (setup.html; also the extension's options).

Its words are in the browser's language when TextFold's own interface has it (`_locales`), else in English.

## A copy of your own

The store's extension (lkfemlafmabkdjedldmjebdpccdaeglf) is let in by every install. A copy of this folder loaded
unpacked has the same id, given by the manifest's `key` (the public half of the store's, as its dashboard shows it); a
copy without the key has an id of its own (its card in `chrome://extensions`), let in with

```bash
python3 -m textfold capture install --extension <id>
```

An install keeps the extensions a manifest written before let in. `python3 tools/pack_extension.py` makes the zip the
stores take (dist/textfold-extension-<version>.zip): this folder without README.md and without the key.

## What it asks for

- `activeTab`: the tab you click it on, while you stay on that page. No other page is read, and none is read before
  you click.
- `scripting`: to run its own `snapshot.js` and `reader.js` in that tab, and to ask the page's MathJax, if it has
  one, for the source of each formula it drew. It runs no code it did not ship with.
- `nativeMessaging`: to hand the page to TextFold through the browser. No port is opened on this computer, and only
  the extensions TextFold lets in can reach it.

Your cookies stay in the browser: what is sent is the page as it was drawn. It goes to your library on this computer
and nowhere else. TextFold writes its levels with the model you chose in its settings, as for any other document.
