// The article in the snapshot of a tab (snapshot.js), read the way a browser's reading view reads a page: by Defuddle
// (Obsidian's Web Clipper, with TextFold's changes: tools/defuddle/build.mjs) and by Readability (Firefox's reading
// view), both in vendor/. A browser's own reading view cannot be asked for what it read; these are what such views
// run. The snapshot is read rather than the tab, so that what its shadow roots and frames hold is read too, and what
// the browser did not show is known (data-tf-css-hidden): such an element hanging on a mark (1, [12], *) is a note
// shown on hover, and becomes a footnote either reader keeps; the rest of them go. TextFold chooses between the two
// readings (textfold/capture.py), and reads the snapshot itself when neither will do. A formula the page's MathJax
// drew is read from its source, which background.js asked MathJax for. Run in the tab's page (the service worker has
// no DOM): self.tfRead(html, url) -> {defuddle, readability, notes, formulas, errors}.
(() => {
  const MARK = /^(?:\[?(?:\d{1,3}[a-z]?|[a-z]|[*†‡§¶⊕]{1,3})\]?|\(\d{1,3}\))$/i;   // 1, [10], 3a, *, †, (2)
  const BLOCK = 'p, li, dd, dt, td, th, figcaption, blockquote, h1, h2, h3, h4, h5, h6';
  const bare = (s) => (s || '').replace(/\s+/g, '');
  // a formula (as mathOut leaves it, MathML) other than in the note itself: never a note's mark, whatever it reads
  // (n, 0, 16: Zhihu keeps each formula's TeX hidden beside its drawing)
  const formula = (el, note) => el.localName === 'math' || [...el.querySelectorAll('math')].some((m) => !(note && note.contains(m)));

  // the element a hidden one hangs on, if it reads as a mark: the one just before it (nothing written between), the
  // one holding it, or (a tooltip) the one that names it in its aria-describedby
  function markOf(el, named) {
    if (el.getAttribute('role') === 'tooltip' && el.id && named.has(el.id)) {
      const m = named.get(el.id);
      return !formula(m, el) && MARK.test(bare(m.contains(el) ? m.textContent.replace(el.textContent, '') : m.textContent)) ? m : null;
    }
    let prev = el.previousSibling;
    while (prev && prev.nodeType !== 1 && !bare(prev.textContent)) prev = prev.previousSibling;
    if (prev && prev.nodeType === 1 && !prev.hasAttribute('data-tf-css-hidden') && !formula(prev) && MARK.test(bare(prev.textContent))) return prev;
    const parent = el.parentElement;
    if (parent && parent.localName !== 'body' && !formula(parent, el) && MARK.test(bare(parent.textContent.replace(el.textContent, '')))) return parent;
    return null;
  }

  // what the browser did not show: a note on its mark becomes a footnote (its mark a link to it, its words in a
  // footnote list just after the mark's paragraph, which both readers know); the rest goes
  function hiddenOut(doc) {
    const named = new Map();
    for (const el of doc.querySelectorAll('[aria-describedby]')) {
      for (const id of el.getAttribute('aria-describedby').split(/\s+/)) if (id && !named.has(id)) named.set(id, el);
    }
    let k = 0;
    for (const el of [...doc.querySelectorAll('[data-tf-css-hidden]')]) {
      if (!el.isConnected || (el.parentElement && el.parentElement.closest('[data-tf-css-hidden]'))) continue;
      const mark = el.textContent.trim() ? markOf(el, named) : null;
      if (!mark || !mark.isConnected) { el.remove(); continue; }
      k += 1;
      const id = `fn:tf${k}`;   // (Defuddle pairs fn:<id> with a sup#fnref:<id>, normalize a list's #fn:<id> with its link)
      const item = doc.createElement('li');
      item.id = id;
      const p = doc.createElement('p');
      p.append(...el.childNodes);
      item.append(p);
      el.remove();
      const said = bare(mark.textContent) || String(k);
      const sup = doc.createElement('sup');
      sup.id = `fnref:tf${k}`;
      const a = doc.createElement('a');
      a.href = `#${id}`;
      a.textContent = said;
      sup.append(a);
      const block = mark.closest(BLOCK) || mark.parentElement;
      mark.replaceWith(sup);
      const list = doc.createElement('section');
      list.className = 'footnotes';
      const ol = doc.createElement('ol');
      ol.append(item);
      list.append(ol);
      (block && block.isConnected && block !== doc.body ? block : sup).after(list);
    }
    return k;
  }

  // a formula MathJax drew, whose source background.js found (data-tf-math): that source in its place, as MathML (the
  // source's own, its LaTeX beside it; or TeX in its annotation, which TextFold draws), the drawing gone. In the place
  // of all that draws it: a span around it holding nothing else a reader sees (Zhihu's span.ztext-math: an empty
  // preview, the drawing, its TeX hidden) goes with it, or a reading view takes the span for the formula (Defuddle's
  // [data-tex]) and reads it inline, a display formula too, and the hidden TeX beside it for a note on it
  const NS = 'http://www.w3.org/1998/Math/MathML';
  const besides = (box, el) => [...box.childNodes].every((n) => n === el || (n.nodeType === 3 ? !bare(n.textContent)
    : n.nodeType !== 1 || n.hasAttribute('data-tf-css-hidden') || n.localName === 'script'
      || (!bare(n.textContent) && !n.querySelector('img, svg, math, video, canvas'))));
  const drawnBy = (el) => {
    let at = el;
    while (at.parentElement && at.parentElement.localName === 'span' && besides(at.parentElement, at)) at = at.parentElement;
    return at;
  };
  function mathOut(doc) {
    let k = 0;
    for (const el of [...doc.querySelectorAll('[data-tf-math]')]) {
      if (!el.isConnected) continue;
      const src = el.getAttribute('data-tf-math');
      let m = null;
      if (el.getAttribute('data-tf-math-in') === 'mathml') {
        const box = doc.createElement('div');
        box.innerHTML = src.replace(/<(\/?)[A-Za-z][\w-]*:/g, '<$1');   // (mml:math, as Elsevier writes it: math)
        m = box.querySelector('math');
        if (m && self.TFMathMLToLaTeX) {   // (data-latex, which Defuddle asks first: else it takes the formula's bare text)
          try { m.setAttribute('data-latex', m.getAttribute('alttext') || self.TFMathMLToLaTeX.convert(m.outerHTML)); } catch (e) { /* its MathML alone */ }
        }
      }
      if (!m) {
        m = doc.createElementNS(NS, 'math');
        const sem = doc.createElementNS(NS, 'semantics');
        const ann = doc.createElementNS(NS, 'annotation');
        ann.setAttribute('encoding', 'application/x-tex');
        ann.textContent = src;
        sem.append(ann);
        m.append(sem);
        m.setAttribute('alttext', src);
        m.setAttribute('data-latex', src);
      }
      m.setAttribute('display', el.getAttribute('data-tf-math-display') === 'block' ? 'block' : 'inline');
      drawnBy(el).replaceWith(m);
      k += 1;
    }
    return k;
  }

  const words = (s) => {
    const t = (s || '').replace(/\s+/g, ' ').trim();
    return t ? t.split(' ').length + Math.round((t.match(/[぀-ヿ㐀-鿿가-힯]/g) || []).length / 2) : 0;
  };

  self.tfRead = (html, url) => {
    const out = { notes: 0, formulas: 0, errors: [] };
    const parsed = () => {
      const doc = new DOMParser().parseFromString(html, 'text/html');
      out.formulas = mathOut(doc);
      out.notes = hiddenOut(doc);
      return doc;
    };
    try {
      const r = new self.Defuddle(parsed(), { url, useAsync: false }).parse();
      const text = new DOMParser().parseFromString(r.content || '', 'text/html').body.textContent;
      out.defuddle = { html: r.content || '', title: r.title || '', author: r.author || '', published: r.published || '',
                       site: r.site || '', words: words(text) };
    } catch (e) { out.errors.push(`defuddle: ${e && e.message}`); }
    try {
      const r = new self.Readability(parsed()).parse();
      if (r) out.readability = { html: r.content || '', title: r.title || '', author: r.byline || '', words: words(r.textContent) };
    } catch (e) { out.errors.push(`readability: ${e && e.message}`); }
    return out;
  };
})();
