// TextFold: the paper at five zoom levels, morphing word by word.
// Level 4 Full · 3 Brief (the author's key sentences) · 2 Key (written: takeaway and reason) · 1 Takeaway · 0 Topic.
// (Until v0.9 the reader called 3 "Key" and 2 "Brief"; the code's comments still say so.) Every visible piece
// (word, formula, dot, heading, figure) is an element with a data-id that
// persists across levels; a level change is FLIP: measure, re-render, put each
// surviving piece back where it was, then let it slide home. A formula keeps
// its id across languages too, so switching EN/中文 slides it to its new place.
// A zoom gesture (wheel, pinch, slider, keys) does not lay anything out while it
// runs: it plays keyframes of the screen at each level, prepared while the reader
// was still ("the stage"), and the page takes over again at rest.
// Links: the author's cross-references and the model's argument links, as
// sidenotes (Full, Key, Brief), and on the Topic map by
// letting a chip's linked chips stay lit while the rest of the map steps back.
(() => {
  'use strict';
  const D = JSON.parse(document.getElementById('paper-data').textContent);
  const U = D.units, A = D.atoms, EDGES = D.edges || [], ALIAS = D.alias || {};
  const BAR = [4, 3, 2, 1, 0];
  const ROLES = ['claim', 'gap', 'method', 'setup', 'result', 'analysis', 'limitation', 'context'];
  const DUR = 560;
  const BRAND = 'TextFold', REPO = 'https://github.com/chenxiachan/textfold';
  const LOGO = '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2" y="3" width="16" height="2.4" rx="1.2" fill="#6B5CE7"/>' +
    '<rect x="2" y="8.8" width="11" height="2.4" rx="1.2" fill="#6B5CE7" fill-opacity=".4"/><rect x="2" y="14.6" width="5" height="2.4" rx="1.2" fill="#E08A3C"/></svg>';
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  // languages: the interface speaks the language the paper is read in (i18n.js)
  const I18N = window.DR_I18N;
  // a research paper, or a text of another kind (a web page, a README, a PDF, a letter): the questions about it say which
  const SRC = (D.meta.source || {}).kind;
  const PAPER = ['europepmc', 'biorxiv', 'medrxiv'].includes(SRC) || (!SRC && /^\d{4}\.\d{4,5}$/.test(D.meta.id));
  const T = Object.fromEntries(I18N.CODES.map((c) => [c, I18N.t(c, !PAPER)]));
  // One language, the reader's (`readerLang`): chosen in the app, else the browser's. The paper is read in `lang`: its
  // text, and the names of its own parts (figures, tables, theorems, references, equations), in English where the
  // interface has no words for that language. The interface speaks `ui`: the reader's language when it has the words,
  // else English. A switch of either is a switch of both (setLang, setUi). A paper that does not have the reader's
  // language yet opens in one it has, and the interface stays as it was.
  const UI_KEY = 'pf-ui-lang';
  const NAMES = D.langs || I18N.NATIVE;   // every language a text can be read in, by its own name (build.py)
  const nameOf = (code) => NAMES[code] || I18N.NATIVE[code] || code;
  let readerLang = (() => { let v = null; try { v = localStorage.getItem(UI_KEY); } catch { /* private window */ } return v && (T[v] || NAMES[v]) ? v : I18N.guess(); })();
  let ui = I18N.uiFor(readerLang);
  const BASE = D.meta.lang || 'en';   // the language the text is written in, its own levels' (store.text_lang): never a translation
  const AVAIL = (D.meta.langs && D.meta.langs.length) ? D.meta.langs : [BASE];
  const STALE = new Set(D.meta.stale || []);
  // the paragraphs a language's translation has none for (store.assemble: a call that failed), by language; the app
  // asks for them as the language is opened, and its menu can ask again
  const GAPS = D.meta.gaps || {};
  const gapTag = (code, t) => (GAPS[code] && !STALE.has(code) ? `<em class="stale">${esc(t.gaps_tag.replace('{n}', GAPS[code]))}</em>` : '');
  // a document in Chinese or Japanese (store.assemble): the CJK rules below are its only, an English paper reads as before
  const CJK_DOC = !!D.meta.cjk;
  const trOf = (x) => (x && x.tr && x.tr[lang]) || null;   // a unit's or chunk's translation, if any (none in BASE)

  const perf = (name, t0) => { if (window.__drPerf) window.__drPerf.push([name, Math.round((performance.now() - t0) * 10) / 10]); };
  const docEl = document.getElementById('doc');
  docEl.classList.add('doc');   // the page's CSS speaks of .doc, which the keyframes' sandbox is too
  if (CJK_DOC) docEl.classList.add('cjk');
  const ghostEl = document.getElementById('ghosts');
  let level = 4;
  let lang = BASE;
  const localLevel = new Map();           // uid -> level the reader opened it to
  const findMarks = new Map();            // uid -> Map(piece id -> 1, 2 for the one stepped to): the words found (⌘F)
  const finder = { open: false, q: '', hits: [], at: -1, opened: null };   // (opened: the paragraph a step opened at Full)
  const unitEl = new Map();               // uid -> element
  const spans = new Map();                // uid -> Map(piece id -> span)
  const figOf = new Map();                // caption uid -> figure element
  const bareFigs = [];                    // figures without a caption follow the global level
  const lvBlocks = [];                    // blocks that show or hide by level: equations, raw blocks, title-page leftovers, bare figures
  const chunkOf = {};                     // uid -> chunk
  const headEls = [];                     // [chunk, heading element, stake element]
  const eqEls = [];                       // the equations set apart, each with the paragraph it goes with (_unit)
  D.chunks.forEach((c) => c.units.forEach((uid) => { chunkOf[uid] = c; }));
  const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch { /* private window */ } } };
  // the type as this reader likes it, for every text (kept in this browser): its size, the column's width, the font
  // of the author's words, the margins hidden, the colours, the line spacing and the colour of the highlights made at
  // each level (reader.css: --fs, html[data-width], html[data-font], html[data-hide]: the links and the conversations
  // on the right, the reader's notes on the left; html[data-theme]: light or dark, the system's when none, and
  // html[data-style]: the colours of either, the default's when none; --lh; --lv-*); set before anything is drawn, and
  // changed from the Aa menu beside the language's
  const TYPE_KEY = 'textfold.type';
  const SIZES = [0.86, 0.93, 1, 1.08, 1.16, 1.25, 1.35], WIDTHS = ['narrow', 'standard', 'wide'], MARGINS = ['links', 'asks', 'notes'];
  const MODES = ['auto', 'light', 'dark'], STYLES = ['default', 'paper'], LEADS = { tight: 0.9, standard: 1, loose: 1.15 };
  // the colours a level's highlights may be given (reader.css, --c-*: the same), a fill and an ink in the light, then in
  // the dark; and each level's (Full and Brief, the author's words; the written line at Key, at Takeaway) at first
  const HUES = {
    yellow: ['#FFE07A', '#D9A400', '#8A7024', '#E8C04A'], orange: ['#FFCB99', '#E07A1F', '#85501F', '#F2A65E'],
    red: ['#FFC2BA', '#D2493B', '#84403A', '#F2958A'], pink: ['#F9C4D8', '#D0487A', '#7A3550', '#F08CB4'],
    purple: ['#D3CAFF', '#6B5CE7', '#4A3F94', '#B1A6EE'], blue: ['#C6E2F7', '#3B82C4', '#2B5675', '#7DB8EA'],
    teal: ['#B8E6E1', '#2A9187', '#2E615C', '#7FD3C8'], green: ['#BDE8A8', '#4E9A3A', '#3E6B33', '#8FD27A'],
  };
  const HL0 = { src: 'yellow', key: 'purple', take: 'blue' };
  const TYPE0 = { size: 1, width: 'standard', font: 'serif', hide: [], mode: 'auto', style: 'default', lead: 'standard', hl: HL0 };
  let type = (() => {
    let v = null;
    try { v = JSON.parse(store.get(TYPE_KEY) || 'null'); } catch { /* (written by hand) */ }
    if (!v || typeof v !== 'object') return { ...TYPE0 };
    return { size: SIZES.includes(v.size) ? v.size : 1, width: WIDTHS.includes(v.width) ? v.width : 'standard', font: v.font === 'sans' ? 'sans' : 'serif',
      hide: Array.isArray(v.hide) ? MARGINS.filter((k) => v.hide.includes(k)) : [],
      mode: MODES.includes(v.mode) ? v.mode : MODES.includes(v.theme) ? v.theme : 'auto',   // (theme: a demo's name for it)
      style: STYLES.includes(v.style) ? v.style : v.theme === 'paper' ? 'paper' : 'default', lead: LEADS[v.lead] ? v.lead : 'standard',
      hl: Object.fromEntries(Object.keys(HL0).map((g) => [g, v.hl && HUES[v.hl[g]] ? v.hl[g] : HL0[g]])) };
  })();
  function applyType() {
    const r = document.documentElement;
    if (type.size === 1) r.style.removeProperty('--fs'); else r.style.setProperty('--fs', String(type.size));
    if (type.width === 'standard') delete r.dataset.width; else r.dataset.width = type.width;
    if (type.font === 'serif') delete r.dataset.font; else r.dataset.font = type.font;
    if (type.hide.length) r.dataset.hide = type.hide.join(' '); else delete r.dataset.hide;
    if (type.mode === 'auto') delete r.dataset.theme; else r.dataset.theme = type.mode;
    if (type.style === 'default') delete r.dataset.style; else r.dataset.style = type.style;
    if (type.lead === 'standard') r.style.removeProperty('--lh'); else r.style.setProperty('--lh', String(LEADS[type.lead]));
    for (const g of Object.keys(HL0)) {   // (reader.css: --lv-*, the level's colour, and its ink)
      if (type.hl[g] === HL0[g]) { r.style.removeProperty(`--lv-${g}`); r.style.removeProperty(`--lv-${g}-ink`); }
      else { r.style.setProperty(`--lv-${g}`, `var(--c-${type.hl[g]})`); r.style.setProperty(`--lv-${g}-ink`, `var(--c-${type.hl[g]}-ink)`); }
    }
  }
  applyType();

  // ── a unit in the current language ───────────────────────────────────
  function view(uid) {
    const u = U[uid];
    const z = trOf(u);
    const l = z ? z.lad : u.lad;
    // glue: the half of a sentence a page break left in the unit before this one (ladder.dress)
    if (z) return { toks: z.toks, sents: z.sents, ev: u.lad.ev.filter((s) => s < z.sents.length), brief: l.brief, tn: l.tn, topic: l.topic, glue: l.glue, p: `${lang}~` };
    return { toks: u.toks, sents: u.sents, ev: u.lad.ev, brief: l.brief, tn: l.tn, topic: l.topic, glue: l.glue, p: '' };
  }

  // ── pieces: what a unit shows at a level ─────────────────────────────
  function pieces(uid, L) {
    const v = view(uid), p = `${uid}:${v.p}`;
    const ev = new Set(v.ev);
    // with "keep my highlights" on, the passages the reader marked are a level of their own: they stay as the paper
    // zooms out (the sentences at Brief, the author's words after the written line at Key and Takeaway)
    const mk = marksFor(uid), mine = keepMine && mk && L > 0 && L < 4 ? [...new Set([...mk.exact, ...mk.soft])].sort((a, b) => a - b) : null;
    let out;
    if (L >= 4) {
      out = [];
      v.sents.forEach(([a, b], s) => { for (let i = a; i < b; i++) out.push({ id: p + i, i, tok: v.toks[i], lead: ev.has(s) }); });
    } else if (L === 3) {
      const keep = new Set(v.brief.filter((t) => t.m !== undefined).map((t) => t.m));
      out = [];
      let prevEnd = null;
      const sents = mine ? [...new Set([...v.ev, ...v.sents.map((r, k) => [r, k]).filter(([r]) => mine.some((i) => i >= r[0] && i < r[1])).map(([, k]) => k)])].sort((a, b) => a - b) : v.ev;
      for (const s of sents) {
        const [a, b] = v.sents[s];
        if (prevEnd !== null && a !== prevEnd) out.push({ id: `${p}gap${s}`, gap: true });
        for (let i = a; i < b; i++) {
          if (!CJK_DOC) { out.push({ id: p + i, i, tok: v.toks[i], lead: keep.has(i) }); continue; }
          const raw = v.toks[i], txt = raw && raw.t !== undefined ? raw.t : raw;
          // a CJK footnote marker (①-⑩, ⑴-⒑, ⒈-⒑) glued into a sentence: dropped for display; a token that is
          // nothing but markers is not rendered at all (Full keeps the text as written). † ‡ § ¶ are left alone:
          // English papers use them as ordinary marks
          const clean = String(txt).replace(/[①②③④⑤⑥⑦⑧⑨⑩⑴⑵⑶⑷⑸⑹⑺⑻⑼⑽⒈⒉⒊⒋⒌⒍⒎⒏⒐⒑]/g, "");
          if (!clean.trim()) continue;
          out.push({ id: p + i, i, tok: clean === txt ? raw : (typeof raw === "string" ? clean : { ...raw, t: clean }), lead: keep.has(i) });
        }
        prevEnd = b;
      }
      // the page break cut this sentence in two and the first half stays in the unit before: joined here, at the
      // first word, so the sentence reads whole ("…的法律位阶为行政" + "法规，加强对…") and stays one line
      if (v.glue) {
        const first = out.find((x) => !x.gap && x.tok);
        if (first) {
          const tk = typeof first.tok === "string" ? { t: first.tok } : first.tok;
          first.tok = { ...tk, t: v.glue + (tk.t || "") };
        }
      }
    } else {
      // i: the token of the full text a written word came from (none for the model's own words); j: its place in
      // the written line (Key's: Takeaway is the line's first sentence, so its words keep their places)
      const brief = v.brief.map((t, j) => ({ id: t.m !== undefined ? p + t.m : `${p}w${j}`, i: t.m, j, tok: t }));
      if (L === 2) out = brief.map((x, j) => ({ ...x, lead: j < v.tn }));
      else if (L === 1) {
        // topic words carry k, the takeaway word they slide from; a topic the model phrased itself may have none
        const inTopic = new Set(v.topic.map((t) => t.k).filter((k) => k !== undefined));
        out = brief.slice(0, v.tn).map((x, j) => ({ ...x, lead: inTopic.has(j) }));
      } else if (CJK_DOC && !v.tn) out = [];   // a takeaway that stepped down (ladder.fold_background): its topic with it
      else out = v.topic.map((t, j) => ({ id: t.k !== undefined ? brief[t.k].id : `${p}t${j}`, i: t.k !== undefined ? brief[t.k].i : undefined, tok: t, lead: true }));
      if (mine && L > 0) {   // the reader's passages after the written line, in the author's words (same ids: they stay put)
        const used = new Set(out.map((x) => x.id));
        let prev = null;
        for (const i of mine) {
          if (prev === null || i !== prev + 1) out.push({ id: `${p}kg${i}`, gap: true, kept: true });
          out.push({ id: used.has(p + i) ? `${p}${i}~k` : p + i, i, tok: v.toks[i], kept: true });
          prev = i;
        }
      }
    }
    // the reader's highlights. At Full and Brief (and in the passages kept after a written line): the words of the
    // passages marked there, or (in another language) their sentences, more lightly. On a written line: the words
    // marked on it, the model's too, in the colour of the level they were marked at; and the words that came from a
    // passage marked on a line before marks were kept as the line's own (anchor.shown), as they were lit then
    if (mk) for (const x of out) {
      if (L <= 2 && !x.kept) {
        const ln = x.j !== undefined && mk.line.has(x.j);
        x.hl = ln ? mk.line.get(x.j) : x.i !== undefined && mk.old.has(x.i) ? mk.old.get(x.i) : 0;
        x.sh = ln ? mk.lineSh.get(x.j) : mk.sh.get(x.i);
      } else if (x.i !== undefined) { x.hl = mk.exact.has(x.i) ? 1 : mk.soft.has(x.i) ? 2 : 0; x.sh = mk.sh.get(x.i); }
    }
    // a formula, citation or reference is the same element in every level and language
    const seen = new Map();
    for (const x of out) {
      if (!x.tok || !x.tok.a) continue;
      const base = `${uid}@${x.tok.a}`, n = seen.get(base) || 0;
      seen.set(base, n + 1);
      x.id = n ? `${base}#${n}` : base;
    }
    const fm = findMarks.get(uid);   // the words found (find in the text, below)
    if (fm) for (const x of out) x.fd = fm.get(x.id) || 0;
    return out;
  }

  // an atom is kept on one line, except a citation, which may list many works and breaks between them
  const atCls = (t) => (t.a ? (A[t.a].wrap ??= A[t.a].html.includes('ltx_cite')) ? ' at wrap' : ' at' : t.x ? ' at' : '');
  // a piece's highlight (pieces(): hl): marked at Full or Brief, the same more lightly (another language), marked at
  // Key, marked at Takeaway
  const HL = ['', ' hl', ' hl hls', ' hl hlk', ' hl hlt'];
  // its shade (pieces(): sh; note.shade, 1 light or 3 deep, the usual 2 none: reader.css .sh1, .sh3), in the colour of
  // the level the note was made at (toneOf: tn-src, Full and Brief, the author's words; tn-key, tn-take, the written
  // line at Key, at Takeaway). The colour of each level is the reader's (the Aa menu), so a note keeps no colour
  const SHADES = [1, 2, 3];
  const shadeOf = (n) => (n && (n.shade === 1 || n.shade === 3) ? n.shade : 2);
  const toneOf = (n) => (n.anchor && n.anchor.shown ? (n.anchor.level === 1 ? 'take' : 'key') : 'src');
  const toneCls = (n) => ` tn-${toneOf(n)}${shadeOf(n) !== 2 ? ` sh${shadeOf(n)}` : ''}`;
  // the shade last chosen (a dot clicked): Highlight, and a note written from the bar over a selection, take it
  let lastShade = SHADES.includes(+store.get('dr-shade')) ? +store.get('dr-shade') : 2;
  const useShade = (s) => { if (SHADES.includes(s)) { lastShade = s; store.set('dr-shade', String(s)); } };
  const hlCls = (p) => (HL[p.hl] || '') + (p.hl && p.sh && p.sh !== 2 ? ` sh${p.sh}` : '');
  function fill(el, p) {
    const t = p.tok || {};
    const key = p.gap ? 'gap' : `${t.a || ''}\u0001${t.x || ''}\u0001${t.t || ''}\u0001${t.f || ''}`;
    if (el._k !== key) {
      el._k = key;
      if (p.gap) { el.textContent = '…'; }
      else if (t.a) { el.innerHTML = A[t.a].html; }
      else if (t.x) { el.textContent = t.x; tex(el, t.x); }
      else { el.textContent = t.t; }
      el.className = 'tk' + (p.gap ? ' gap' : '') + atCls(t) + (t.f ? ' ' + t.f : '');
      el._base = el.className;
    }
    el.className = el._base + (p.lead ? ' lead' : '') + (p.kept ? ' kept' : '') + hlCls(p) + fdCls(p);
  }
  const fdCls = (p) => (p.fd ? (p.fd === 2 ? ' fd fdc' : ' fd') : '');

  // where a line may break between two pieces written without a space: between Chinese characters yes (except
  // before closing or after opening punctuation), inside a Latin word or formula group no
  const CJK = /[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]/;   // Han and kana break anywhere
  const CLOSE = /^[，。、；：！？）」』”’》%,.;:!?)\]]/;
  const OPEN = /[（「『“‘《([]$/;
  const textOf = (p) => p.gap ? '…' : (p.tok.t || 'A');
  function glued(p, q, cut) {
    const a = textOf(p), b = textOf(q);
    if (CLOSE.test(b) || OPEN.test(a)) return true;
    return !(CJK.test(a) || CJK.test(b) || cut);
  }

  // Thai is written without spaces and its tokens are syllables (tokens.py): a line may break only where the browser's
  // own dictionary ends a word, so the pieces of a word stay together and the words of a clause may part (a closing
  // mark after the word, an opening one before it, stay glued to it as everywhere)
  const THAI = /[\u0e00-\u0e7f]/;
  const thaiSeg = typeof Intl !== 'undefined' && Intl.Segmenter ? new Intl.Segmenter('th', { granularity: 'word' }) : null;
  // the pieces after which a line may break: the end of each Thai word, and each side of a Thai run among other text
  function thaiCuts(pcs) {
    const cuts = new Set();
    let run = [], text = '';
    const flush = () => {
      if (run.length) {
        const ends = new Set();
        if (thaiSeg) for (const w of thaiSeg.segment(text)) ends.add(w.index + w.segment.length);
        let at = 0;
        run.forEach((n) => { at += pcs[n].tok.t.length; if (!thaiSeg || ends.has(at)) cuts.add(n); });
        if (run[0] > 0) cuts.add(run[0] - 1);
        cuts.add(run[run.length - 1]);
      }
      run = []; text = '';
    };
    pcs.forEach((p, n) => {
      if (p.tok && p.tok.t && THAI.test(p.tok.t)) { run.push(n); text += p.tok.t; if (p.tok.s) flush(); }
      else flush();
    });
    flush();
    return cuts;
  }

  const levelOf = (uid) => localLevel.has(uid) ? localLevel.get(uid) : level;

  // Two ways to draw a unit. Near the viewport every piece is its own inline-block with a persistent element,
  // so a level change can slide it ("tokens"). Everywhere else the unit is plain text in a few runs ("text"):
  // a long paper laid out as ~60k inline-blocks made every level change re-lay the whole document (0.5 s at
  // 150 pages); as text it costs what an ordinary article costs, and only what is on screen pays for motion.
  function renderUnit(uid, hot = true) {
    const L = levelOf(uid), el = unitEl.get(uid);
    el.classList.toggle('local-open', localLevel.has(uid) && L > level);
    const fig = figOf.get(uid);
    if (fig) fig.dataset.l = L;
    el._mode = hot ? 'tok' : 'txt';
    el._stale = false;
    drawUnit(uid, L, el, spans.get(uid), hot);
  }
  // the drawing itself, into the unit's element (or a copy of it: the keyframes' sandbox)
  function drawUnit(uid, L, el, map, hot) {
    el.dataset.l = L;
    if (CJK_DOC) {   // an apparatus unit (footnote, contents line): not shown at all when folded, as written at Full
      el.hidden = L < 4 && !pieces(uid, L).length;
      if (el.hidden) { el._tx.replaceChildren(); return; }
    }
    if (!hot) {
      el._tx.innerHTML = textHtml(uid, L);
      if (el._tx.querySelector('.texsrc')) texify(el._tx);
      return;
    }
    const pcs = pieces(uid, L), frag = document.createDocumentFragment();
    const cuts = lang === 'th' ? thaiCuts(pcs) : null;
    let group = null;
    pcs.forEach((p, n) => {
      let s = map.get(p.id);
      if (!s) { s = document.createElement('span'); s.dataset.id = p.id; map.set(p.id, s); }
      fill(s, p);
      s._i = p.i;
      s._j = p.j;
      const last = n === pcs.length - 1;
      const space = !last && (p.gap || (p.tok && p.tok.s) || pcs[n + 1].gap);
      const glue = !last && !space && glued(p, pcs[n + 1], cuts && cuts.has(n));
      if (glue) { if (!group) { group = document.createElement('span'); group.className = 'wg'; frag.appendChild(group); } group.appendChild(s); }
      else if (group) { group.appendChild(s); group = null; }
      else frag.appendChild(s);
      if (space) {   // a space inside a highlight is highlighted too
        const both = p.hl && !last && pcs[n + 1].hl, found = p.fd && !last && pcs[n + 1].fd;
        frag.appendChild(both ? h('span', `hlsp${p.hl === pcs[n + 1].hl && p.sh === pcs[n + 1].sh ? hlCls(p).replace(' hl', '') : ''}`, ' ')
          : found ? h('span', `fdsp${p.fd === 2 && pcs[n + 1].fd === 2 ? ' fdc' : ''}`, ' ') : document.createTextNode(' '));
      }
    });
    el._tx.replaceChildren(frag);
  }

  // the same pieces as text: runs of equal styling become one span, formulas stay as they are
  function textHtml(uid, L) {
    const pcs = pieces(uid, L);
    let html = '', run = null, buf = '';
    const flush = () => { if (buf) html += run ? `<span class="${run}">${buf}</span>` : buf; buf = ''; };
    pcs.forEach((p, n) => {
      const t = p.tok || {};
      const cls = p.gap ? 'tr gap' : `tr${p.lead ? ' lead' : ''}${p.kept ? ' kept' : ''}${t.f ? ' ' + t.f : ''}${hlCls(p)}${fdCls(p)}`;
      if (cls !== run) { flush(); run = cls; }
      buf += p.gap ? '…' : t.a ? A[t.a].html : t.x ? `<span class="texsrc">${esc(t.x)}</span>` : esc(t.t);
      if (n < pcs.length - 1 && (p.gap || t.s || pcs[n + 1].gap)) {
        if ((p.hl && !pcs[n + 1].hl) || (p.fd && !pcs[n + 1].fd)) { flush(); run = null; }   // a highlight ends on its last word, not the space after it
        buf += ' ';
      }
    });
    flush();
    return html;
  }

  // ── math the model wrote that matches no formula of the unit: KaTeX, from where the page keeps it (D.katex: the
  //    app's or a gallery's folder, or "inside" the page), never from another server; without it, the source stays ──
  let katexReady = null, katexLib = window.katex || null;
  function loadKatex() {
    if (katexLib) return Promise.resolve(katexLib);
    katexReady ??= new Promise((res) => {
      if (!D.katex || D.katex === 'inside') { res(null); return; }
      const css = document.createElement('link'); css.rel = 'stylesheet';
      css.href = `${D.katex}katex.min.css`; document.head.appendChild(css);
      const js = document.createElement('script'); js.src = `${D.katex}katex.min.js`;
      js.onload = () => { katexLib = window.katex; res(window.katex); }; js.onerror = () => res(null); document.head.appendChild(js);
    });
    return katexReady;
  }
  function tex(el, src) {
    if (katexLib) { try { katexLib.render(src, el, { throwOnError: false }); } catch { /* keep the source */ } return; }   // loaded: at once, so it is measured drawn
    loadKatex().then((k) => { if (k) try { k.render(src, el, { throwOnError: false }); } catch { /* keep the source */ } });
  }
  // ── formulas written in LaTeX (semantic.py: math.pf-tex, a Markdown file's): drawn by KaTeX, as Typora draws them
  //    with MathJax, once, before anything is laid out; the page has KaTeX before this script (build.py). Without it,
  //    their MathML stays ──
  const attrText = (x) => x.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&');
  function drawTex(html) {
    if (!katexLib || !html.includes('pf-tex')) return html;
    return html.replace(/<math\b([^>]*)>[\s\S]*?<\/math>/g, (m, attrs) => {
      const src = /\balttext="([^"]*)"/.exec(attrs);
      if (!/\bclass="pf-tex"/.test(attrs) || !src) return m;
      try { return katexLib.renderToString(attrText(src[1]), { displayMode: /\bdisplay="block"/.test(attrs), throwOnError: false }); } catch { return m; }
    });
  }
  for (const a of Object.values(A)) if (a.kind === 'math') a.html = drawTex(a.html);
  for (const b of D.blocks) if (b.html) b.html = drawTex(b.html);

  // ── a diagram written in Mermaid (semantic.py: pre.pf-mermaid, its source): drawn by Mermaid, loaded only for a text
  //    that has one, from where the page keeps it (D.mermaid, as D.katex), light or dark as the page is, and drawn
  //    again when that changes; without it, or where the source does not read, the source stays as the code it is ──
  let mermaidReady = null, diagramN = 0, diagramsDark = null;
  function loadMermaid() {
    if (window.mermaid) return Promise.resolve(window.mermaid);
    mermaidReady ??= new Promise((res) => {
      if (!D.mermaid || D.mermaid === 'inside') { res(null); return; }
      const js = document.createElement('script'); js.src = `${D.mermaid}mermaid.min.js`;
      js.onload = () => res(window.mermaid || null); js.onerror = () => res(null); document.head.appendChild(js);
    });
    return mermaidReady;
  }
  async function drawDiagrams() {
    const pres = [...docEl.querySelectorAll('pre.pf-mermaid')], r = document.documentElement;
    const dark = r.dataset.theme ? r.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    if (!pres.length || dark === diagramsDark) return;
    diagramsDark = dark;
    const lib = await loadMermaid();
    if (!lib) return;
    await document.fonts?.ready;   // (the words' boxes are measured in the page's own face)
    lib.initialize({ startOnLoad: false, securityLevel: 'strict', suppressErrorRendering: true, theme: dark ? 'dark' : 'default',
      fontFamily: getComputedStyle(r).getPropertyValue('--sans').trim() || 'sans-serif' });
    for (const pre of pres) {
      let fig = pre.nextElementSibling;
      try {
        const { svg } = await lib.render(`pf-mermaid-${++diagramN}`, pre.textContent);
        if (!fig || !fig.classList.contains('pf-diagram')) { fig = h('div', 'pf-diagram'); pre.after(fig); }
        fig.innerHTML = svg;
        for (const el of fig.querySelectorAll('[data-id]')) el.removeAttribute('data-id');   // (the zoom's pieces are ours alone)
        pre.hidden = true;
      } catch { /* the source stays */ }
    }
    capEpoch++; relayout(); warmSoon();
  }

  // ── skeleton ─────────────────────────────────────────────────────────
  function h(tag, cls, html) { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const flipName = (n) => { const p = n.split(',').map((x) => x.trim()); return p.length === 2 ? `${p[1]} ${p[0]}` : n; };
  // a title the model translated keeps $...$: show the heading's own formula when it matches, else KaTeX
  function mathify(text, refHtml) {
    const t = document.createElement('template');
    t.innerHTML = refHtml || '';
    const maths = [...t.content.querySelectorAll('math')];
    const sq = (x) => (x || '').replace(/\s+/g, '');
    return String(text).split(/\$([^$]+)\$/).map((seg, i) => {
      if (i % 2 === 0) return esc(seg);
      const m = maths.find((x) => sq(x.getAttribute('alttext')) === sq(seg));
      return m ? m.outerHTML : `<span class="texsrc">${esc(seg)}</span>`;
    }).join('');
  }
  const texify = (root) => root.querySelectorAll('.texsrc').forEach((e) => { e.classList.remove('texsrc'); tex(e, e.textContent); });
  function tokEl(tk, cls) {
    const s = h('span', 'tk' + atCls(tk) + (cls ? ' ' + cls : ''));
    if (tk.a) s.innerHTML = A[tk.a].html;
    else if (tk.x) { s.textContent = tk.x; tex(s, tk.x); }
    else s.textContent = tk.t;
    return s;
  }
  function toksHtml(toks) {
    let s = '';
    toks.forEach((t, i) => {
      s += t.a ? A[t.a].html : t.x ? `<span class="texsrc">${esc(t.x)}</span>` : esc(t.t);
      if (t.s && i < toks.length - 1) s += ' ';
    });
    return s;
  }

  function unitNode(uid, tag) {
    const u = U[uid];
    const el = h(tag, `u ${u.role} r-${u.lad.role}${u.lad.model === 'local' ? ' is-local' : ''}`);
    el.dataset.u = uid;
    el.id = uid;
    const dot = h('span', 'dot'); dot.dataset.id = `dot:${uid}`;
    el.append(dot);
    if (u.role === 'item' && u.marker) { const mk = h('span', 'mk', u.marker); mk.dataset.id = `mk:${uid}`; el.append(mk); }
    el._tx = h(tag === 'li' || tag === 'figcaption' ? 'div' : 'p', 'tx');
    el.append(el._tx);
    unitEl.set(uid, el); spans.set(uid, new Map()); order.set(uid, order.size);
    return el;
  }

  const paperHead = h('header', 'paper-head');
  {   // the mark and the name in the bar lead home in the app; on a page saved or published, to where TextFold is
    const brand = document.querySelector('.topbar .brand'), home = h('a', 'brand-home');
    home.href = D.app ? '/' : REPO;
    if (!D.app) { home.target = '_blank'; home.rel = 'noopener'; }
    home.append(...[...brand.children].filter((x) => x.tagName !== 'A'));
    brand.prepend(home);
  }
  const madeWith = D.app ? null : h('div', 'madewith');   // and such a page ends with the mark
  if (madeWith) docEl.after(madeWith);
  const madeLabel = () => { if (madeWith) madeWith.innerHTML = `<a href="${REPO}" target="_blank" rel="noopener">${LOGO}<span>${esc(T[ui].sv_madewith)}</span></a>`; };
  madeLabel();
  // under a drawing whose words the browser set (an arXiv paper's TikZ picture: they may run out of their boxes), the
  // paper's figures of that kind cut from its PDF, as TeX set them, when the reader asks (pdffigs.py); under one taken
  // (back), the paper's put back as the HTML has them; then the page again
  function pdfOffer(back) {
    const btn = h('button', 'fig-pdf'); btn.type = 'button'; btn.textContent = back ? T[ui].fig_pdf_back : T[ui].fig_pdf;
    btn.addEventListener('click', async () => {
      btn.disabled = true; if (!back) btn.textContent = T[ui].fig_pdf_busy;
      try {
        const r = await post(`/api/pdf-figures/${encodeURIComponent(D.meta.id)}`, back ? { back: true } : {});
        if (r.cut || back) location.reload(); else btn.textContent = T[ui].fig_pdf_none;
      } catch { btn.disabled = false; btn.textContent = T[ui].fig_pdf_fail; }
    });
    return btn;
  }
  // where a paper comes from, in a few words: arXiv:2201.11903v6, or a document's file name
  const sourceName = (m) => m.label || `arXiv:${m.id}${m.version || ''}`;
  function build() {
    const m = D.meta;
    const link = document.getElementById('arxiv-link');
    if (m.abs_url) { link.href = m.abs_url; link.textContent = `${sourceName(m)} ↗`; }
    else { link.removeAttribute('href'); link.textContent = sourceName(m); }
    docEl.append(paperHead);
    const chunksEl = h('div', 'chunks');
    docEl.append(chunksEl);
    const chunkById = Object.fromEntries(D.chunks.map((c) => [c.id, c]));
    let body = chunksEl, list = null, listId = null, front = null;
    const boxes = [];       // open theorem / definition / proof frames
    // an equation goes with the paragraph that brings it in (the one before it in its section), or, first in its
    // section, the one after it: a note on it is that paragraph's (selectionAnchors)
    let lastU = null;
    const eqWait = [];
    for (const b of D.blocks) {
      if (b.k === 'box') {
        const cat = b.kind === 'proof' ? 'proof' : /^def/.test(b.kind) ? 'def' : /rem|exa|note|obs/.test(b.kind) ? 'rem' : 'thm';
        const bx = h('div', `box box-${cat}`); bx.id = b.id;
        const bh = h('div', 'box-h', b.title || esc(b.kind[0].toUpperCase() + b.kind.slice(1))); bh.dataset.id = `bh:${b.id}`;
        bx.append(bh);
        (boxes.length ? boxes[boxes.length - 1] : b.chunk ? body : front).append(bx);
        boxes.push(bx); list = null;
        continue;
      }
      if (b.k === 'box_end') { boxes.pop(); list = null; continue; }
      if (b.k === 'h') {
        boxes.length = 0;
        lastU = null; eqWait.length = 0;
        const c = chunkById[b.chunk];
        // An empty section whose title is a reference itself ([EB/OL], a URL): the converter read a footnote as a
        // section heading. Full keeps it as written; every folded level drops the section, or Takeaway shows a
        // block of quoted text where a heading should be
        const ref = CJK_DOC && !c.units.length && /\[[A-Za-z]+\/OL\]|https?:\/\//.test(c.title || '');
        const cur = h('section', `chunk l${c.level}${c.units.length ? '' : ' empty'}${ref ? ' ref' : ''}`);
        cur.dataset.chunk = c.id; cur.id = c.id;
        const hd = h('h2', 'hd'); hd.dataset.id = `h:${c.id}`;
        hd.hidden = !c.num && !c.title;   // a document's opening text has no heading of its own
        const st = h('p', 'stake');
        cur.append(hd, st);
        headEls.push([c, hd, st]);
        body = h('div', 'body'); cur.append(body); chunksEl.append(cur);
        list = null; listId = null;
        continue;
      }
      // blocks outside any section (keywords, title-page leftovers) stay where they are, shown at Full and Key only
      if (!b.chunk && !boxes.length && (!front || chunksEl.lastElementChild !== front)) { front = h('div', 'front'); front.dataset.l = level; lvBlocks.push(front); chunksEl.append(front); list = null; }
      const target = boxes.length ? boxes[boxes.length - 1] : b.chunk ? body : front;
      if (b.k === 'u') {
        const u = U[b.u];
        lastU = b.u;
        for (const e of eqWait.splice(0)) e._unit = b.u;
        if (u.role === 'item') {
          if (!list || listId !== u.list) { list = h('ul', 'list'); listId = u.list; target.append(list); }
          list.append(unitNode(b.u, 'li'));
          continue;
        }
        list = null;
        target.append(unitNode(b.u, 'div'));
      } else if (b.k === 'fig') {
        list = null;
        const f = h('figure', `fig fig-${b.kind}`); f.dataset.l = level;
        if (b.id) f.id = b.id;
        if (!b.cap) { bareFigs.push(f); lvBlocks.push(f); }
        const fb = h('div', 'fbody', b.html); fb.dataset.id = `fb:${b.id}`;
        f.append(fb);
        if (b.pdf && D.app) f.append(pdfOffer(b.pdf === 'cut'));
        if (b.cap) {
          const cap = unitNode(b.cap, 'figcaption');
          if (b.label) { const fl = h('span', 'flabel'); fl.dataset.label = b.label; fl.dataset.id = `fl:${b.cap}`; cap.insertBefore(fl, cap._tx); }
          figOf.set(b.cap, f);
          f.append(cap);
        }
        target.append(f);
      } else if (b.k === 'eq' || b.k === 'raw') {
        list = null;
        const e = h('div', b.k, b.html); e.dataset.id = `${b.k}:${b.id || Math.random().toString(36).slice(2)}`;
        if (b.k === 'eq') {
          e.dataset.eq = b.id; e.dataset.chunk = b.chunk; eqEls.push(e);
          if (lastU) e._unit = lastU; else eqWait.push(e);
        }
        e.dataset.l = level; lvBlocks.push(e);
        target.append(e);
      }
    }
    if (D.bib) {
      const bib = h('details', 'bib'); bib.dataset.id = 'bib';
      bib.append(h('summary'), h('div', null, D.bib));
      docEl.append(bib);
    }
    for (const uid of unitEl.keys()) sideNode(uid);
    let lastPos = 0;   // a block sits where the unit before it sits
    for (const el of docEl.querySelectorAll('.u, .eq, .raw, .front, figure.fig')) {
      if (el.dataset.u && order.has(el.dataset.u)) lastPos = order.get(el.dataset.u); else el._pos = lastPos;
    }
  }

  // everything that is text in the chrome or depends on the language but is not a unit
  function applyLang() {
    if (mapReady) { renderTview(); mapKey = ''; if (mapShown) setTimeout(() => { if (mapShown) mapRender(); }); }   // (drawn again in the new language, once it is in)
    const t = T[ui], c = T[lang] || T.en, m = D.meta;   // (c: the names of the paper's own parts, in the language it is read in)
    document.documentElement.lang = I18N.htmlLang(lang);
    document.documentElement.dataset.lang = lang;
    mirrorLang();
    // the reader's name for it in the library (m.name), if they gave one, over its own title in this language
    const trTitle = m.titles && m.titles[lang], below = m.name ? trTitle || m.title : trTitle ? m.title : '';
    paperHead.innerHTML = `<h1>${esc(m.name || trTitle || m.title)}</h1>${below ? `<div class="orig">${esc(below)}</div>` : ''}<div class="authors">${esc((m.authors || []).map(flipName).join(', '))}</div>` +
      (m.license || m.snapshot ? `<div class="license">${[m.license ? `<a href="${esc(m.license)}" target="_blank" rel="noopener">${esc(licenseName(m.license, t))}</a>` : '',
        m.snapshot ? esc(t.snapshot.replace('{date}', longDate(m.snapshot))) : ''].filter(Boolean).join(' · ')}</div>` : '');
    [...paperHead.children].forEach((c) => { c.dataset.id = `ph:${c.className || c.tagName}`; });
    capEpoch++;
    // theorem labels come from LaTeXML in English
    // only the leading kind word ("Theorem 1.1", "Proof"), not a name such as "The Auslander-Reiten Conjecture"
    const KIND = /^(Theorem|Lemma|Proposition|Corollary|Definition|Remark|Example|Proof|Conjecture|Claim|Assumption|Observation|Notation)\b/;
    docEl.querySelectorAll('.box-h').forEach((bh) => {
      bh._en ??= bh.innerHTML;
      const m = KIND.exec(bh.textContent.trim());
      bh.innerHTML = m && lang !== 'en' ? bh._en.replace(m[1], c.env[m[1]] || m[1]) : bh._en;
    });
    for (const [c, hd, st] of headEls) {
      hd.innerHTML = `${c.num ? `<span class="num">${esc(c.num)}</span>` : ''}<span class="ttl">${chunkTitle(c)}</span>`;
      texify(hd);
      renderStake(c, st);
    }
    docEl.querySelectorAll('.flabel').forEach((fl) => {
      fl.textContent = fl.dataset.label.replace(/^Figure\s*/, `${c.figure} `).replace(/^Table\s*/, `${c.table} `);
    });
    const sum = docEl.querySelector('.bib summary'); if (sum) sum.textContent = c.refs;
    document.getElementById('hint').textContent = t.hint;
    document.querySelector('.tagline').textContent = t.tagline;
    langBtn.innerHTML = `${esc(nameOf(lang))}${STALE.has(lang) ? ` <em class="stale">${esc(t.stale)}</em>` : gapTag(lang, t) ? ` ${gapTag(lang, t)}` : ''} <span class="caret">▾</span>`;
    langBtn.title = t.language;
    typeBtn.title = t.ty_title; typeBtn.setAttribute('aria-label', t.ty_title);
    if (!typeMenu.hidden) renderTypeMenu();
    bar.querySelectorAll('button').forEach((b) => { b.textContent = t.levels[+b.dataset.l]; });
    requestAnimationFrame(() => { geom = null; placeThumb(); });
    legend.replaceChildren();
    for (const r of ROLES) if (usedRoles.has(r)) { const it = h('span', null, `<i style="background:var(--r-${r});--beat:var(--r-${r})"></i>${t.roles[r]}`); it.dataset.role = r; legend.append(it); }
    if (rolePinned) legendFocus(rolePinned, true);
    document.getElementById('back').textContent = `← ${t.back}`;
    for (const uid of unitEl.keys()) renderSide(uid);
    notesForLang();
    if (window.DRApp) window.DRApp.onLang(lang, readerLang);
  }

  // a page read from the web is a snapshot: the day it was read, in the interface's language ("6 October 2026")
  const longDate = (iso) => { try { return new Date(`${iso}T12:00:00`).toLocaleDateString(I18N.htmlLang(ui), { year: 'numeric', month: 'long', day: 'numeric' }); } catch { return iso; } };
  // what a paper's license allows, in a few words: CC licenses by name; arXiv's own grants only arXiv the right to share
  function licenseName(url, t) {
    const cc = /creativecommons\.org\/(licenses|publicdomain)\/([a-z-]+)\/([\d.]+)/.exec(url);
    if (cc) return `${t.license}: ${cc[1] === 'publicdomain' ? 'CC0' : 'CC ' + cc[2].toUpperCase()} ${cc[3]}`;
    const spdx = /spdx\.org\/licenses\/([\w.+-]+?)(?:\.html)?$/.exec(url);   // a GitHub repository's, by its SPDX id
    if (spdx) return `${t.license}: ${spdx[1]}`;
    return /arxiv\.org\/licenses\/nonexclusive/.test(url) ? t.lic_arxiv : `${t.license}: ${url.replace(/^https?:\/\//, '')}`;
  }
  // a section's one-line summary, word by word (the reader's highlights on it marked)
  function renderStake(c, st) {
    const lad = (trOf(c) && trOf(c).lad) || c.lad;
    st.replaceChildren();
    if (CJK_DOC) {   // no section stake: leave no empty row
      st.hidden = !lad || !lad.take.length;
      if (st.hidden) return;
    }
    if (!lad) return;
    const mk = stakeMarks(c.id);
    lad.take.forEach((tk, i) => {
      const s = tokEl(tk, `${lad.topic.includes(i) ? 'lead' : ''}${mk && mk.has(i) ? ' hl' + mk.get(i) : ''}`);
      s.dataset.id = `st:${c.id}:${lang}:${i}`;
      s._i = i;
      st.append(s);
      if (tk.s) st.append(mk && mk.has(i) && mk.has(i + 1) ? h('span', `hlsp${mk.get(i) === mk.get(i + 1) ? mk.get(i) : ''}`, ' ') : ' ');
    });
  }
  function chunkTitle(c) { const z = trOf(c); return z ? mathify(z.title, c.html) : c.html; }

  // Every unit at the current level: the ones in `hot` as tokens, the ones in `band` as text, and the rest
  // later. A unit far from the viewport keeps its old drawing for now (stale) and is redrawn when the browser
  // is idle, nearest first, so a level change costs the neighbourhood, not the whole paper.
  // Blocks (equations above all: MathML is the costliest thing to lay out) follow the same rule.
  let staleQ = [];
  const order = new Map();   // uid -> position in the paper
  const redraw = (x) => { if (typeof x === 'string') renderUnit(x, false); else { x.dataset.l = level; x._stale = false; } };
  const elOf = (x) => (typeof x === 'string' ? unitEl.get(x) : x);
  function renderAll(hot = null, band = null) {
    staleQ = [];
    for (const uid of unitEl.keys()) {
      if (hot && hot.has(uid)) renderUnit(uid, true);
      else if (!band || band.has(uid)) renderUnit(uid, false);
      else { unitEl.get(uid)._stale = true; staleQ.push(uid); }
    }
    for (const b of lvBlocks) {
      if (!band || band.has(b)) { b.dataset.l = level; b._stale = false; } else { b._stale = true; staleQ.push(b); }
    }
  }
  // what is stale but the change has brought on screen is drawn now (the shrinking text pulled it in)
  function drawStaleInView(M) {
    let drew = false;
    for (const x of staleQ) {
      const el = elOf(x);
      if (!el._stale) continue;
      const r = el.getBoundingClientRect();
      if ((r.width || r.height) && r.bottom > -M && r.top < innerHeight + M) { redraw(x); drew = true; }
    }
    return drew;
  }
  // time-sliced, not idle-callback driven: idle time is scarce while animations run, and the backlog must drain
  const later = (f) => setTimeout(f, 0);
  function refreshStale() {
    staleQ = staleQ.filter((x) => elOf(x)._stale);
    if (!staleQ.length) return;
    const pin = viewAnchor();
    const at = pin && pin.dataset.u ? order.get(pin.dataset.u) : 0;
    const pos = (x) => (typeof x === 'string' ? order.get(x) : (x._pos ?? at));
    staleQ.sort((a, b) => Math.abs(pos(a) - at) - Math.abs(pos(b) - at));
    const step = () => {
      if (busy) return;   // a level change took over; it leaves its own stale list
      const ref = viewAnchor(), top0 = ref ? ref.getBoundingClientRect().top : 0;
      const t0 = performance.now();
      while (staleQ.length && performance.now() - t0 < 8) {   // 8 ms of work, then the page breathes
        const x = staleQ.shift();
        if (elOf(x)._stale) redraw(x);
      }
      // what was redrawn above the reader changed height: keep the page where the reader is
      if (ref) { const d = ref.getBoundingClientRect().top - top0; if (Math.abs(d) > 0.5) window.scrollBy(0, d); }
      if (staleQ.length) later(step); else { relayout(); warmSoon(); }
    };
    later(step);
  }
  function viewAnchor() {
    const col = docEl.getBoundingClientRect();
    const el = document.elementFromPoint(col.left + Math.min(col.width / 2, 260), innerHeight * 0.35);
    return el && el.closest('.u, .hd');
  }

  // units within `above` px over and `below` px under the viewport
  function unitsNear(above, below, withBlocks = false) {
    const out = new Set();
    const ok = (el) => { const r = el.getBoundingClientRect(); return (r.width || r.height) && r.bottom > -above && r.top < innerHeight + below; };
    for (const [uid, el] of unitEl) if (ok(el)) out.add(uid);
    // a hidden block has no box: judge it by the unit before it
    if (withBlocks) { const ids = [...order.keys()]; for (const b of lvBlocks) if (ok(b) || out.has(ids[b._pos ?? 0])) out.add(b); }
    return out;
  }

  // The units drawn at once on a level change: the ones near the anchor in reading order (on the Topic map
  // nearly the whole paper is on screen, so distance on screen says little about what will be on screen
  // after zooming in), the ones on screen now, and the blocks among them.
  function bandAround(anchor, k, hot) {
    const ids = [...order.keys()];
    let el = anchor && anchor.el;
    if (el && !el.dataset.u) el = (el.closest('section.chunk') || docEl).querySelector('.u');
    const at = el && el.dataset.u ? order.get(el.dataset.u) : 0;
    const band = new Set(hot);
    for (let i = Math.max(0, at - k); i <= Math.min(ids.length - 1, at + k); i++) band.add(ids[i]);
    for (const b of lvBlocks) if (Math.abs((b._pos ?? 0) - at) <= k) band.add(b);
    return band;
  }

  // before measuring, whatever is on screen as text becomes tokens (same content, now able to move)
  function hydrate(M) {
    for (const uid of unitsNear(M, M)) if (unitEl.get(uid)._mode !== 'tok') renderUnit(uid, true);
  }

  // ── FLIP ─────────────────────────────────────────────────────────────
  let busy = false, queued = null;
  let mapReady = false, mapShown = false;   // the Topic map drawn as a mind map (below): set up, and over the page now

  function visibleRect(el) {
    if (!el || !el.isConnected) return null;
    const r = el.getBoundingClientRect();
    if (!r.width && !r.height) return null;
    return r;
  }
  const near = (r, M) => r && r.bottom > -M && r.top < innerHeight + M;
  const BOXES = '.u, .hd, .stake, .box-h, .fbody, .eq, .raw, .paper-head, .bib';

  function snapshot(M) {
    const snap = new Map();
    // only pieces inside containers near the viewport: a full paper has ~20k of them
    const styles = new Map();   // one computed style per (container, class): pieces of a unit share their looks
    for (const box of docEl.querySelectorAll(BOXES)) {
      if (!near(visibleRect(box), M)) continue;
      const items = box.matches('[data-id]') ? [box] : box.querySelectorAll('[data-id]');
      for (const el of items) {
        const r = visibleRect(el);
        if (!near(r, M)) continue;
        const key = `${box.dataset.u || box.className}|${el.className}`;
        let st = styles.get(key);
        if (!st) {
          const cs = getComputedStyle(el);
          st = { font: `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} / ${cs.lineHeight} ${cs.fontFamily}`, color: cs.color, bg: cs.backgroundColor };
          styles.set(key, st);
        }
        snap.set(el, { r, ...st });
      }
    }
    return snap;
  }

  function anchorAt(x, y) {
    let el = document.elementFromPoint(x, y);
    el = el && el.closest('.u, .hd, figure.fig, .chunk');
    if (el && el.matches('figure.fig')) el = el.querySelector('.u') || el;
    if (el && el.matches('.chunk')) el = el.querySelector('.hd');
    if (!el || !docEl.contains(el)) {
      let best = null, bd = Infinity;
      for (const u of unitEl.values()) {
        const r = visibleRect(u); if (!r) continue;
        const d = r.top > y ? r.top - y : r.bottom < y ? y - r.bottom : 0;
        if (d < bd) { bd = d; best = u; }
        if (r.top > y + 2000) break;
      }
      el = best;
    }
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { el, y, frac: r.height ? Math.min(1, Math.max(0, (y - r.top) / r.height)) : 0 };
  }

  // Motion from `before` (a snapshot) to the page as it is now. Timed: it plays at once (a paragraph opening,
  // a language change). Scrubbed: paused, linear, one second long; the zoom position sets its progress.
  function buildMotion(before, M, scrubbed) {
    const after = new Map(), arrived = [], anims = [];
    for (const box of docEl.querySelectorAll(BOXES)) {
      if (!near(visibleRect(box), M)) continue;
      if (box._mode === 'txt') { arrived.push(box._tx); continue; }   // drawn as text: it fades in whole
      const items = box.matches('[data-id]') ? [box] : box.querySelectorAll('[data-id]');
      for (const el of items) { const r = visibleRect(el); if (near(r, M)) after.set(el, r); }
    }
    let t0 = performance.now();
    const add = (a) => { if (scrubbed) a.pause(); anims.push(a); };
    const fadeIn = scrubbed ? [{ opacity: 0 }, { opacity: 0, offset: 0.35 }, { opacity: 1 }] : [{ opacity: 0 }, { opacity: 1 }];
    const inOpts = scrubbed ? { duration: 1000, fill: 'both' } : { duration: DUR * 0.55, delay: DUR * 0.4, easing: 'ease-out', fill: 'backwards' };
    const moveOpts = scrubbed ? { duration: 1000, fill: 'both' } : { duration: DUR, easing: 'cubic-bezier(.2,.8,.2,1)' };
    const fadeOut = scrubbed ? [{ opacity: 1 }, { opacity: 0, offset: 0.5 }, { opacity: 0 }] : [{ opacity: 1 }, { opacity: 0 }];
    const outOpts = scrubbed ? { duration: 1000, fill: 'both' } : { duration: DUR * 0.45, easing: 'ease-in', fill: 'forwards' };
    perf('m.measure', t0); t0 = performance.now();
    for (const tx of arrived) add(tx.animate(fadeIn, inOpts));
    let moved = 0;
    for (const [el, b] of after) {
      const a = before.get(el);
      if (!a) { add(el.animate(fadeIn, inOpts)); continue; }
      const ar = a.r, dx = ar.left - b.left, dy = ar.top - b.top;
      // boxes (figures, equations) may change shape; text and headings scale uniformly by height
      const box = el.classList.contains('fbody') || el.classList.contains('eq') || el.classList.contains('raw') || el.classList.contains('bib');
      let sx = b.width ? ar.width / b.width : 1, sy = b.height ? ar.height / b.height : 1;
      if (!box) { sx = sy = (b.height ? ar.height / b.height : 1); }
      if (!isFinite(sx) || sx <= 0) sx = 1;
      if (!isFinite(sy) || sy <= 0) sy = 1;
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5 && Math.abs(sx - 1) < 0.01 && Math.abs(sy - 1) < 0.01) continue;
      add(el.animate([{ transform: `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})` }, { transform: 'none' }], moveOpts));
      moved++;
    }
    perf('m.animate', t0); t0 = performance.now();
    // leaving: a ghost fades where the piece was
    const frag = document.createDocumentFragment();
    let ghosts = 0;
    for (const [el, a] of before) {
      if (after.has(el)) continue;   // (no rect read here: it would force a layout after the animations start)
      if (!near(a.r, 0) || ghosts > 600) continue;
      const g = el.cloneNode(true);
      g.removeAttribute('data-id');
      Object.assign(g.style, { left: `${a.r.left}px`, top: `${a.r.top}px`, width: `${a.r.width}px`, height: `${a.r.height}px`, font: a.font, color: a.color, backgroundColor: a.bg, transform: 'none', overflow: 'hidden' });
      frag.append(g); ghosts++;
      add(g.animate(fadeOut, outOpts));
    }
    ghostEl.append(frag);
    perf('m.ghosts', t0);
    if (window.__drPerf) window.__drPerf.push(['counts', { before: before.size, after: after.size, moved, ghosts }]);
    return anims;
  }

  // a timed change: everything near the reader moves at once
  function morph(mutate, anchor) {
    document.documentElement.classList.add('morphing');
    clearFocus();
    if (reduced) { mutate(); settle(anchor); done(); return; }
    const M = 160;
    hydrate(M);
    const before = snapshot(M);
    mutate();
    settle(anchor);
    for (let i = 0; i < 3 && drawStaleInView(M); i++) settle(anchor);
    buildMotion(before, M, false);
    busy = true;
    setTimeout(() => { ghostEl.replaceChildren(); done(); }, DUR + 40);
  }

  function done() {
    busy = false;
    document.documentElement.classList.remove('morphing');
    findStale();
    if (mapReady) mapSync();
    relayout();
    refreshStale();
    warmSoon();
    if (queued) { const q = queued; queued = null; q(); }
  }

  function settle(anchor) {
    if (!anchor || !anchor.el.isConnected) return;
    const r = visibleRect(anchor.el);
    if (!r) return;
    window.scrollBy(0, r.top + anchor.frac * r.height - anchor.y);
  }

  // ── zoom as a position, not a jump ───────────────────────────────────
  // The wheel, a pinch and the slider move one continuous position (4 = Full … 0 = Topic). The reader can stop
  // half way, or turn back; when the input stops, the position springs to the nearer level. The motion itself
  // is the stage's (below) when its keyframes are ready. Otherwise it is live: leaving a level prepares the
  // change to the next one for what is on screen (everything else waits, see renderAll), with its motion
  // paused, and the position sets how far the motion has run, frame by frame.
  const K_BAND = { 4: 30, 3: 45, 2: 60, 1: 150 };
  let pos = 4, goal = 4, tr = null, rafId = 0, lastT = 0, holding = false, snapTimer = 0;
  let aimLevel = null, aimAnchor = null, gesturePoint = null;
  const progress = () => (tr ? (pos - tr.from) / (tr.to - tr.from) : 0);
  function anchorNow() {
    const a = aimAnchor || (gesturePoint ? anchorAt(gesturePoint.x, gesturePoint.y) : defaultAnchor());
    aimAnchor = null;
    return a;
  }
  function prepare(to) {
    const from = level, M = 160, anchor = anchorNow();
    document.documentElement.classList.add('morphing');
    clearFocus();
    let t0 = performance.now(); const T0 = t0;
    hydrate(M);
    perf('p.hydrate', t0); t0 = performance.now();
    const before = snapshot(M);
    perf('p.snapshot', t0); t0 = performance.now();
    // zooming out shrinks the text, pulling units from a few screens away into view: give them tokens too
    const reach = innerHeight * (to < from ? 3 : 1.2);
    const hot = unitsNear(reach, reach);
    // mid-gesture, a narrow band: the next level is a moment away, and what comes on screen is drawn anyway
    const band = to === 0 ? null : bandAround(anchor, holding ? Math.min(12, K_BAND[to]) : K_BAND[to], hot);
    level = to;
    localLevel.clear();
    docEl.dataset.level = to;
    document.documentElement.dataset.level = to;
    renderAll(hot, band);
    perf('p.render', t0); t0 = performance.now();
    settle(anchor);
    for (let i = 0; i < 3 && drawStaleInView(M); i++) settle(anchor);
    perf('p.settle', t0); t0 = performance.now();
    tr = { from, to, anchor, anims: buildMotion(before, M, true) };
    perf('p.motion', t0);
    busy = true;
    perf('prepare', T0);
  }
  function scrub(t) {
    const ms = Math.max(0, Math.min(1, t)) * 1000;
    for (const a of tr.anims) a.currentTime = ms;
  }
  function finish() {   // the motion ran to its end: the page simply is the new level
    for (const a of tr.anims) a.cancel();
    ghostEl.replaceChildren();
    tr = null;
  }
  function revert() {   // back where it started: the old level is drawn again under the reader
    const { from, anchor } = tr;
    for (const a of tr.anims) a.cancel();
    ghostEl.replaceChildren();
    tr = null;
    level = from;
    docEl.dataset.level = from;
    document.documentElement.dataset.level = from;
    renderAll(from === 0 ? null : bandAround(anchor, 8, new Set()), from === 0 ? null : bandAround(anchor, K_BAND[from], new Set()));
    settle(anchor);
    for (let i = 0; i < 3 && drawStaleInView(160); i++) settle(anchor);
  }
  function apply() {
    if (stage && stageFrame()) return;
    if (!tr) {
      if (Math.abs(pos - level) < 1e-3) return;
      if (!stageOff && stageBegin() && stageFrame()) return;
      prepare(aimLevel !== null && aimLevel !== level ? aimLevel : pos < level ? level - 1 : level + 1);
    }
    const t = progress();
    if (t >= 1 - 1e-3) {   // arrived; if the position goes on, so does the zoom
      scrub(1); finish();
      if (aimLevel === level) aimLevel = null;
      if (Math.abs(pos - level) > 1e-3) apply();
      return;
    }
    if (t < 0) {           // turned back past the level it started from
      revert();
      if (Math.abs(pos - level) > 1e-3) apply();
      return;
    }
    scrub(t);
  }
  function tick(now) {
    rafId = 0;
    const dt = lastT ? Math.min(50, now - lastT) : 16;
    lastT = now;
    pos += (goal - pos) * (1 - Math.exp(-dt / (holding ? 45 : 95)));   // follows the hand closely, settles softly
    if (Math.abs(goal - pos) < 0.004) pos = goal;
    apply();
    placeThumb();
    if (pos !== goal) rafId = requestAnimationFrame(tick);
    else { lastT = 0; rest(); }
  }
  const kick = () => { if (mapShown) mapAway(); if (!rafId) rafId = requestAnimationFrame(tick); };
  function release() {   // the hand let go: spring to the level it was heading for
    holding = false;
    if (stage) { const d = pos - stage.last; goal = Math.max(0, Math.min(4, Math.abs(d) > 0.3 ? stage.last + Math.sign(d) : stage.last)); }
    else if (tr) { const t = progress(); goal = t > 0.3 ? tr.to : tr.from; } else goal = Math.round(goal);
    kick();
  }
  function rest() {
    if (holding) return;
    if (stage) {
      const L = Math.round(pos);
      if (Math.abs(pos - L) > 1e-3) { goal = L; kick(); return; }
      stageCommit(Math.max(stage.lo, Math.min(stage.hi, L)));
    } else if (tr) {
      const t = progress();
      if (t > 1e-3 && t < 1 - 1e-3) { goal = t > 0.5 ? tr.to : tr.from; kick(); return; }
      if (t <= 1e-3) revert(); else finish();
    }
    stageOff = false;
    busy = false;
    aimLevel = null;
    gesturePoint = null;
    document.documentElement.classList.remove('morphing');
    updateBar();
    relayout();
    refreshStale();
    hydrateSoon();
    warmSoon();
    if (queued) { const q = queued; queued = null; q(); }
  }
  function zoomBy(dz, point) {
    if (!selbar.hidden) hideSelbar();
    if (popNote) closeNote();
    if (busy && !tr && !stage) return;   // a timed change (a paragraph opening, a language) is running
    if (!tr && !stage && !holding) gesturePoint = point;   // the zoom centres on the pointer
    holding = true;
    aimLevel = null;
    goal = Math.max(0, Math.min(4, goal + dz));
    clearTimeout(snapTimer);
    snapTimer = setTimeout(release, 160);
    kick();
  }
  // a jump to a level (a click, a key, a link): one motion straight there
  function setLevel(L, anchor) {
    if (!selbar.hidden) hideSelbar();
    if (popNote) closeNote();
    L = Math.max(0, Math.min(4, Math.round(L)));
    if (busy && !tr && !stage) { queued = () => setLevel(L, anchor); return; }
    if (stage) { aimLevel = L; holding = false; goal = L; kick(); return; }   // the stage plays on to L
    if (tr) { if (progress() > 0.5) { scrub(1); finish(); } else revert(); pos = goal = level; }
    if (L === level) {
      if (localLevel.size) {   // close the paragraphs the reader opened
        const band = level === 0 ? null : bandAround(anchor || defaultAnchor(), K_BAND[level], new Set());
        morph(() => { localLevel.clear(); renderAll(unitsNear(innerHeight, innerHeight), band); }, anchor || defaultAnchor());
      }
      return;
    }
    aimLevel = L;
    aimAnchor = anchor || null;
    gesturePoint = null;
    holding = false;
    goal = L;
    kick();
  }
  // after a pause in scrolling, what is on screen becomes tokens, so the next zoom starts without that work
  let hydrateTimer = 0;
  function hydrateSoon() {
    clearTimeout(hydrateTimer);
    hydrateTimer = setTimeout(() => { if (!busy && !reduced) hydrate(200); }, 220);
  }
  // ── the stage: a zoom that lays nothing out ──────────────────────────
  // Crossing a level above costs one layout of everything near the reader (30-70 ms on an ordinary paper), a
  // hitch the hand feels on the wheel and on the slider. So while the reader is still, the stretch of paper on
  // screen is laid out at each level ahead of time, one level per idle moment, and every visible piece of it is
  // kept as a positioned copy: five keyframes. A zoom hides the page and plays the keyframes on a stage above it:
  // a piece present at two neighbouring levels slides and scales from one to the other, a piece present at one
  // fades. Every frame, level crossings included, only moves and fades copies. At rest the page is drawn at the
  // level reached, exactly as its keyframe was, and takes the stage's place in the same frame. Where no keyframe
  // is ready (a fresh page, a far jump), the zoom falls back to the live motion above.
  const stageEl = h('div', 'zstage');
  // The stage and its sandbox live in a closed shadow tree. The thousands of copies they add, move and drop are then
  // not in the page's document: nothing watching the page (browser extensions above all: translators, ad blockers,
  // which a served page lets in and a local file does not) has to process them. Measured with this computer's own
  // extensions loaded, keeping them in the page froze a served page for seconds at a time.
  const zhost = h('div', 'zhost');
  document.body.append(zhost);
  const zroot = zhost.attachShadow({ mode: 'closed' });
  zroot.adoptedStyleSheets = [shadowSheet()];
  zroot.append(stageEl);

  // the reader's own stylesheet, for inside the shadow tree: rules that start from <html> (the language's line
  // height, dark mode) start from the host instead, which carries the same attributes
  function shadowSheet() {
    const out = [];
    const hosted = (sel) => sel.replace(/(^|[\s,>+~(])(?:html|:root)((?:\[[^\]]*\]|\.[\w-]+|:not\([^)]*\))*)(?=[\s,>+~)]|$)/g,
      (m, pre, q) => `${pre}:host${q ? `(${q})` : ''}`);
    const walk = (rules) => {
      for (const r of rules) {
        if (r.media && r.cssRules) { out.push(`@media ${r.media.mediaText} {`); walk(r.cssRules); out.push('}'); }
        else if (r.selectorText !== undefined) out.push(`${hosted(r.selectorText)} { ${r.style.cssText} }`);
        else out.push(r.cssText);
      }
    };
    for (const sh of document.styleSheets) {
      let rules;
      try { rules = sh.cssRules; } catch { continue; }   // another origin's sheet (the fonts): not readable, and not needed
      if ([...rules].some((r) => r.selectorText === '.zstage')) walk(rules);
    }
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(out.join('\n'));
    return sheet;
  }
  function mirrorLang() { zhost.setAttribute('lang', document.documentElement.lang); zhost.dataset.lang = lang; }
  // and the type's (applyType): the sandbox lays a level out with the page's rules for them (a sans-serif paragraph is
  // smaller), and the stage hides the margins the page hides
  function mirrorType() {
    const r = document.documentElement;
    for (const k of ['font', 'width', 'hide', 'theme', 'style']) { if (r.dataset[k]) zhost.dataset[k] = r.dataset[k]; else delete zhost.dataset[k]; }
  }
  mirrorType();
  // KaTeX's stylesheet, once the page has it (loaded, or carried inside), is wanted inside too (drawn formulas in the
  // copies and the sandbox)
  let katexInside = false;
  function katexCss() {
    const sheet = !katexInside && document.querySelector('link[href*="katex"], style#katex-css');
    if (!sheet) return false;
    katexInside = true;
    zroot.append(sheet.cloneNode(true));
    return true;
  }
  const caps = [null, null, null, null, null];   // level -> keyframe
  const tried = [];                              // level -> the situation a capture was last attempted in
  let capEpoch = 0, stage = null, stageOff = false, warmTimer = 0, lastPointer = null, lastScrollT = 0;
  let IDS = [];                                  // units in reading order
  const HEAD_IDX = new Map();                    // heading -> reading position of the first unit after it
  const capKey = () => `${lang}|${innerWidth}|${innerHeight}|${capEpoch}`;
  const setLevelAttr = (L) => { level = L; docEl.dataset.level = L; document.documentElement.dataset.level = L; };
  // a level flipped and flipped back must leave no CSS transition running on the page, nor be measured half way
  const CSST = window.CSSTransition;
  function settleTransitions() { if (CSST) for (const a of document.getAnimations()) if (a instanceof CSST) a.finish(); }

  function bandRange(i0, i1, k) {
    const band = new Set();
    for (let i = Math.max(0, i0 - k); i <= Math.min(IDS.length - 1, i1 + k); i++) band.add(IDS[i]);
    for (const b of lvBlocks) { const p = b._pos ?? 0; if (p >= i0 - k && p <= i1 + k) band.add(b); }
    return band;
  }

  // how a piece looks, written out, since its copy will not sit where the page's CSS can reach it
  function looks(el) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return null;
    const css = `font:${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} / ${cs.lineHeight} ${cs.fontFamily};font-variant:${cs.fontVariant};` +
      `color:${cs.color};background-color:${cs.backgroundColor};letter-spacing:${cs.letterSpacing};text-transform:${cs.textTransform};` +
      `box-shadow:${cs.boxShadow};text-align:${cs.textAlign};${cs.textWrap ? `text-wrap:${cs.textWrap};` : ''}`;
    // type: what a copy's look is apart from its ink and its size (a heavier weight counts as one weight)
    return { css, fs: parseFloat(cs.fontSize) || 16, disp: cs.display, type: `${cs.fontStyle} ${parseFloat(cs.fontWeight) >= 550 ? 'b' : 'n'} ${cs.fontFamily}|${cs.backgroundColor}` };
  }
  // the boxes drawn behind the words: Topic chips and cards, theorem rules, the Takeaway spine
  const clear = (c) => !c || c === 'transparent' || /^rgba\(.*,\s*0\)$/.test(c);
  function decoLooks(el) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.display === 'contents' || cs.visibility === 'hidden') return null;
    const sides = ['Top', 'Right', 'Bottom', 'Left'].map((s) => [cs[`border${s}Width`], cs[`border${s}Style`], cs[`border${s}Color`]]);
    const drawn = !clear(cs.backgroundColor) || cs.boxShadow !== 'none' || sides.some(([w, s, c]) => parseFloat(w) > 0 && s !== 'none' && !clear(c));
    return drawn ? `background-color:${cs.backgroundColor};border-width:${sides.map((x) => x[0]).join(' ')};border-style:${sides.map((x) => x[1]).join(' ')};` +
      `border-color:${sides.map((x) => x[2]).join(' ')};border-radius:${cs.borderRadius};box-shadow:${cs.boxShadow}` : null;
  }

  // every visible piece between page heights lo and hi: where it is, how it looks, what it moves with.
  // root: the page, or a sandbox copy of it; oy turns its viewport heights into page heights; own: the pieces are
  // the sandbox's, to be moved onto the stage (the page's own are copied)
  function collect(root, units, lo, hi, oy, L, own) {
    const P = new Map(), U = new Map(), lk = new Map(), dk = new Map();
    const rectOf = (r) => ({ x: r.left, y: r.top + oy, w: r.width, h: r.height });
    const inRange = (r) => r.width > 0 && r.height > 0 && r.bottom + oy > lo && r.top + oy < hi;
    const piece = (el, id, grp, kind, ctx) => {
      const r = el.getBoundingClientRect();
      if (!inRange(r)) return false;
      const k = `${ctx}|${el.tagName}|${el.className}`;
      let st = lk.get(k);
      if (st === undefined) { st = looks(el); lk.set(k, st); }
      if (st) P.set(id, { ...rectOf(r), st, kind, grp, el, take: own });
      return !!st;
    };
    const deco = (el, id, grp, k) => {
      const r = el.getBoundingClientRect();
      if (!inRange(r)) return;
      let css = dk.get(k);
      if (css === undefined) { css = decoLooks(el); dk.set(k, css); }
      if (css) P.set(id, { ...rectOf(r), st: { css, fs: 1 }, kind: 'd', grp, id });
    };
    for (const [uid, u] of units) {
      const r = u.getBoundingClientRect();
      if (!inRange(r)) continue;
      U.set(uid, rectOf(r));
      deco(u, `d:u:${uid}`, uid, `u|${u.className}`);
      for (const el of u.querySelectorAll('[data-id]')) piece(el, el.dataset.id, uid, el.classList.contains('dot') ? 'b' : 't', u.className);
    }
    for (const sec of root.querySelectorAll('section.chunk')) deco(sec, `d:c:${sec.id}`, `h:${sec.id}`, `c|${sec.className}`);
    for (const bx of root.querySelectorAll('.box')) { const f = bx.querySelector('.u'); deco(bx, `d:b:${bx.id}`, f && f.dataset.u, `b|${bx.className}`); }
    if (L === 1) {   // the spine of a Takeaway section is a pseudo-element: drawn from its section's box
      const any = root.querySelector('section.chunk > .body');
      const line = any ? getComputedStyle(any, '::before').backgroundColor : '';
      for (const sec of root.querySelectorAll('section.chunk')) {
        const body = sec.querySelector(':scope > .body'), r = body && body.getBoundingClientRect();
        if (r && r.height > 24 && r.bottom + oy > lo && r.top + oy < hi) {
          P.set(`d:s:${sec.id}`, { x: r.left + 3, y: r.top + oy + 10, w: 1, h: r.height - 20, st: { css: `background-color:${line}`, fs: 1 }, kind: 'd', grp: `h:${sec.id}`, id: `d:s:${sec.id}` });
        }
      }
    }
    for (const el of root.querySelectorAll('.paper-head > [data-id], .hd, .stake > [data-id], .box-h, .fbody, .eq, .raw, .bib')) {
      const sec = el.closest('section.chunk');
      let grp = null, kind = 't';
      if (el.classList.contains('hd') || el.parentNode.classList.contains('stake')) grp = sec && `h:${sec.id}`;
      else if (el.classList.contains('box-h')) { const f = el.parentNode.querySelector('.u'); grp = f && f.dataset.u; }
      else {
        kind = 'b';
        const fig = el.classList.contains('fbody') ? el.parentNode : null, cap = fig && fig.querySelector('figcaption.u');
        const blk = fig || el;
        grp = cap ? cap.dataset.u : blk._pos !== undefined ? IDS[blk._pos] : null;
      }
      if (piece(el, el.dataset.id, grp, kind, el.parentNode.className) && el.classList.contains('hd')) {
        const p = P.get(el.dataset.id);
        U.set(el.dataset.id, { x: p.x, y: p.y, w: p.w, h: p.h });
      }
    }
    return { P, U };
  }

  // the pieces become copies (kept aside: the pairs below are made of them), hidden until a zoom plays them
  const DECO_Z = { c: 0, b: 1, s: 2, u: 3 };   // cards under theorem rules under the spine under chips
  const zOf = (p) => (p.kind === 'd' ? DECO_Z[p.id[2]] ?? 3 : 9);   // and the words over all of them
  let capSerial = 0;
  function keep(L, got, lo, hi, hot, band, start, end) {
    for (const p of got.P.values()) {
      let el;
      if (p.kind === 'd') {
        el = document.createElement('div');
        el.className = 'zp';
        el.style.cssText = `${p.st.css};width:${p.w}px;height:${p.h}px;opacity:0`;
      } else {
        el = p.take ? p.el : p.el.cloneNode(true);
        el.removeAttribute('data-id'); el.removeAttribute('id');
        for (const x of el.querySelectorAll('[id]')) x.removeAttribute('id');
        el.classList.remove('flash');
        const inline = el.tagName === 'SPAN';
        el.classList.add('zp', inline ? 'zi' : 'zb');
        el.style.cssText = `${p.st.css}display:${inline ? 'inline-block' : p.st.disp};width:${p.w}px;height:${p.h}px;opacity:0`;
      }
      p.pp = { el };
      p.el = null;
    }
    caps[L] = { key: capKey(), P: got.P, U: got.U, lo, hi, hot, band, start, end, n: ++capSerial };
  }
  function drop(L) { caps[L] = null; dropPair(L); dropPair(L + 1); }

  // Pairs: the copies of two neighbouring levels, grouped by how they move from one to the other. Copies that move
  // alike (the words of a line that keeps its shape, the words of a paragraph fading out) are one group: one layer the
  // GPU draws once, one animation. A dense paper then moves a few hundred groups instead of thousands of words: each
  // animation costs the page a little every frame, and thousands of them filled the frame (2512.23916: half the frames
  // dropped). A pair is built while the reader is still, once both its levels have keyframes; a zoom only plays it.
  const pairs = [null, null, null, null, null];   // the upper level -> its pair with the level below
  function pairReady(hi) { const P = pairs[hi], A = caps[hi], B = caps[hi - 1]; return !!(P && A && B && P.a === A.n && P.b === B.n); }
  function dropPair(hi) { if (pairs[hi]) { pairs[hi].layer.remove(); pairs[hi] = null; } }
  // How one copy can stand for both: a text copy in the same type (ink and size aside) scales by the ratio of the font
  // sizes, a box of the same size moves as it is; its ink turns at the level. When the type changes (the author's serif
  // to the summary's sans), two copies crossfade. Returns the scale, or 0.
  function oneCopy(a, b) {
    if ((a.st.type || a.st.css) !== (b.st.type || b.st.css)) return 0;
    if (a.kind === 't' && b.kind === 't') { const k = b.st.fs / a.st.fs; return Math.abs(b.w - k * a.w) < 1.5 && Math.abs(b.h - k * a.h) < 2.5 ? k : 0; }
    return Math.abs(a.w - b.w) < 0.5 && Math.abs(a.h - b.h) < 0.5 ? 1 : 0;
  }
  function buildPair(hi) {
    const A = caps[hi], B = caps[hi - 1], groups = [], by = new Map();
    const group = (key, make) => {
      let g = by.get(key);
      if (!g) { g = { ...make(), el: h('div', 'zg'), y0: Infinity, y1: -Infinity }; by.set(key, g); groups.push(g); }
      return g;
    };
    const add = (g, p, x, y) => {   // a member sits at its own place, shown: its group shows, hides and moves it
      const el = p.pp.el.cloneNode(true);
      el.style.transform = `translate(${x}px,${y}px)`;
      el.style.opacity = '1';
      g.el.append(el);
      g.y0 = Math.min(g.y0, y); g.y1 = Math.max(g.y1, y + p.h);
    };
    for (const [id, a] of A.P) {
      const b = B.P.get(id), z = zOf(a), k = b ? oneCopy(a, b) : 0;
      if (k) {   // copies that map the same way (scale k, then the same shift: a line keeping its shape) are one group
        const dx = b.x - k * a.x, dy = b.y - k * a.y;
        add(group(`m|${z}|${a.grp}|${k.toFixed(3)}|${dx.toFixed(1)}|${dy.toFixed(1)}`, () => ({ kind: 'move', k, dx, dy, z })), a, a.x, a.y);
      } else if (b) {
        const text = a.kind === 't' && b.kind === 't';
        groups.push({ kind: 'x', side: 0, a, b, text, el: a.pp.el.cloneNode(true), z }, { kind: 'x', side: 1, a, b, text, el: b.pp.el.cloneNode(true), z });
      } else add(group(`o|${z}|${a.grp}`, () => ({ kind: 'out', grp: a.grp, z })), a, a.x, a.y);
    }
    for (const [id, b] of B.P) if (!A.P.has(id)) { const z = zOf(b); add(group(`i|${z}|${b.grp}`, () => ({ kind: 'in', grp: b.grp, z })), b, b.x, b.y); }
    groups.sort((x, y) => x.z - y.z);
    const layer = h('div', 'zl');
    layer.append(...groups.map((g) => g.el));
    dropPair(hi);
    stageEl.append(layer);
    // laid out once now, while nothing moves; then set aside (a set-aside layer keeps its layout, so showing it is cheap)
    layer.classList.add('live');
    requestAnimationFrame(() => setTimeout(() => { if (!(stage && stage.pair && stage.pair.P.layer === layer)) layer.classList.remove('live'); }));
    pairs[hi] = { a: A.n, b: B.n, layer, groups };
  }
  // the page's top and end are where a zoom must stop too; known when the band reaches them
  const reachesStart = (band) => !band || band.has(IDS[0]);
  const reachesEnd = (band) => !band || band.has(IDS[IDS.length - 1]);

  // the level on screen: the page as it is, a screen above and below, its tokens copied
  function captureHere() {
    const H = innerHeight, sy = scrollY, lo = sy - H, hi = sy + 2 * H;
    const near = new Set();
    for (const [uid, el] of unitEl) { const r = el.getBoundingClientRect(); if ((r.width || r.height) && r.bottom + sy > lo && r.top + sy < hi) near.add(uid); }
    if (!near.size) return;
    for (const uid of near) if (unitEl.get(uid)._mode !== 'tok') renderUnit(uid, true);
    const got = collect(docEl, [...near].map((u) => [u, unitEl.get(u)]), lo, hi, scrollY, level, false);
    const idx = [...near].map((u) => order.get(u));
    const band = level === 0 ? null : bandRange(Math.min(...idx), Math.max(...idx), K_BAND[level]);
    keep(level, got, lo, hi, near, band, reachesStart(band) ? 0 : -Infinity, reachesEnd(band) ? docEl.getBoundingClientRect().bottom + scrollY : Infinity);
  }

  // Another level is laid out in a sandbox: a hidden copy of the stretch of paper a zoom would draw (the band,
  // with the sections and boxes around it), never painted. The page itself is not touched, so nothing of it has
  // to be laid out again; the sandbox's pieces are measured and moved onto the stage.
  const sandEl = h('div', 'zsand');
  zroot.append(sandEl);
  function sandbox(L, band, first, last) {
    const units = new Map(), heads = new Map(), lv = new Set(lvBlocks);
    const inBand = (x) => !band || band.has(x);
    const S = band && unitEl.get(first), E = band && unitEl.get(last);
    const between = (sec) => {   // a section from the band's first unit to its last, units of its own or not
      if (!band) return true;
      const p = S.compareDocumentPosition(sec), q = E.compareDocumentPosition(sec);
      return !!((p & Node.DOCUMENT_POSITION_CONTAINS) || (q & Node.DOCUMENT_POSITION_CONTAINS) ||
        ((p & Node.DOCUMENT_POSITION_FOLLOWING) && (q & Node.DOCUMENT_POSITION_PRECEDING)));
    };
    const plain = (el) => { el.classList.remove('linked', 'focal', 'flash', 'local-open'); return el; };
    function copy(node) {
      const cl = node.classList;
      if (cl.contains('u')) {
        const uid = node.dataset.u;
        if (!inBand(uid)) return null;
        const el = plain(node.cloneNode(false));
        for (const c of node.children) {
          if (c === node._tx) { el._tx = c.cloneNode(false); el.append(el._tx); } else el.append(c.cloneNode(true));
        }
        units.set(uid, el);
        drawUnit(uid, L, el, null, false);
        return el;
      }
      if (lv.has(node) && !inBand(node)) return null;
      if (node.tagName === 'FIGURE') { const cap = node.querySelector(':scope > figcaption.u'); if (cap && !inBand(cap.dataset.u)) return null; }
      if (cl.contains('hd')) { const el = plain(node.cloneNode(true)); heads.set(el.dataset.id, el); return el; }
      if (cl.contains('stake') || cl.contains('box-h') || cl.contains('fbody') || cl.contains('eq') || cl.contains('raw')) {
        const el = node.cloneNode(true);
        if (lv.has(node)) { el.dataset.l = L; el._pos = node._pos; }
        return el;
      }
      if (cl.contains('side') || cl.contains('lside')) return node.cloneNode(true);
      if (cl.contains('paper-head')) return reachesStart(band) ? node.cloneNode(true) : null;
      if (cl.contains('bib')) return reachesEnd(band) ? node.cloneNode(true) : null;
      if (node.tagName === 'SECTION' && !between(node)) return null;
      const el = plain(node.cloneNode(false));   // a container: kept with what it holds of the band
      if (lv.has(node) || node.tagName === 'FIGURE') el.dataset.l = L;
      el._pos = node._pos;
      let any = false;
      for (const c of node.children) { const k = copy(c); if (k) { el.append(k); any = true; } }
      return any || node.tagName === 'SECTION' || cl.contains('chunks') ? el : null;
    }
    const root = h('div', CJK_DOC ? 'doc cjk' : 'doc');
    root.dataset.level = L;
    for (const c of docEl.children) { const k = copy(c); if (k) root.append(k); }
    return { root, units, heads };
  }
  function captureAt(L, cands) {
    const H = innerHeight, k = K_BAND[L];
    const idx = cands.map((c) => c.idx).filter((i) => i !== undefined);
    if (!idx.length) return;
    const i0 = Math.min(...idx), i1 = Math.max(...idx);
    const band = L === 0 ? null : bandRange(i0, i1, k);
    const first = IDS[Math.max(0, i0 - k)], last = IDS[Math.min(IDS.length - 1, i1 + k)];
    const { root, units, heads } = sandbox(L, band, first, last);
    const bs = getComputedStyle(document.body), pl = parseFloat(bs.paddingLeft) || 0;
    sandEl.style.left = `${pl}px`;
    sandEl.style.width = `${document.body.clientWidth - pl - (parseFloat(bs.paddingRight) || 0)}px`;
    sandEl.replaceChildren(root);
    const oy = docEl.getBoundingClientRect().top + scrollY - root.getBoundingClientRect().top;   // (lays the sandbox out)
    const lim = { start: reachesStart(band) ? 0 : -Infinity, end: reachesEnd(band) ? root.getBoundingClientRect().bottom + oy : Infinity };
    // what a zoom anchored on any of the candidates may show at L
    let lo = Infinity, hi = -Infinity;
    for (const c of cands) {
      const el = c.el.dataset.u ? units.get(c.key) : heads.get(c.key), r = el && el.getBoundingClientRect();
      if (!r || (!r.width && !r.height)) continue;
      const [a, b] = need(c, { y: r.top + oy, h: r.height }, lim);
      lo = Math.min(lo, a); hi = Math.max(hi, b);
    }
    lo -= 0.15 * H; hi += 0.15 * H;
    if (band) {   // nothing beyond the band was drawn
      const fe = first !== IDS[0] && units.get(first), le = last !== IDS[IDS.length - 1] && units.get(last);
      if (fe) lo = Math.max(lo, fe.getBoundingClientRect().top + oy);
      if (le) hi = Math.min(hi, le.getBoundingClientRect().bottom + oy);
    }
    let got = null;
    const hot = new Set();
    if (lo < hi) {
      for (const [uid, el] of units) {
        const r = el.getBoundingClientRect();
        if ((r.width || r.height) && r.bottom + oy > lo - 0.3 * H && r.top + oy < hi + 0.3 * H) hot.add(uid);
      }
      for (const uid of hot) drawUnit(uid, L, units.get(uid), new Map(), true);
      got = collect(root, [...hot].map((u) => [u, units.get(u)]), lo, hi, oy, L, true);
      if (lim.end !== Infinity) lim.end = root.getBoundingClientRect().bottom + oy;
    }
    sandEl.replaceChildren();
    if (got) keep(L, got, lo, hi, hot, band, lim.start, lim.end);
  }

  // The stretch of page (at some level, in its keyframe's coordinates) that a zoom anchored on c may show. The
  // anchor's point under the pointer stays put; the pointer may be anywhere on c that is on screen. The page
  // cannot scroll above its top or below its end, and neither does the stage.
  const clampTop = (y, lim) => Math.max(lim.start, Math.min(Math.max(lim.start, lim.end - innerHeight), y));
  function need(c, at, lim) {
    const H = innerHeight;
    const f0 = c.Hc ? Math.min(1, Math.max(0, -c.T / c.Hc)) : 0, f1 = c.Hc ? Math.min(1, Math.max(0, (H - c.T) / c.Hc)) : 0;
    const top = (f) => clampTop(at.y + f * at.h - (c.T + f * c.Hc), lim);
    const a = top(f0), b = top(f1);
    return [Math.min(a, b), Math.max(a, b) + H];
  }
  // what a zoom may be anchored on: whatever is on screen (units and headings)
  function visibleCands() {
    const H = innerHeight, out = [];
    const add = (el, key, idx) => { const r = el.getBoundingClientRect(); if ((r.width || r.height) && r.bottom > 0 && r.top < H) out.push({ el, key, idx, T: r.top, Hc: r.height, X: r.left }); };
    for (const [uid, el] of unitEl) add(el, uid, order.get(uid));
    for (const [, hd] of headEls) add(hd, hd.dataset.id, HEAD_IDX.get(hd));
    return out;
  }
  // zooming in, every anchor on a full screen of chips would need the whole paper laid out at the deeper levels:
  // prepare for the one a zoom will likely start from: what is under the pointer when it rests on the paper,
  // else the reading line (where the slider and the keys zoom). A few paragraphs on screen are all prepared.
  function zoomInCands(all) {
    const out = all.length <= 12 ? [...all] : [];
    const onPaper = lastPointer && (() => { const e = document.elementFromPoint(lastPointer.x, lastPointer.y); return e && docEl.contains(e); })();
    const a = onPaper ? anchorAt(lastPointer.x, lastPointer.y) : defaultAnchor();
    const key = a && (a.el.dataset.u || a.el.dataset.id);
    if (key && !out.some((x) => x.key === key)) {
      const r = a.el.getBoundingClientRect();
      out.push({ el: a.el, key, idx: a.el.dataset.u ? order.get(key) : HEAD_IDX.get(a.el), T: r.top, Hc: r.height, X: r.left });
    }
    return out;
  }
  // the keyframe of the level on screen must be the page as it is: every visible unit where the keyframe has it
  // (up to one offset for all, when something far above changed height), and the screen inside it
  function fits(cap, vis) {
    let d0 = null;
    for (const c of vis) {
      const at = cap.U.get(c.key);
      if (!at) return null;
      const d = at.y - (c.T + scrollY);
      d0 ??= d;
      if (Math.abs(d - d0) > 1 || Math.abs(at.h - c.Hc) > 1 || Math.abs(at.x - c.X) > 1) return null;
    }
    if (d0 === null) return null;
    const top = scrollY + d0;
    return top >= cap.lo && top + innerHeight <= cap.hi ? { d0 } : null;
  }
  function covers(L, cands) {
    const cap = caps[L];
    if (!cap) return false;
    if (L === level) return !!fits(cap, cands);
    for (const c of cands) {
      const at = cap.U.get(c.key);
      if (!at) return false;
      const [a, b] = need(c, at, cap);
      if (a < cap.lo || b > cap.hi) return false;
    }
    return true;
  }

  // while the reader is still: the next keyframe that is missing, one per task, nearest level first
  function warmSoon(ms = 360) { clearTimeout(warmTimer); if (!reduced) warmTimer = setTimeout(warm, ms); }
  function warm() {
    if (busy || stage || tr || drag || holding || localLevel.size || staleQ.length || document.hidden) return;
    if (performance.now() - lastScrollT < 180) return warmSoon(200);
    if (katexCss()) { capEpoch++; return warmSoon(400); }
    const key = capKey();
    caps.forEach((c, L) => { if (c && c.key !== key) drop(L); });
    const all = visibleCands();
    if (!all.length) return;
    for (const L of [level, level - 1, level + 1, level - 2, level + 2, level - 3, level + 3, level - 4, level + 4]) {
      if (L < 0 || L > 4) continue;
      const cands = L <= level ? all : zoomInCands(all);
      if (covers(L, cands)) continue;
      const sig = `${key}|${level}|${Math.round(scrollY)}|${cands.map((c) => c.key).join(',')}`;
      if (tried[L] === sig) continue;   // tried in this very situation already: it cannot do better
      // the map lit up under the pointer would be copied lit; other levels are laid out apart and can go ahead
      if (L === level && (docEl.classList.contains('mapfocus') || docEl.dataset.role)) { warmSoon(600); continue; }
      tried[L] = sig;
      const t0 = performance.now();
      if (L === level) captureHere(); else captureAt(L, cands);
      perf(`cap${L}`, t0);
      return warmSoon(20);
    }
    for (const up of [level, level + 1, level - 1, level + 2, level - 2, level + 3, level - 3, level + 4]) {
      if (up < 1 || up > 4 || !caps[up] || !caps[up - 1] || pairReady(up)) continue;
      const t0 = performance.now();
      buildPair(up);
      perf(`pair${up}`, t0);
      return warmSoon(20);
    }
  }

  // a zoom begins: the keyframes take over if they hold the screen at this level and at the next in its direction
  function stageBegin() {
    if (reduced || localLevel.size) return false;
    const t0 = performance.now(), key0 = capKey(), cap = caps[level];
    if (!cap || cap.key !== key0) return false;
    const f = fits(cap, visibleCands());
    if (!f) return false;
    const anchor = aimAnchor || (gesturePoint ? anchorAt(gesturePoint.x, gesturePoint.y) : defaultAnchor());
    if (!anchor) return false;
    const key = anchor.el.dataset.u || anchor.el.dataset.id, r = anchor.el.getBoundingClientRect();
    const P = r.top + anchor.frac * r.height, shift = [];
    const ok = (L) => {   // each level is placed so the anchor's point stays where it is on screen
      const c = caps[L];
      if (!c || c.key !== key0) return false;
      if (L === level) { shift[L] = scrollY + f.d0; return true; }
      const at = c.U.get(key);
      if (!at) return false;
      const s = clampTop(at.y + anchor.frac * at.h - P, c);
      if (s < c.lo || s + innerHeight > c.hi) return false;
      shift[L] = s;
      return true;
    };
    ok(level);
    let lo = level, hi = level;
    while (lo > 0 && ok(lo - 1) && pairReady(lo)) lo--;
    while (hi < 4 && ok(hi + 1) && pairReady(hi + 1)) hi++;
    if (pos < level ? lo === level : hi === level) return false;
    aimAnchor = null;
    stage = { el: anchor.el, key, shift, lo, hi, pair: null, last: level, sy: scrollY };
    busy = true;
    clearFocus(); hidePeek();
    document.documentElement.classList.add('morphing');
    stageEl.classList.add('on');   // opaque: it covers the page, which stays as it is and is not painted again
    perf('stage', t0);
    if (window.__drPerf) window.__drPerf.push(['t.stage', performance.now()]);
    if (window.__drDrift) window.__drPerf.push(['beginDrift', stageDrift(level, shift[level])]);
    return true;
  }

  // Between two neighbouring levels: what each copy does as the position runs from the upper level (t = 0) to the
  // lower (t = 1), written as one paused animation per copy whose progress is the position. Moving an animation's
  // progress writes nothing into the document, so the page's observers (and browser extensions watching the page:
  // translators, ad blockers, which are let into a served page though not into a file) have nothing to process, and
  // each copy is a layer the GPU moves without drawing it again. Only copies whose path crosses the screen play.
  const SMOOTH = [0, 0.25, 0.5, 0.75, 1].map((x) => [x, x * x * (3 - 2 * x)]);   // smoothstep, sampled
  const fadeOut = (a, b) => [{ offset: 0, opacity: 1 }, ...SMOOTH.map(([x, y]) => ({ offset: a + (b - a) * x, opacity: 1 - y })), { offset: 1, opacity: 0 }];
  const fadeIn = (a, b) => [{ offset: 0, opacity: 0 }, ...SMOOTH.map(([x, y]) => ({ offset: a + (b - a) * x, opacity: y })), { offset: 1, opacity: 1 }];
  const STAY = [{ offset: 0, opacity: 1 }, { offset: 1, opacity: 1 }];
  const tf = (x, y, sx, sy) => `translate(${x.toFixed(2)}px,${y.toFixed(2)}px) scale(${sx.toFixed(4)},${sy.toFixed(4)})`;
  function enterPair(lo) {
    const s = stage, prev = s.pair, P = pairs[lo + 1];
    if (prev) {
      s.last = lo < prev.lo ? lo + 1 : lo;   // the level just crossed
      for (const an of prev.anims) an.cancel();   // its groups fall back to their resting state: hidden
      if (prev.P !== P) { prev.P.layer.classList.remove('live'); prev.P.layer.style.opacity = '0'; }
    }
    P.layer.classList.add('live');   // only the pair in play is rendered at all; the others skip style, paint and hit testing
    P.layer.style.opacity = '1';
    const A = caps[lo + 1], B = caps[lo], sa = s.shift[lo + 1], sb = s.shift[lo], H = innerHeight, anims = [];
    const seen = (y0, y1) => y1 > -120 && y0 < H + 120;
    const drift = (g) => { const a = g && A.U.get(g), b = g && B.U.get(g); return a && b ? [b.x - a.x, (b.y - sb) - (a.y - sa)] : [0, 0]; };
    const play = (el, from, to, fades) => {
      const an = el.animate([{ offset: 0, transform: from }, ...fades, { offset: 1, transform: to }], { duration: 1000, fill: 'both' });
      an.pause();
      anims.push(an);
    };
    for (const g of P.groups) {
      if (g.kind === 'move') {   // a group moving as one (scaled by k), from the upper level's place to the lower's
        if (!seen(Math.min(g.y0 - sa, g.k * g.y0 + g.dy - sb), Math.max(g.y1 - sa, g.k * g.y1 + g.dy - sb))) continue;
        play(g.el, tf(0, -sa, 1, 1), tf(g.dx, g.dy - sb, g.k, g.k), STAY);
      } else if (g.kind === 'out') {   // only above: it travels with its paragraph and fades in the first half
        if (!seen(g.y0 - sa, g.y1 - sa)) continue;   // seen only where it starts (gone before it would come into view)
        const [dx, dy] = drift(g.grp);
        play(g.el, tf(0, -sa, 1, 1), tf(dx, dy - sa, 1, 1), fadeOut(0, 0.5));
      } else if (g.kind === 'in') {   // only below: it arrives with its paragraph in the second half
        if (!seen(g.y0 - sb, g.y1 - sb)) continue;
        const [dx, dy] = drift(g.grp);
        play(g.el, tf(-dx, -dy - sb, 1, 1), tf(0, -sb, 1, 1), fadeIn(0.35, 1));
      } else {   // a copy whose type changes: it slides and scales between its two places, crossfading with its twin
        const { a, b } = g, ya = a.y - sa, yb = b.y - sb, me = g.side ? b : a;
        if (!seen(Math.min(ya, yb), Math.max(ya + a.h, yb + b.h) + 40)) continue;
        // text scales by its font size, centred on its line; a box takes the other box
        const ends = g.text
          ? [tf(a.x, ya + a.h / 2 - (me.h * a.st.fs / me.st.fs) / 2, a.st.fs / me.st.fs, a.st.fs / me.st.fs), tf(b.x, yb + b.h / 2 - (me.h * b.st.fs / me.st.fs) / 2, b.st.fs / me.st.fs, b.st.fs / me.st.fs)]
          : [tf(a.x, ya, a.w / me.w, a.h / me.h), tf(b.x, yb, b.w / me.w, b.h / me.h)];
        play(g.el, ...ends, g.side ? fadeIn(0.3, 0.6) : fadeOut(0.4, 0.7));
      }
    }
    s.pair = { lo, anims, P };
    if (window.__drPerf) window.__drPerf.push(['pair', lo, anims.length, P.groups.length, A.P.size + B.P.size]);
  }
  function stageFrame() {
    const s = stage;
    if (pos < s.lo - 1e-6 || pos > s.hi + 1e-6) {   // past the last keyframe: the live page carries on from there
      stageCommit(pos < s.lo ? s.lo : s.hi);
      stageOff = true;
      busy = false;
      return false;
    }
    const lo = Math.max(s.lo, Math.min(s.hi - 1, Math.floor(pos))), t = Math.min(1, Math.max(0, lo + 1 - pos));
    if (!s.pair || s.pair.lo !== lo) enterPair(lo);
    const near = Math.round(pos);
    if (s.chrome !== near) {   // the legend and the tagline follow the zoom (set on them: an attribute on <html> restyles everything)
      s.chrome = near;
      legend.style.display = near <= 2 ? 'flex' : 'none';
      tagline.style.display = near <= 2 ? 'none' : '';
    }
    const ms = t * 1000;
    for (const an of s.pair.anims) an.currentTime = ms;
    return true;
  }
  // the zoom came to rest on L: the page is drawn at L as its keyframe was and shown where the stage shows it
  function stageCommit(L) {
    const s = stage, t0 = performance.now();
    stage = null;
    if (s.pair) for (const an of s.pair.anims) an.cancel();
    if (L !== level) {
      const cap = caps[L];
      setLevelAttr(L);
      localLevel.clear();
      renderAll(cap.hot, cap.band);
      settleTransitions();
      const at = cap.U.get(s.key), r = visibleRect(s.el);
      if (at && r) window.scrollBy(0, r.top - (at.y - s.shift[L]));
      if (window.__drDrift) window.__drPerf.push(['commitDrift', L, stageDrift(L, s.shift[L])]);
    }
    for (const P of pairs) if (P) { P.layer.classList.remove('live'); P.layer.style.opacity = '0'; }
    legend.style.display = ''; tagline.style.display = '';
    stageEl.classList.remove('on');
    perf('commit', t0);
    if (window.__drPerf) window.__drPerf.push(['t.commit', performance.now()]);
  }
  // the reader scrolls, or the window changes, mid-zoom: the zoom lands on the nearest level at once
  function stageDrift(L, shift) {   // (measuring only: how far the copies sit from the page's own pieces)
    const errs = [];
    for (const [id, p] of caps[L].P) {
      if (p.kind === 'd' || errs.length > 400) continue;
      const el = docEl.querySelector(`[data-id="${CSS.escape(id)}"]`), r = el && visibleRect(el);
      if (!r || r.bottom < 0 || r.top > innerHeight) continue;
      errs.push(Math.max(Math.abs(r.left - p.x), Math.abs(r.top - (p.y - shift)), Math.abs(r.width - p.w), Math.abs(r.height - p.h)));
    }
    errs.sort((a, b) => a - b);
    return { n: errs.length, med: Math.round((errs[errs.length >> 1] || 0) * 100) / 100, max: Math.round((errs[errs.length - 1] || 0) * 100) / 100 };
  }
  function snapNow() {
    if (!stage) return;
    clearTimeout(snapTimer);
    holding = false;
    cancelAnimationFrame(rafId); rafId = 0; lastT = 0;
    pos = goal = Math.max(stage.lo, Math.min(stage.hi, Math.round(pos)));
    stageFrame();
    rest();
  }
  addEventListener('scroll', () => {
    lastScrollT = performance.now();
    if (stage && Math.abs(scrollY - stage.sy) > 1) snapNow();
    if (!busy) { hydrateSoon(); warmSoon(200); }   // a fifth of a second still: the reader stopped there
  }, { passive: true });
  addEventListener('pointermove', (e) => { lastPointer = { x: e.clientX, y: e.clientY }; if (level < 4 && !busy) warmSoon(); }, { passive: true });

  function setLang(l) {
    if (l === lang) return;
    if (!AVAIL.includes(l)) { if (window.DRApp) window.DRApp.generate(l, false); return; }
    if (busy) { queued = () => setLang(l); return; }
    store.set('dr-lang', l);
    readerLang = l; ui = I18N.uiFor(l); store.set(UI_KEY, l);   // the interface goes with the text (in English when it has no words for it)
    if (D.app) history.replaceState(null, '', `?lang=${encodeURIComponent(l)}${location.hash}`);
    const hot = unitsNear(innerHeight * 1.5, innerHeight * 1.5);
    const band = level === 0 ? null : bandAround(defaultAnchor(), { 4: 30, 3: 45, 2: 60, 1: 150 }[level], hot);
    morph(() => { lang = l; applyLang(); renderAll(hot, band); }, defaultAnchor());
  }

  function toggleUnit(uid) {
    if (busy) return;
    const el = unitEl.get(uid);
    const r = el.getBoundingClientRect();
    morph(() => {
      if (localLevel.has(uid)) localLevel.delete(uid); else localLevel.set(uid, 4);
      renderUnit(uid, true);
    }, { el, y: r.top, frac: 0 });
  }

  function defaultAnchor() {
    const col = docEl.getBoundingClientRect();
    return anchorAt(col.left + Math.min(col.width / 2, 260), innerHeight * 0.38);
  }

  // ── links: data ──────────────────────────────────────────────────────
  const outOf = new Map(), inOf = new Map();
  for (const e of EDGES) {
    if (!outOf.has(e.f)) outOf.set(e.f, []);
    outOf.get(e.f).push(e);
    if (e.tk === 'unit') { if (!inOf.has(e.t)) inOf.set(e.t, []); inOf.get(e.t).push(e); }
  }
  const unitBox = (uid) => figOf.get(uid) || unitEl.get(uid);
  function edgeTarget(tk, id) {
    if (tk === 'unit') return unitBox(id);
    if (tk === 'chunk') { const s = document.getElementById(id); return s && s.querySelector('.hd'); }
    if (tk === 'eq') return docEl.querySelector(`[data-id="eq:${CSS.escape(id)}"]`);
    return document.getElementById(id);
  }
  function resolve(htmlId) {
    const el = document.getElementById(htmlId) || document.getElementById(ALIAS[htmlId]);
    if (!el) return null;
    const box = el.closest('.u, figure.fig, .eq, .hd, .box, section.chunk, li.ltx_bibitem');
    if (!box) return el;
    if (box.matches('section.chunk')) return box.querySelector('.hd');
    if (box.matches('figcaption.u')) return box.closest('figure.fig');
    return box;
  }
  const secLabel = (uid) => { const c = chunkOf[uid]; return c ? (c.num ? `§${c.num}` : ((trOf(c) || c).title).replace(/\$/g, '')) : ''; };
  function figLabel(uid) { const f = figOf.get(uid); const fl = f && f.querySelector('.flabel'); return fl ? fl.textContent : ''; }
  function targetLabel(tk, id) {
    if (tk === 'unit') {
      const v = view(id);
      return `<span class="where">${esc(figLabel(id) || secLabel(id))}</span> ${toksHtml(v.topic)}`;
    }
    if (tk === 'chunk') {
      const c = D.chunks.find((x) => x.id === id);
      return c ? `<span class="where">§${esc(c.num)}</span> ${chunkTitle(c)}` : '';
    }
    if (tk === 'eq') {
      const el = edgeTarget(tk, id), tag = el && el.querySelector('.ltx_tag_equation');
      return `<span class="where">${(T[lang] || T.en).eq}</span> ${esc(tag ? tag.textContent : '')}`;
    }
    return esc(id);
  }

  // sidenotes: the unit's argument links (and, at Brief, its cross-references), in the right margin
  // The margins of a unit, or of a section (hung after its heading). Right: the conversations with the model, then
  // the links. Left: the reader's own notes. The reader on the left in warm ink, the machine on the right in cool ink.
  const secs = new Map();   // section id -> { c, sec, hd, st }
  function sideNode(uid) {
    if (!outOf.has(uid) && !inOf.has(uid)) return null;
    return ensureSide(uid);
  }
  function hostOf(key) {
    if (key === '¶') return { el: paperHead, after: null };   // the whole paper: its head (the reader's notes on it)
    if (!key.startsWith('§')) return { el: unitEl.get(key), after: null };
    const s = secs.get(key.slice(1));
    return s ? { el: s.sec, after: s.hd } : null;
  }
  function ensureSide(key, left = false) {
    const host = hostOf(key), cls = left ? 'lside' : 'side';
    if (!host || !host.el) return null;
    let s = host.el.querySelector(`:scope > .${cls}`);
    if (!s) {
      s = h('div', cls + (host.after ? ' sec' : ''));
      if (!left) s.append(h('div', 'side-notes'), h('div', 'side-links'));
      const links = left && host.el.querySelector(':scope > .side');
      if (links) links.before(s); else if (host.after) host.after.after(s); else host.el.append(s);
    }
    return s;
  }
  function renderSide(uid) {
    const side = unitEl.get(uid).querySelector(':scope > .side'), s = side && side.querySelector(':scope > .side-links');
    if (!s) return;
    const t = T[ui], rows = [];
    for (const e of outOf.get(uid) || []) rows.push({ e, dir: 0, tk: e.tk, id: e.t });
    for (const e of inOf.get(uid) || []) rows.push({ e, dir: 1, tk: 'unit', id: e.f });
    const order = (r) => (r.e.rel === 'ref' ? 2 : 0) + r.dir;
    rows.sort((a, b) => order(a) - order(b));
    s.innerHTML = rows.slice(0, 6).map((r) =>
      `<button type="button" class="lk rel-${r.e.rel}${r.e.rel === 'ref' ? ' ref' : ''}" data-tk="${r.tk}" data-to="${esc(r.id)}">` +
      `<span class="arr">${r.dir ? '←' : '→'}</span><span class="rel">${esc(t.rel[r.e.rel][r.dir])}</span><span class="lbl">${targetLabel(r.tk, r.id)}</span></button>`).join('');
    texify(s);
  }

  // keep sidenotes from overlapping: each sits at its paragraph, or just below the one above it
  // all writes, then all reads, then all writes: one layout, not one per sidenote
  function layoutSides() {
    const all = docEl.querySelectorAll('.side, .lside');
    for (const sd of all) sd.style.marginTop = '';
    const root = document.documentElement;   // beside the PDF, or beside a pinned sidebar in a window too narrow for both: in the column
    if (root.classList.contains('compare') || root.classList.contains('sb-narrow') || !matchMedia(D.app ? '(min-width: 1300px)' : '(min-width: 1240px)').matches) return;
    const moves = [];
    for (const col of ['.side', '.lside']) {   // the right margin, then the left: each stacks on its own
      const shown = [...docEl.querySelectorAll(col)].map((sd) => [sd, sd.getBoundingClientRect()]).filter(([, r]) => r.height)
        .sort((a, b) => a[1].top - b[1].top);
      let floor = -Infinity;
      for (const [sd, r] of shown) {
        const top = r.top + scrollY, shift = Math.max(0, floor + 8 - top);
        if (shift) moves.push([sd, shift]);
        floor = top + shift + r.height;
      }
    }
    for (const [sd, shift] of moves) sd.style.marginTop = `${shift}px`;
  }

  // (Brief and Takeaway draw no links: with nothing to do next from a line, a line was only decoration.)
  let focused = null;
  function clearFocus() {
    focused = null;
    docEl.classList.remove('mapfocus');
    docEl.querySelectorAll('.linked, .focal').forEach((e) => e.classList.remove('linked', 'focal'));
    for (const [el, cls] of lit) el.classList.remove(cls);
    lit = [];
  }
  // ── links on the Topic map: no lines. The hovered chip's linked chips stay lit (tinted by relation),
  //    the rest of the map steps back, so the eye finds them without anything being drawn over the cards ──
  let lit = [];
  function mapFocus(uid) {
    clearFocus();
    const ends = [...(outOf.get(uid) || []).map((e) => [e, e.tk, e.t]), ...(inOf.get(uid) || []).map((e) => [e, 'unit', e.f])];
    for (const [e, tk, id] of ends) {
      const el = tk === 'unit' ? unitEl.get(id) : edgeTarget(tk, id);
      if (!el || !visibleRect(el)) continue;
      el.classList.add('linked', `rel-${e.rel}`);
      lit.push([el, `rel-${e.rel}`]);
    }
    if (!lit.length) return;          // a chip without links: nothing dims
    const me = unitEl.get(uid);
    me.classList.add('focal'); lit.push([me, 'focal']);
    docEl.classList.add('mapfocus');
  }

  // ── links: following one, and coming back ───────────────────────────
  const backBtn = document.getElementById('back');
  let backTo = null;
  function flash(el) {
    el.classList.remove('flash');
    void el.offsetWidth;
    el.classList.add('flash');
    setTimeout(() => el.classList.remove('flash'), 1900);
  }
  function go(el, from) {
    if (!el) return;
    if (!visibleRect(el) && level < 3) {      // an equation or table body is hidden below Key: step in first
      setLevel(3);
      setTimeout(() => go(el, from), DUR + 120);
      return;
    }
    backTo = { y: scrollY, level, from };
    const r = el.getBoundingClientRect();
    window.scrollTo({ top: scrollY + r.top - innerHeight * 0.28, behavior: reduced ? 'auto' : 'smooth' });
    flash(el);
    backBtn.hidden = false;
  }
  backBtn.addEventListener('click', () => {
    const b = backTo; backTo = null; backBtn.hidden = true;
    if (!b) return;
    if (b.level !== level) {
      setLevel(b.level);
      setTimeout(() => { if (b.from) { b.from.scrollIntoView({ block: 'center', behavior: 'smooth' }); flash(b.from); } }, DUR + 80);
    } else {
      window.scrollTo({ top: b.y, behavior: reduced ? 'auto' : 'smooth' });
      if (b.from) flash(b.from);
    }
  });

  // ── links: hover previews ────────────────────────────────────────────
  const peek = h('div', 'peek');
  document.body.append(peek);
  let peekTimer = null, peekFor = null;
  function peekHtml(el) {
    if (!el) return '';
    if (el.matches('li.ltx_bibitem')) return `<div class="pk-bib">${el.innerHTML}</div>`;
    if (el.matches('.eq')) return `<div class="pk-eq">${el.innerHTML}</div>`;
    if (el.matches('.box')) {
      const first = el.querySelector('.u');
      const v = first && view(first.dataset.u);
      return `<div class="pk-h">${el.querySelector('.box-h').innerHTML}</div>${v ? `<div class="pk-t">${toksHtml(v.brief.slice(0, v.tn))}</div>` : ''}`;
    }
    if (el.matches('.hd')) {
      const c = D.chunks.find((x) => x.id === el.closest('section.chunk').id);
      const lad = c && ((trOf(c) && trOf(c).lad) || c.lad);
      return `<div class="pk-h">${el.innerHTML}</div>${lad ? `<div class="pk-t">${toksHtml(lad.take)}</div>` : ''}`;
    }
    const fig = el.matches('figure.fig') ? el : null;
    const uid = fig ? (fig.querySelector('figcaption.u') || {}).dataset?.u : el.dataset.u;
    if (!uid) return '';
    const v = view(uid), u = U[uid];
    const img = fig && fig.querySelector('img');
    const key = v.ev.map((s) => toksHtml(v.toks.slice(v.sents[s][0], v.sents[s][1]))).join(' … ');
    return `<div class="pk-where"><i class="pk-dot" style="background:var(--r-${u.lad.role})"></i>${esc(figLabel(uid) || secLabel(uid))}</div>` +
      (img ? `<img src="${img.getAttribute('src')}" alt="">` : '') +
      `<div class="pk-t">${toksHtml(v.brief.slice(0, v.tn))}</div><div class="pk-k">${key}</div>`;
  }
  function showPeek(trigger) {
    let el, html;
    if (trigger.matches('.ltx_note')) {                    // a footnote: its text, which the page keeps folded
      const c = trigger.querySelector('.ltx_note_content');
      html = c ? `<div class="pk-note">${c.innerHTML}</div>` : '';
    } else {
      el = trigger.matches('.lk') ? edgeTarget(trigger.dataset.tk, trigger.dataset.to) : resolve(decodeURIComponent(trigger.getAttribute('href').slice(1)));
      html = peekHtml(el);
    }
    if (!html) return;
    peek.innerHTML = html;
    texify(peek);
    peekFor = trigger;
    const r = trigger.getBoundingClientRect(), w = Math.min(400, innerWidth - 24);
    peek.style.width = `${w}px`;
    peek.style.left = `${Math.max(12, Math.min(innerWidth - w - 12, r.left))}px`;
    peek.classList.add('on');
    const ph = peek.offsetHeight;
    peek.style.top = `${r.bottom + 8 + ph > innerHeight - 90 ? Math.max(8, r.top - ph - 8) : r.bottom + 8}px`;
  }
  function hidePeek() { peekFor = null; peek.classList.remove('on'); }
  const PEEKABLE = 'a[href^="#"], .lk, .ltx_note';
  docEl.addEventListener('mouseover', (e) => {
    const trg = e.target.closest(PEEKABLE);
    if (trg && trg !== peekFor) { clearTimeout(peekTimer); peekTimer = setTimeout(() => showPeek(trg), 200); }
    const u = e.target.closest('.u');
    legendMark(u && level <= 2 ? U[u.dataset.u].lad.role : null);
    if (level <= 1 && !busy) {   // zoomed out, a paragraph's (or a section's) notes show on hover
      const tg = e.target.closest('.u, .hd, .stake'), key = tg && (tg.classList.contains('u') ? tg.dataset.u : secKeyOf(tg));
      const list = key && notesOf.get(key);
      if (list && tg !== peekFor) { clearTimeout(notesPeekTimer); notesPeekTimer = setTimeout(() => showNotesPeek(tg, list), 260); }
    }
    if (u && !busy) {
      clearTimeout(unfocusTimer);
      if (level === 0) { if (!u.classList.contains('focal')) { clearTimeout(mapTimer); mapTimer = setTimeout(() => mapFocus(u.dataset.u), 90); } }
    }
  });
  let mapTimer = null, unfocusTimer = null;
  docEl.addEventListener('mouseout', (e) => {
    const nt = e.target.closest('.u, .hd, .stake');
    if (nt && !nt.contains(e.relatedTarget)) { clearTimeout(notesPeekTimer); if (peekFor === nt) hidePeek(); }
    const trg = e.target.closest(PEEKABLE);
    if (trg && !trg.contains(e.relatedTarget)) { clearTimeout(peekTimer); hidePeek(); }
    const u = e.target.closest('.u');
    // leaving a chip for the gap before the next one should not flash the whole map
    if (u && !u.contains(e.relatedTarget)) {
      clearTimeout(mapTimer); clearTimeout(unfocusTimer); unfocusTimer = setTimeout(() => clearFocus(), 70);
      if (!(e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest('.u'))) legendMark(null);
    }
  });
  addEventListener('scroll', () => { if (peekFor) hidePeek(); }, { passive: true });


  // ── notes: highlights, notes and questions (docs/notes-format.md) ────
  // A selection becomes an anchor: its unit, the language and level it was made at, the tokens of the unit's full
  // text it covers (a written word maps back to the word it came from), the sentences (the same in every language),
  // and the quote. Highlights are marks on the pieces, so they zoom with the words; where the passage is not on
  // screen at a level, the paragraph carries a mark. Notes with text and questions sit in the margin.
  // The local server keeps them in papers/<id>/notes.json; a static page keeps them in this browser.
  let notes = [], noteAliases = {};
  const notesOf = new Map();   // uid -> its notes
  let keepMine = store.get('dr-keep-mine') === '1';   // "keep my highlights" (a viewer's preference: kept in this browser)
  const NOTES_KEY = `dr-notes:${D.meta.id}${D.saved ? ':' + D.saved : ''}`;
  let cloudOffline = false;
  const post = (path, body, signal) => fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal })
    .then(async (r) => {
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || r.status);
      if (Array.isArray(d.conflicts)) document.dispatchEvent(new CustomEvent('tf-conflicts', { detail: d.conflicts.length }));
      if (r.headers.has('X-TextFold-Save-State')) {
        cloudOffline = r.headers.get('X-TextFold-Online') === '0';
        document.dispatchEvent(new CustomEvent('tf-save-state', { detail: { local: r.headers.get('X-TextFold-Save-State') === 'local', online: !cloudOffline } }));
      }
      return d;
    });
  const noteId = () => {
    if (globalThis.crypto?.randomUUID) return 'n-' + crypto.randomUUID();
    const bytes = new Uint8Array(16);
    if (globalThis.crypto?.getRandomValues) crypto.getRandomValues(bytes);
    else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
    return 'n-' + [...bytes].map((x) => x.toString(16).padStart(2, '0')).join('');
  };
  function saveError(error) {
    let box = document.querySelector('.note-save-error');
    if (!box) { box = document.createElement('div'); box.className = 'note-save-error'; box.setAttribute('role', 'alert'); document.body.append(box); }
    box.textContent = `${T[ui].sync_save_failed} ${error.message}`;
    box.hidden = false;
  }
  const noteSaves = new Map(), acknowledged = new Map(), failedNoteFields = new Map();
  // a note's target: a paragraph (its id), a section ("§" and its id: its one-line summary, or its title), or the
  // whole paper ("¶": a conversation from the chat, with no passage)
  const whole = (a) => a.on === 'paper';
  const keyOf = (a) => (whole(a) ? '¶' : a.unit ? a.unit : `§${a.section}`);
  const shown = (a) => !!a && (whole(a) || (a.unit ? unitEl.has(a.unit) : secs.has(a.section)));
  function stakeMarks(cid) {   // the words of a section's summary that the reader marked, in the language being read
    const list = notesOf.get(`§${cid}`), out = new Map();   // token -> its note's shade class ('' the usual)
    for (const n of list || []) {
      const a = n.anchor;
      if (a.on === 'summary' && a.lang === lang && a.tokens) for (let i = a.tokens[0]; i <= a.tokens[1]; i++) out.set(i, shadeOf(n) !== 2 ? ` sh${shadeOf(n)}` : '');
    }
    return out.size ? out : null;
  }

  // the words of the written line a note was marked on (Key or Takeaway: anchor.shown), while the line reads as it
  // did, in the language it was marked in; null when it is not on one, or the line has been written again since
  function shownAt(a) {
    if (!a.shown || a.lang !== lang || !a.unit || !U[a.unit]) return null;
    const v = view(a.unit), [x, y] = a.shown.words || [];
    if (!(x >= 0 && y >= x && y < v.brief.length)) return null;
    return toksText(v.brief, x, y) === a.shown.exact ? [x, y] : null;
  }
  // what to mark in a unit, in the language being read. A passage marked at Full or Brief: its tokens, or in another
  // language its sentences (soft), lit at Full and Brief, and after a written line when the reader keeps their
  // highlights. One marked on a written line: those words of the line (line: place -> its colour), lit on the line
  // alone. One marked on a line before the line's words were kept (no anchor.shown): as it was lit then, its tokens
  // at Full and Brief and the written words that came from them (old: token -> its mark)
  function marksFor(uid) {
    const list = notesOf.get(uid);
    if (!list) return null;
    // (sh, lineSh: the shade of the note a token or a place of the line is marked by, the latest's)
    const v = view(uid), exact = new Set(), soft = new Set(), old = new Map(), line = new Map(), sh = new Map(), lineSh = new Map();
    for (const n of list) {
      const a = n.anchor, c = shadeOf(n);
      if (a.shown) {
        const at = shownAt(a);
        if (at) for (let j = at[0]; j <= at[1]; j++) { line.set(j, a.level === 1 ? 4 : 3); lineSh.set(j, c); }
        continue;
      }
      const before = (a.level ?? 4) <= 2;
      if (a.lang === lang && a.tokens) { for (let i = a.tokens[0]; i <= a.tokens[1]; i++) { exact.add(i); sh.set(i, c); if (before) old.set(i, 1); } }
      else if (a.sents) for (const k of a.sents) { const r = v.sents[k]; if (r) for (let i = r[0]; i < r[1]; i++) { if (!exact.has(i)) sh.set(i, c); soft.add(i); if (before && !old.has(i)) old.set(i, 2); } }
    }
    return exact.size || soft.size || line.size ? { exact, soft, old, line, sh, lineSh } : null;
  }
  // an equation's LaTeX: its MathML's (alttext, LaTeXML's and semantic.py's), or KaTeX's annotation; the rows of one
  // that has several (LaTeXML's eqnarray, align), gathered; and its number, (1), as \tag{1}, so a page that draws it
  // (Obsidian's) numbers it the same
  const texOf = (m) => m.getAttribute('alttext') || (m.querySelector('annotation[encoding="application/x-tex"]') || {}).textContent || '';
  function eqTex(e) {
    const rows = [...e.querySelectorAll('tr')].map((tr) => [...tr.querySelectorAll('math')].map(texOf).filter(Boolean).join(' ')).filter(Boolean);
    return rows.length > 1 ? `\\begin{gathered} ${rows.join(' \\\\ ')} \\end{gathered}` : rows[0] || '';
  }
  function eqTag(e) {
    const no = ((e.querySelector('.ltx_tag_equation') || {}).textContent || '').trim(), m = /^\((.+)\)$/.exec(no);
    return m ? ` \\tag{${m[1]}}` : no ? ` \\tag*{${no}}` : '';
  }
  // the equations of a paragraph's notes on them (anchor.eq): lit whole, in the note's shade, in any language (the
  // formula is the same in every one); a conversation leaves it as it was, as it leaves words
  function eqMarks(uid) {
    const list = (notesOf.get(uid) || []).filter((n) => n.anchor.eq && n.kind !== 'ask');
    for (const e of eqEls) {
      if (e._unit !== uid) continue;
      const n = list.filter((x) => x.anchor.eq === e.dataset.eq).pop();
      e.classList.toggle('hl', !!n);
      e.classList.toggle('sh1', !!n && shadeOf(n) === 1);
      e.classList.toggle('sh3', !!n && shadeOf(n) === 3);
    }
  }
  function indexNotes() {
    notesOf.clear();
    for (const n of notes) {
      if (!shown(n.anchor)) continue;   // a unit or section the paper no longer has: the note is kept, not shown
      const key = keyOf(n.anchor);
      if (!notesOf.has(key)) notesOf.set(key, []);
      notesOf.get(key).push(n);
    }
  }
  // the mark of a paragraph (or heading) with notes: a band in the colour of each level its notes were made at, from
  // Full and Brief to Takeaway; a conversation alone leaves it as it was
  function noteBar(el, key) {
    const tones = new Set((notesOf.get(key) || []).filter((n) => n.kind !== 'ask' && !n.anchor.eq).map(toneOf));   // (an equation's: lit itself)
    const inks = ['src', 'key', 'take'].filter((g) => tones.has(g)).map((g) => `var(--lv-${g}-ink)`);
    if (!inks.length) { el.style.removeProperty('--note-bar'); return; }
    const k = inks.length;
    el.style.setProperty('--note-bar', k === 1 ? inks[0] : `linear-gradient(${inks.map((c, i) => `${c} ${(i * 100 / k).toFixed(2)}% ${((i + 1) * 100 / k).toFixed(2)}%`).join(', ')})`);
  }
  // what a change of notes redraws: the units, their margin, the keyframes (which hold the marks)
  function notesChanged(uids) {
    for (const uid of new Set(uids)) {
      if (uid === '¶') { renderNoteCards(uid); continue; }   // the whole paper: the notes on it, by its head
      if (uid.startsWith('§')) {   // a section: its heading's mark, its summary's marks, its margins
        const sc = secs.get(uid.slice(1));
        if (!sc) continue;
        sc.hd.classList.toggle('has-notes', notesOf.has(uid));
        noteBar(sc.hd, uid);
        renderStake(sc.c, sc.st);
        renderNoteCards(uid);
        continue;
      }
      const el = unitEl.get(uid);
      if (!el) continue;
      el.classList.toggle('has-notes', notesOf.has(uid));
      noteBar(el, uid);
      if (!el._stale) renderUnit(uid, el._mode === 'tok');
      eqMarks(uid);
      renderNoteCards(uid);
    }
    capEpoch++;
    relayout();
    warmSoon();
    updateNotesBtn();
  }
  async function loadNotes() {
    try {
      if (D.app) {
        const data = await (await fetch(`/api/notes/${D.meta.id}`)).json(); notes = data.notes || []; noteAliases = data.aliases || {};
        if (data.conflicts?.length) document.dispatchEvent(new CustomEvent('tf-conflicts', { detail: data.conflicts.length }));
      } else notes = JSON.parse(store.get(NOTES_KEY) || 'null') || D.notes || [];
    } catch { notes = []; }
    indexNotes();
    // nothing to draw: leave the keyframes alone (prepared already, maybe; redrawing would throw them away)
    if (notesOf.size) notesChanged([...notesOf.keys()]); else updateNotesBtn();
    openFromHash();
  }
  let notesRefreshPending = false, notesRefreshTimer = 0, notesRefreshRunning = false, notesRefreshFailures = 0;
  function queueNotesRefresh(delay = 250) {
    if (!notesRefreshTimer && !notesRefreshRunning) notesRefreshTimer = setTimeout(refreshSyncedNotes, delay);
  }
  function notesRefreshBlocked() {
    // A remote refresh must not replace an animation's DOM or a draft under the cursor.
    return document.hidden || busy || stage || tr || holding || drag || rafId ||
      noteSaves.size || failedNoteFields.size || asking.size || popNote || !chat.hidden;
  }
  function noteAppearance(list) {
    return JSON.stringify(list, (key, value) => {
      if (key === '_base' || key === '_revision') return undefined;
      return value && typeof value === 'object' && !Array.isArray(value)
        ? Object.fromEntries(Object.keys(value).sort().map((k) => [k, value[k]])) : value;
    });
  }
  async function refreshSyncedNotes() {
    notesRefreshTimer = 0;
    if (!notesRefreshPending || notesRefreshRunning) return;
    if (notesRefreshBlocked()) { queueNotesRefresh(busy || stage || tr || holding || drag || rafId ? 250 : 2000); return; }
    notesRefreshRunning = true; notesRefreshPending = false;
    const requestedNotes = notes;
    try {
      const response = await fetch(`/api/notes/${D.meta.id}`);
      if (!response.ok) throw new Error('Could not refresh notes');
      const data = await response.json();
      notesRefreshFailures = 0;
      // Editing or another sync may have begun while HTTP was in flight. Read
      // again after it settles rather than publishing this older snapshot.
      if (notesRefreshBlocked() || notesRefreshPending || notes !== requestedNotes) { notesRefreshPending = true; return; }
      const before = new Map([...notesOf].map(([uid, list]) => [uid, noteAppearance(list)]));
      notes = data.notes || []; noteAliases = data.aliases || {}; acknowledged.clear();
      for (const n of notes) acknowledged.set(n.id, n);
      indexNotes();
      const affected = [...new Set([...before.keys(), ...notesOf.keys()])]
        .filter((uid) => before.get(uid) !== (notesOf.has(uid) ? noteAppearance(notesOf.get(uid)) : undefined));
      // Library changes and revision acknowledgements leave the prepared zoom
      // frames intact; only passages whose notes changed need new frames.
      if (affected.length) notesChanged(affected);
    } catch {   // Keep the last hint through a transient local HTTP failure; cached reading continues.
      notesRefreshPending = true;
      notesRefreshFailures = Math.min(6, notesRefreshFailures + 1);
    }
    finally {
      notesRefreshRunning = false;
      if (notesRefreshPending) queueNotesRefresh(notesRefreshFailures ? Math.min(30000, 1000 * 2 ** (notesRefreshFailures - 1)) : 250);
    }
  }
  document.addEventListener('tf-storage-synced', () => {
    notesRefreshPending = true;
    notesRefreshFailures = 0;
    clearTimeout(notesRefreshTimer); notesRefreshTimer = 0;
    queueNotesRefresh();
  });
  function saveNote(n) {   // shown at once; written in the background (the promise, for whoever must wait for it)
    const before = notes.find((x) => x.id === n.id);
    notes = notes.filter((x) => x.id !== n.id).concat([n]);
    indexNotes();
    notesChanged([keyOf(n.anchor), ...(before ? [keyOf(before.anchor)] : [])]);
    if (!D.app) { store.set(NOTES_KEY, JSON.stringify(notes)); return Promise.resolve(); }
    const ignored = new Set(['_base', '_revision', 'messages', 'branches']);
    const intent = Object.keys(n).filter((k) => !ignored.has(k) && JSON.stringify(n[k]) !== JSON.stringify(before?.[k]));
    const task = (noteSaves.get(n.id) || Promise.resolve()).catch(() => {}).then(async () => {
      const ack = acknowledged.get(n.id);
      // A rejected write leaves a visible draft. Its fields remain unconfirmed
      // even when the next input event repeats that exact text.
      const fields = new Set([...(failedNoteFields.get(n.id) || []), ...intent]);
      let payload = n;
      if (ack) {
        payload = { ...ack, ...Object.fromEntries([...fields].map((k) => [k, n[k]])) };
        if (n.messages) payload.messages = [...new Map([...(ack.messages || []), ...n.messages].map((m) => [m.id, m])).values()];
      }
      let data;
      try { data = await post(`/api/notes/${D.meta.id}`, { op: 'put', note: payload }); }
      catch (error) { failedNoteFields.set(n.id, fields); throw error; }
      failedNoteFields.delete(n.id);
      const saveErrorBox = document.querySelector('.note-save-error'); if (saveErrorBox) saveErrorBox.hidden = true;
      const id = data.aliases?.[n.id] || n.id, saved = data.notes.find((x) => x.id === id);
      if (saved) {
        acknowledged.set(n.id, saved);
        if (notes.find((x) => x.id === n.id) === n) {
          notes = notes.filter((x) => x.id !== n.id).concat([saved]); indexNotes(); notesChanged([keyOf(saved.anchor)]);
          if (popNote?.id === n.id) popNote = { ...saved, text: popNote.text };
        }
      }
      return saved;
    });
    noteSaves.set(n.id, task);
    task.then(() => { if (noteSaves.get(n.id) === task) noteSaves.delete(n.id); }, (error) => {
      if (noteSaves.get(n.id) === task) noteSaves.delete(n.id);
      saveError(error);
    });
    return task;
  }
  function deleteNote(n) {
    notes = notes.filter((x) => x.id !== n.id);
    indexNotes();
    notesChanged([keyOf(n.anchor)]);
    if (!D.app) { store.set(NOTES_KEY, JSON.stringify(notes)); return Promise.resolve(); }
    const task = (noteSaves.get(n.id) || Promise.resolve()).catch(() => {}).then(async () => {
      const data = await post(`/api/notes/${D.meta.id}`, { op: 'delete', id: n.id, base: acknowledged.get(n.id)?._base || n._base });
      failedNoteFields.delete(n.id); acknowledged.delete(n.id);
      return data;
    });
    noteSaves.set(n.id, task);
    task.then(() => { if (noteSaves.get(n.id) === task) noteSaves.delete(n.id); }, (error) => {
      if (noteSaves.get(n.id) === task) noteSaves.delete(n.id);
      saveError(error);
    });
    return task;
  }

  // ── a selection, as anchors (one per paragraph it touches) ──
  const tokPlain = (t) => (t.a ? (A[t.a].kind === 'math' ? `$${A[t.a].alt}$` : (A[t.a].text || '')) : t.x ? `$${t.x}$` : (t.t || ''));
  function toksText(toks, a, b, spaceAfter = false, spaceBefore = false) {   // tokens a..b as text, with their spaces
    let s = '';
    for (let i = a; i <= b; i++) { s += tokPlain(toks[i]); if (toks[i].s && (i < b || spaceAfter)) s += ' '; }
    return (spaceBefore && a > 0 && toks[a - 1] && toks[a - 1].s ? ' ' : '') + s;
  }
  function textOffset(root, node, off) { const r = document.createRange(); r.selectNodeContents(root); r.setEnd(node, off); return r.toString().length; }
  function segments(tx) {   // [from, to, piece] for every run of text in a drawn unit, in order
    const out = [], w = document.createTreeWalker(tx, NodeFilter.SHOW_TEXT);
    let at = 0;
    for (let t = w.nextNode(); t; t = w.nextNode()) {
      const piece = t.parentElement.closest('[data-id]');
      out.push([at, at + t.data.length, piece && tx.contains(piece) ? piece : null]);
      at += t.data.length;
    }
    return out;
  }
  function rangeAt(tx, from, to) {   // a DOM range over characters [from, to) of a unit's text
    const r = document.createRange(), w = document.createTreeWalker(tx, NodeFilter.SHOW_TEXT);
    let at = 0, started = false;
    for (let t = w.nextNode(); t; t = w.nextNode()) {
      const end = at + t.data.length;
      if (!started && from <= end) { r.setStart(t, from - at); started = true; }
      if (started && to <= end) { r.setEnd(t, to - at); return r; }
      at = end;
    }
    return started ? r : null;
  }
  function selectionAnchors() {
    const sel = getSelection();
    if (!sel.rangeCount || sel.isCollapsed) return null;
    const range = sel.getRangeAt(0), spans = [];
    for (const [uid, el] of unitEl) {
      const tx = el._tx;
      if (!range.intersectsNode(tx)) continue;
      const from = tx.contains(range.startContainer) ? textOffset(tx, range.startContainer, range.startOffset) : 0;
      const to = tx.contains(range.endContainer) ? textOffset(tx, range.endContainer, range.endOffset) : tx.textContent.length;
      if (to > from && tx.textContent.slice(from, to).trim()) spans.push([uid, from, to]);
    }
    // a paragraph drawn as plain text becomes words first (same text, so the same characters), and the selection is
    // laid back over it
    let redrawn = false;
    for (const [uid] of spans) if (unitEl.get(uid)._mode !== 'tok') { renderUnit(uid, true); redrawn = true; }
    if (redrawn && spans.length) {
      const [u0, f0] = spans[0], [u1, , t1] = spans[spans.length - 1];
      const a = rangeAt(unitEl.get(u0)._tx, f0, f0), b = rangeAt(unitEl.get(u1)._tx, t1, t1);
      if (a && b) { const r = document.createRange(); r.setStart(a.startContainer, a.startOffset); r.setEnd(b.endContainer, b.endOffset); sel.removeAllRanges(); sel.addRange(r); }
    }
    const anchors = spans.map(([uid, from, to]) => {
      const tx = unitEl.get(uid)._tx, text = tx.textContent;
      let lo = Infinity, hi = -Infinity, jlo = Infinity, jhi = -Infinity;
      for (const [a, b, piece] of segments(tx)) {
        if (!piece || b <= from || a >= to) continue;
        if (piece._i !== undefined) { lo = Math.min(lo, piece._i); hi = Math.max(hi, piece._i); }
        if (piece._j !== undefined) { jlo = Math.min(jlo, piece._j); jhi = Math.max(jhi, piece._j); }
      }
      const tokens = lo <= hi ? [lo, hi] : null, v = view(uid), L = levelOf(uid);
      // on a written line (Key, Takeaway): the words of the line selected, the model's too, to mark them as they
      // were selected; the author's words kept after the line, alone, are a passage of the full text
      const line = L >= 1 && L <= 2 && jlo <= jhi;
      const shown = line ? { words: [jlo, jhi], exact: toksText(v.brief, jlo, jhi) } : null;
      const sents = tokens ? v.sents.map((r, k) => [r, k]).filter(([r]) => r[1] > tokens[0] && r[0] <= tokens[1]).map(([, k]) => k) : null;
      // the quote in plain text, from the tokens (a formula as $LaTeX$), not from the page, which also holds each
      // formula's hidden source; words the model wrote itself are taken from the page as they are
      const quote = tokens
        ? { exact: toksText(v.toks, tokens[0], tokens[1]), prefix: toksText(v.toks, Math.max(0, tokens[0] - 12), tokens[0] - 1, true).slice(-40),
          suffix: toksText(v.toks, tokens[1] + 1, Math.min(v.toks.length - 1, tokens[1] + 12), false, true).slice(0, 40) }
        : { exact: text.slice(from, to).replace(/[\u200b\s]+/g, ' ').trim(), prefix: '', suffix: '' };
      return { unit: uid, section: chunkOf[uid] ? chunkOf[uid].id : null, lang, level: L >= 1 && L <= 2 && !line && tokens ? 4 : L, tokens, sents, quote, ...(shown ? { shown } : {}) };
    });
    // an equation set apart, selected: marked whole, as a note on the paragraph it goes with; its quote the formula,
    // in LaTeX (the model is asked about the formula, with that paragraph)
    for (const e of eqEls) {
      if (!e._unit || !range.intersectsNode(e) || !e.getClientRects().length) continue;
      anchors.push({ unit: e._unit, section: chunkOf[e._unit] ? chunkOf[e._unit].id : null, lang, level, tokens: null, sents: null,
        eq: e.dataset.eq, quote: { exact: `$${eqTex(e)}$`, prefix: '', suffix: '' } });
    }
    // a section's one-line summary (written by the model; its words are counted in the summary) or its title
    for (const [c, hd, st] of headEls) {
      for (const [el, on] of [[st, 'summary'], [hd, 'title']]) {
        if (!range.intersectsNode(el) || !el.getClientRects().length) continue;
        const text = el.textContent;
        const from = el.contains(range.startContainer) ? textOffset(el, range.startContainer, range.startOffset) : 0;
        const to = el.contains(range.endContainer) ? textOffset(el, range.endContainer, range.endOffset) : text.length;
        if (!(to > from && text.slice(from, to).trim())) continue;
        let lo = Infinity, hi = -Infinity;
        if (on === 'summary') for (const [a, b, piece] of segments(el)) if (piece && b > from && a < to && piece._i !== undefined) { lo = Math.min(lo, piece._i); hi = Math.max(hi, piece._i); }
        const tokens = lo <= hi ? [lo, hi] : null, lad = (trOf(c) && trOf(c).lad) || c.lad;
        const exact = tokens && lad ? toksText(lad.take, tokens[0], tokens[1]) : text.slice(from, to).replace(/[\u200b\s]+/g, ' ').trim();
        anchors.push({ unit: null, section: c.id, on, lang, level, tokens, sents: null, quote: { exact, prefix: '', suffix: '' } });
      }
    }
    return anchors.length ? anchors : null;
  }

  // ── the bar over a selection ──
  const selbar = h('div', 'selbar');
  selbar.hidden = true;
  document.body.append(selbar);
  let selAnchors = null;
  function showSelbar() {
    if (busy || stage) return;
    const anchors = selectionAnchors();
    if (!anchors) { hideSelbar(); return; }
    selAnchors = anchors;
    const t = T[ui];
    selbar.innerHTML = `<button type="button" data-act="hl">${esc(t.n_highlight)}</button>${swatches('hl', toneOf({ anchor: anchors[0] }), 0, lastShade)}` +
      `<button type="button" data-act="note">${esc(t.n_note)}</button>` +
      `<button type="button" data-act="ask">${esc(t.n_ask)}</button>`;
    selbar.hidden = false;
    const r = getSelection().getRangeAt(0).getBoundingClientRect(), w = selbar.offsetWidth;
    selbar.style.left = `${Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2))}px`;
    selbar.style.top = `${r.top > 70 ? r.top - 44 : r.bottom + 8}px`;
  }
  function hideSelbar() { selbar.hidden = true; selAnchors = null; }
  // a dot for each shade a highlight may have (data-s), in the colour of the level it is made at; in a note opened, its
  // own is on; over a selection, the one Highlight takes is ringed
  function swatches(act, tone, on, last) {
    const t = T[ui];
    const dot = (s) => `<button type="button" class="sw tn-${tone}${s !== 2 ? ` sh${s}` : ''}${s === on ? ' on' : ''}${s === last ? ' last' : ''}" data-act="${act}" data-s="${s}" title="${esc(t.n_shades[s - 1])}" aria-label="${esc(t.n_shades[s - 1])}"></button>`;
    return `<span class="sws" role="group" aria-label="${esc(t.n_shade)}">${SHADES.map(dot).join('')}</span>`;
  }
  selbar.addEventListener('mousedown', (e) => e.preventDefault());   // keep the selection while a button is pressed
  selbar.addEventListener('click', (e) => {
    // a dot marks the selection in its shade; Highlight, and a note (opened to write), in the shade last chosen
    const b = e.target.closest('button'), act = b && b.dataset.act, anchors = selAnchors;
    if (!act || !anchors) return;
    if (act === 'hl' && b.dataset.s) useShade(+b.dataset.s);
    const c = act !== 'ask' && lastShade !== 2 ? { shade: lastShade } : {};
    const made = anchors.map((a, k) => ({ id: noteId(), kind: k === 0 && act === 'note' ? 'note' : 'highlight', anchor: a, text: '', ask: null, ...c }));
    hideSelbar();
    getSelection().removeAllRanges();
    if (act === 'ask') {   // the question is written first; the note is kept once it is asked
      openNote({ ...made[0], kind: 'ask', thread: [] }, true);
      return;
    }
    for (const n of made) saveNote(n);
    if (act === 'note') openNote(made[0]);
  });
  docEl.addEventListener('mouseup', () => setTimeout(showSelbar, 0));
  docEl.addEventListener('keyup', (e) => { if (e.shiftKey) setTimeout(showSelbar, 0); });
  document.addEventListener('selectionchange', () => { if (!selbar.hidden && getSelection().isCollapsed) hideSelbar(); });
  addEventListener('scroll', () => { if (!selbar.hidden) hideSelbar(); }, { passive: true });

  // ── a note, opened: its text, or its question and answer ──
  const npop = h('div', 'npop');
  npop.hidden = true;
  document.body.append(npop);
  let popNote = null, popDraft = false, saveTimer = 0;
  // the model's Markdown, enough of it: paragraphs, lists, bold, italics, code, $math$, and links to the web pages an
  // agent with its own tools drew on ([title](https://…), or the address alone), which open beside the reader
  function mdLite(s) {
    const links = [], link = (href, text) => `\ue010${links.push(`<a href="${href}" target="_blank" rel="noopener noreferrer">${text}</a>`) - 1}\ue011`;
    const inline = (x) => esc(x).replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, t, u) => link(u, t)).replace(/https?:\/\/(?:(?!&lt;|&gt;|&quot;)[^\s()`])+/g, (u) => {
        const end = /[.,;:!?']+$/.exec(u), href = end ? u.slice(0, -end[0].length) : u;   // (a full stop after it is the sentence's)
        return link(href, href) + (end ? end[0] : '');
      })
      .replace(/\$([^$]+)\$/g, '<span class="texsrc">$1</span>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
      .replace(/(^|[^*])\*([^*]+)\*/g, '$1<i>$2</i>').replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\ue010(\d+)\ue011/g, (_, n) => links[+n]);
    return String(s || '').split(/\n{2,}/).map((block) => {   // within a block, runs of "- " lines are a list
      const out = [];
      for (const l of block.split('\n')) {
        const item = /^\s*[-*] /.test(l), last = out[out.length - 1];
        if (last && last.item === item) last.lines.push(l); else out.push({ item, lines: [l] });
      }
      return out.map((g) => (g.item ? `<ul>${g.lines.map((l) => `<li>${inline(l.replace(/^\s*[-*] /, ''))}</li>`).join('')}</ul>`
        : `<p>${g.lines.map(inline).join('<br>')}</p>`)).join('');
    }).join('');
  }
  const where = (uid) => { const c = chunkOf[uid]; return figLabel(uid) || (c ? (c.num ? (/\s/.test(c.num) ? c.num : `§${c.num}`) : (trOf(c) || c).title.replace(/\$/g, '')) : ''); };   // "Appendix D" says what it is
  // a cited paragraph: its section and its place in it (§1 ¶8), so two cites in one section are told apart
  const citeLabel = (uid) => { const c = chunkOf[uid], k = c ? c.units.indexOf(uid) : -1; return figLabel(uid) || `${where(uid) || uid}${c && c.units.length > 1 && k >= 0 ? ` ¶${k + 1}` : ''}`; };
  // the point of a cited paragraph, in the language being read: what a citation shows under the pointer
  const unitTake = (uid) => {
    const u = U[uid], z = u && (u.tr || {})[lang], lad = z ? z.lad : u && u.lad;
    return lad ? lad.brief.slice(0, lad.tn).map((tk) => tokPlain(tk) + (tk.s ? ' ' : '')).join('').trim() : '';
  };
  // an answer's citations, inline where the model put them: [[S3.p2.1]] a chip that leads to its paragraph (§3 ¶2),
  // [[?]] the model's own inference, [[!7]] a citation that named no paragraph (shown as such, not dropped)
  function citeChip(x) {
    const t = T[ui];
    if (x === '?') return `<span class="np-inf" title="${esc(t.n_infer_tip)}">${esc(t.n_infer)}</span>`;
    if (x[0] === '!' || !U[x]) return `<span class="np-cite bad" title="${esc(t.n_cite_bad)}">?</span>`;
    return `<button type="button" class="np-cite in" data-to="${esc(x)}" title="${esc(unitTake(x))}">${esc(citeLabel(x))}</button>`;
  }
  function answerHtml(s) {
    const marks = [], src = String(s || '').replace(/\[\[([^[\]]+)\]\]/g, (m, x) => { marks.push(x.trim()); return `\uE000${marks.length - 1}\uE001`; });
    return mdLite(src).replace(/\uE000(\d+)\uE001/g, (m, k) => citeChip(marks[+k]));
  }
  // the same as text, for the exports: (§3 ¶2)
  const answerText = (s) => String(s || '').replace(/\s*\[\[([^[\]]+)\]\]/g, (m, x) => {
    x = x.trim();
    return x === '?' ? ` (${T[ui].n_infer})` : x[0] === '!' || !U[x] ? '' : ` (${citeLabel(x)})`;
  });
  // questions to start from, or to go on with: a click puts one in the field, to be sent as it is or changed first
  const tryRow = (list, label) => `<div class="ch-try">${label ? `<span>${esc(label)}</span>` : ''}${list.map((q) => `<button type="button" class="np-ask" data-q="${esc(q)}">${esc(q)}</button>`).join('')}</div>`;
  function fillAsk(field, q) {
    if (!field) return;
    field.value = q;
    field.dispatchEvent(new Event('input', { bubbles: true }));   // (the send button, lit)
    field.focus();
    field.setSelectionRange(q.length, q.length);
  }
  function turnHtml(x, last, k, del) {
    const t = T[ui];
    const said = x.pending ? `<div class="np-wait">${esc(t.n_thinking)}</div>`
      : x.demo ? `<div class="np-demo">${esc(t.n_demo)} <a href="${REPO}" target="_blank" rel="noopener">${esc(t.n_demo_link)} →</a></div>`
      : x.error ? `<div class="np-err">${esc(t.n_failed)}: ${esc(x.error)}</div>`
      : `<div class="np-a">${answerHtml(x.a)}</div>` + (/\[\[/.test(x.a || '') ? '' : (x.cites && x.cites.length ? `<div class="np-cites">${x.cites.map((c) => `<button type="button" class="np-cite" data-to="${esc(c)}">${esc(citeLabel(c))}</button>`).join('')}</div>` : '')) +   // an answer from before inline citations: its list
        (last && x.next && x.next.length ? `<div class="np-next">${tryRow(x.next)}</div>` : '');
    const out = del && !x.pending ? `<button type="button" class="np-tdel" data-turn="${k}" title="${esc(t.n_turn_del)}" aria-label="${esc(t.n_turn_del)}">×</button>` : '';
    return `<div class="np-turn">${out}<div class="np-q">${esc(x.q)}</div>${said}</div>`;
  }
  // the field to ask in, with its send button; while a question is under way the button stops it, and the next question
  // can be written meanwhile, to be sent once the answer is in
  const SEND = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 15.5v-11M5.5 9 10 4.5 14.5 9" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  // a question looked up on the web by the agent, with its own web tools (agents.py): the switch beside a question, where
  // the questions go to an agent on this computer (app.js askAgent); off whenever a text is opened, and turned on for the
  // questions that need it (a work the text cites, a term it takes as known), the same for a passage's and the whole text's
  let askTools = false;
  const TOOLS = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M12.6 3.2a4 4 0 0 0-4.9 5.2l-4.4 4.4a1.6 1.6 0 0 0 2.3 2.3l4.4-4.4a4 4 0 0 0 5.2-4.9l-2.3 2.3-2.1-.5-.5-2.1z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  const toolsHere = () => !!(D.app && window.DRApp && window.DRApp.askAgent && window.DRApp.askAgent());
  function composer(cls, placeholder, value, waiting) {
    const t = T[ui], tools = toolsHere();
    return `<div class="np-compose${tools ? ' has-tools' : ''}"><textarea class="${cls}" rows="2" placeholder="${esc(placeholder)}">${esc(value)}</textarea>` +
      (tools && !waiting ? `<button type="button" class="np-tools${askTools ? ' on' : ''}" aria-pressed="${askTools}" title="${esc(t.n_tools_tip)}" aria-label="${esc(t.n_tools)}">${TOOLS}</button>` : '') +
      (waiting ? `<button type="button" class="np-stop" title="${esc(t.n_stop)}" aria-label="${esc(t.n_stop)}"><i></i></button>`
        : `<button type="button" class="np-send" title="${esc(t.n_send)}" aria-label="${esc(t.n_send)}"${value.trim() ? '' : ' disabled'}>${SEND}</button>`) + '</div>' +
      (tools ? `<p class="np-tools-on"${askTools ? '' : ' hidden'}>${esc(t.n_tools_on)}</p>` : '');
  }
  function flipTools(b) {   // (in place: what is being written, and where, stay)
    askTools = !askTools;
    for (const x of document.querySelectorAll('.np-tools')) { x.classList.toggle('on', askTools); x.setAttribute('aria-pressed', String(askTools)); }
    for (const x of document.querySelectorAll('.np-tools-on')) x.hidden = !askTools;
    const field = b.closest('.np-compose').querySelector('textarea');
    if (field) field.focus();
  }
  const whereA = (a) => { if (whole(a)) return T[ui].n_whole; if (a.unit) return where(a.unit); const sc = secs.get(a.section); return sc ? `${sc.c.num ? `§${sc.c.num} ` : ''}${(trOf(sc.c) || sc.c).title.replace(/\$/g, '')}` : ''; };
  // the element a note's target is drawn in: its paragraph, or its section's summary (its heading where that is hidden)
  function targetEl(a) {
    if (whole(a)) return paperHead;
    if (a.unit) return unitEl.get(a.unit);
    const sc = secs.get(a.section);
    return sc && (sc.st.getClientRects().length && a.on !== 'title' ? sc.st : sc.hd);
  }
  const askedAt = (a) => T[ui].n_asked_at.replace('{level}', T[ui].levels[a.level ?? 4]);
  // what a note marks, as the reader selected it: the words of the written line it was made on (the model's), else
  // the passage; where that line was, and whether it has been written again since (in the language it was made in)
  const passage = (a) => (a.shown ? a.shown.exact : (a.quote && a.quote.exact) || '');
  const lineAt = (a) => T[ui].n_line.replace('{level}', T[ui].levels[a.level]);
  const lineGone = (a) => !!a.shown && a.lang === lang && !!U[a.unit] && !shownAt(a);
  // a question note is a conversation about its passage: every turn asked there, and a field to ask the next
  const selectedBranches = new Map();
  function turnsOf(n) {
    if (!n.messages || !selectedBranches.has(n.id)) return n.thread || (n.ask ? [n.ask] : []);
    const by = new Map(n.messages.map((m) => [m.id, m]));
    const parents = new Set(n.messages.map((m) => m.parent));
    const heads = n.messages.map((m) => m.id).filter((id) => !parents.has(id)).sort();
    let id = selectedBranches.get(n.id);
    if (!by.has(id)) id = heads.at(-1);
    const path = [], seen = new Set();
    while (id && by.has(id) && !seen.has(id)) { seen.add(id); const m = by.get(id); path.unshift(m); id = m.parent; }
    const turns = [];
    for (const m of path) {
      if (m.role === 'user') turns.push({ q: m.text, a: '', message_id: m.id, pending: m.pending, error: m.error });
      else if (turns.length) Object.assign(turns[turns.length - 1], m, { a: m.text, message_id: m.id });
    }
    for (const turn of turns) if (turn.lang === 'en' && n.legacy_language?.messages?.includes(turn.message_id)) turn.lang = n.legacy_language.code;
    if (!asking.has(n.id)) for (const turn of turns) if (turn.pending) { turn.pending = false; turn.error = T[ui].sync_unanswered; }
    return turns;
  }
  function branchPicker(n) {
    if (!n?.messages) return '';
    const parents = new Set(n.messages.map((m) => m.parent));
    const heads = n.messages.filter((m) => !parents.has(m.id)).sort((a, b) => a.id.localeCompare(b.id));
    if (heads.length < 2) return '';
    const chosen = selectedBranches.get(n.id) || heads.at(-1).id;
    return `<label class="conversation-branches">${esc(T[ui].sync_branch)} <select class="conversation-branch">${heads.map((m, i) => `<option value="${esc(m.id)}"${chosen === m.id ? ' selected' : ''}>${i + 1} · ${esc(String(m.text || n.messages.find((q) => q.id === m.parent)?.text || '').slice(0, 45))}</option>`).join('')}</select></label>`;
  }
  function openNote(n, draft = false, back = '') {   // back: a question stopped, in the field again to be put right
    if (whole(n.anchor) && n.kind === 'ask') { openChat(n, back); return; }   // a conversation about the whole paper lives in the chat
    const was = popNote && popNote.id === n.id && npop.querySelector('.np-input'), typed = was ? was.value : '';   // (drawn again: what is written stays)
    popNote = n; popDraft = draft;
    const t = T[ui], a = n.anchor, ask = n.kind === 'ask';
    let body;
    if (ask) {
      const turns = turnsOf(n), waiting = turns.some((x) => x.pending), failed = turns.length && turns[turns.length - 1].error;
      body = branchPicker(n) + turns.map((x, k) => turnHtml(x, k === turns.length - 1, k, !waiting && !n.messages)).join('') +
        (turns.length && turns[turns.length - 1].model ? `<div class="np-model">${esc(turns[turns.length - 1].model)}</div>` : '') +
        (turns.length ? '' : tryRow(t.n_pstarters)) +
        composer('np-input', turns.some((x) => x.a) ? t.n_more : t.n_ask_ph, back || typed || (failed && !waiting ? turns[turns.length - 1].q : ''), waiting);
    } else {
      body = `<textarea class="np-text" rows="3" placeholder="${esc(t.n_note_ph)}">${esc(n.text || '')}</textarea>`;
    }
    const q = passage(a), at = a.shown ? lineAt(a) + (lineGone(a) ? ` · ${t.n_line_changed}` : '') : ask ? askedAt(a) : '';
    npop.innerHTML = `<div class="np-quote${a.shown ? ` ai l${a.level}` : ''}${ask ? ` tn-${toneOf(n)}` : toneCls(n)}"><span class="np-where">${esc(whereA(a))}</span> ${texLite(clipTex(q, 160))}` +
      `${at ? `<span class="np-at">${esc(at)}</span>` : ''}</div>${body}` +
      (draft ? '' : `<div class="np-acts">${!ask && !whole(a) ? `${swatches('shade', toneOf(n), shadeOf(n))}<button type="button" data-act="ask">${esc(t.n_ask)}</button>` : ''}` +
        `<button type="button" data-act="link">${esc(t.n_copy)}</button><button type="button" data-act="del">${esc(t.n_delete)}</button></div>`);
    texify(npop);
    npop.hidden = false;
    placePop();
    npop.scrollTop = npop.scrollHeight;   // the latest turn, and the field to go on
    const field = npop.querySelector('.np-input, .np-text');
    if (field) { field.focus(); field.setSelectionRange(field.value.length, field.value.length); }
  }
  function placePop() {
    if (!popNote) return;
    const el = targetEl(popNote.anchor), card = docEl.querySelector(`.ncard[data-note="${CSS.escape(popNote.id)}"]`);
    if (!el) return;
    const marks = [...el.querySelectorAll('.hl')];
    const r = (card && visibleRect(card)) || (marks.length && marks[marks.length - 1].getBoundingClientRect()) || el.getBoundingClientRect();
    const w = Math.min(380, innerWidth - 24);
    npop.style.width = `${w}px`;
    npop.style.left = `${Math.max(12, Math.min(innerWidth - w - 12, r.left))}px`;
    const ph = npop.offsetHeight;
    npop.style.top = `${r.bottom + 8 + ph > innerHeight - 90 ? Math.max(8, r.top - ph - 8) : r.bottom + 8}px`;
  }
  function closeNote() {
    if (!popNote) return;
    const field = npop.querySelector('.np-text');
    if (field && !popDraft && field.value !== (popNote.text || '')) { clearTimeout(saveTimer); saveNote({ ...popNote, text: field.value }); }
    popNote = null;
    if (npop.contains(document.activeElement)) document.activeElement.blur();   // the keys go back to the page
    npop.hidden = true;
  }
  npop.addEventListener('input', (e) => {
    if (!e.target.matches('.np-text') || !popNote) return;
    clearTimeout(saveTimer);
    const n = popNote, v = e.target.value;
    saveTimer = setTimeout(() => { if (popNote === n) popNote = { ...n, text: v }; saveNote({ ...n, text: v }); }, 700);
  });
  npop.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); closeNote(); return; }
    if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;   // Shift+Enter: a new line, as in the chat
    if (e.target.matches('.np-input')) { e.preventDefault(); sendPop(); }
    // a note is kept and closed, as with Escape; not on a phone or a tablet, whose keyboard has no Shift+Enter: Enter
    // writes a new line there, and a tap outside keeps the note
    else if (e.target.matches('.np-text') && !matchMedia('(pointer: coarse)').matches) { e.preventDefault(); closeNote(); }
  });
  npop.addEventListener('change', (e) => {
    if (e.target.matches('.conversation-branch') && popNote) { selectedBranches.set(popNote.id, e.target.value); openNote(popNote); }
  });
  npop.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b || !popNote) return;
    const n = popNote;
    if (b.classList.contains('np-ask')) { fillAsk(npop.querySelector('.np-input'), b.dataset.q); return; }
    if (b.classList.contains('np-send')) { sendPop(); return; }
    if (b.classList.contains('np-tools')) { flipTools(b); return; }
    if (b.classList.contains('np-stop')) { stopAsk(n); return; }
    if (b.classList.contains('np-tdel')) { deleteTurn(n, +b.dataset.turn); return; }
    if (b.classList.contains('np-cite') && b.dataset.to) { closeNote(); showEvidence(b.dataset.to, targetEl(n.anchor)); return; }
    if (b.dataset.act === 'shade') {   // another shade (what is being written goes with it)
      const sh = +b.dataset.s;
      if (sh === shadeOf(n)) return;
      const field = npop.querySelector('.np-text'), m = { ...n, text: field ? field.value : n.text };
      if (sh === 2) delete m.shade; else m.shade = sh;
      useShade(sh);
      clearTimeout(saveTimer);
      popNote = m;
      saveNote(m);
      for (const x of npop.querySelectorAll('.sw')) x.classList.toggle('on', +x.dataset.s === sh);
      const qe = npop.querySelector('.np-quote');
      qe.classList.toggle('sh1', sh === 1); qe.classList.toggle('sh3', sh === 3);
      return;
    }
    if (b.dataset.act === 'del') { closeNote(); deleteNote(n); }
    else if (b.dataset.act === 'ask') { closeNote(); openNote({ id: noteId(), kind: 'ask', anchor: n.anchor, text: '', thread: [] }, true); }
    else if (b.dataset.act === 'link') {
      navigator.clipboard.writeText(noteLink(n)).then(() => { b.textContent = T[ui].n_copied; }, () => {});
    }
  });
  addEventListener('mousedown', (e) => { if (popNote && !npop.contains(e.target) && !e.target.closest('.ncard, .hl, .hlsp')) closeNote(); });
  let notesPeekTimer = 0;
  const secKeyOf = (el) => { const sc = [...secs.values()].find((x) => x.hd === el || x.st === el); return sc ? `§${sc.c.id}` : null; };
  const clip = (x, n) => (x.length > n ? x.slice(0, n - 1) + '…' : x);
  function showNotesPeek(el, list) {
    peek.innerHTML = list.map((n) => {
      if (n.kind === 'ask') {
        const turns = turnsOf(n), said = [...turns].reverse().find((x) => x.a);
        return `<div class="pk-n ask"><b>${esc((turns[0] || {}).q || '')}</b>${said ? ` <span>${mdLite(said.takeaway).replace(/<\/?p>/g, '')}</span>` : ''}</div>`;
      }
      return `<div class="pk-n${toneCls(n)}"><span class="pk-q${n.anchor.shown ? ' ai' : ''}">“${esc(clip(passage(n.anchor), 90))}”</span>${n.text ? ` ${esc(clip(n.text, 160))}` : ''}</div>`;
    }).join('');
    texify(peek);
    peekFor = el;
    const r = el.getBoundingClientRect(), w = Math.min(360, innerWidth - 24);
    peek.style.width = `${w}px`;
    peek.style.left = `${Math.max(12, Math.min(innerWidth - w - 12, r.left))}px`;
    peek.classList.add('on');
    const ph = peek.offsetHeight;
    peek.style.top = `${r.bottom + 8 + ph > innerHeight - 90 ? Math.max(8, r.top - ph - 8) : r.bottom + 8}px`;
  }
  // the evidence of an answer: the cited paragraph, brought on screen and opened to the authors' full text in place
  // (where the paper is zoomed out), so a summary leads back to the words it stands for; Back returns
  function showEvidence(uid, from) {
    const el = unitEl.get(uid);
    if (!el) return;
    go(unitBox(uid), from);
    if (level < 4 && !localLevel.has(uid)) setTimeout(() => { if (!busy && !localLevel.has(uid)) toggleUnit(uid); }, 480);
  }
  addEventListener('scroll', () => { if (popNote) placePop(); }, { passive: true });
  // typing into a note scrolled out of sight brings its passage back (and the note with it, on the scroll above):
  // the keys never go into a box no one sees, and scrolling away to read while writing stays possible
  npop.addEventListener('beforeinput', () => {
    const r = npop.getBoundingClientRect(), el = popNote && targetEl(popNote.anchor);
    if (el && (r.bottom < 0 || r.top > innerHeight)) el.scrollIntoView({ block: 'center' });
  });

  // a question goes to the model with the turns before it; the note keeps the question at once, the answer fills it in.
  // Stopped (its words wrong, say), the turns before it are as they were and the question is in the field again.
  const asking = new Map();   // a note's id -> the question under way: {req, ctl, q, before}
  async function askLocalNote(n, q) {
    if (!D.app) {   // a page made to share has no model behind it: the question stays on screen, with how to ask for real
      const shown = { ...n, kind: 'ask', thread: [...turnsOf(n).filter((x) => x.a || x.demo), { q, demo: true }] };
      if (whole(n.anchor)) { chatNote = shown; openChat(shown); } else openNote(shown, true);
      return;
    }
    if (asking.has(n.id)) return;
    const done = turnsOf(n).filter((x) => x.a), qlang = (turnsOf(n).find((x) => x.lang) || {}).lang || lang;
    const note = { ...n, kind: 'ask', thread: [...done, { q, a: '', pending: true, lang: qlang }] };
    delete note.ask;
    const mine = { req: noteId(), ctl: new AbortController(), q, before: notes.find((x) => x.id === n.id) || null };
    asking.set(n.id, mine);
    try { await saveNote(note); } catch { /* shown already; the answer below will report it */ }
    if (popNote && popNote.id === n.id) openNote(note);
    if (chatNote && chatNote.id === n.id) openChat(note);
    let answered;
    try { answered = await post('/api/ask', { paper: D.meta.id, id: note.id, question: q, ui: readerLang, req: mine.req, tools: askTools && toolsHere() }, mine.ctl.signal); }
    catch (err) {
      if (mine.ctl.signal.aborted || err.message === 'stopped') return;   // (stopAsk put it all back)
      answered = { ...note, thread: [...done, { q, a: '', lang: qlang, error: String(err.message || err).slice(0, 200) }] };
    } finally { if (asking.get(n.id) === mine) asking.delete(n.id); }
    notes = notes.filter((x) => x.id !== answered.id).concat([answered]);
    acknowledged.set(answered.id, answered);
    indexNotes();
    notesChanged([keyOf(answered.anchor)]);
    if (popNote && popNote.id === answered.id) openNote(answered);
    if (chatNote && chatNote.id === answered.id) openChat(answered);
  }

  function stopLocalAsk(n) {
    const a = asking.get(n.id);
    if (!a) return;
    asking.delete(n.id);
    a.ctl.abort();
    post('/api/ask/stop', { req: a.req }).catch(() => {});
    const field = whole(n.anchor) ? chat.querySelector('.ch-input') : npop.querySelector('.np-input');
    const back = field && field.value.trim() ? field.value : a.q;
    // the conversation as it was before the question; a first question, stopped, leaves no note (only the field again)
    const was = a.before && turnsOf(a.before).filter((x) => x.a).length ? { ...a.before, thread: turnsOf(a.before).filter((x) => x.a) } : null;
    if (was) { delete was.ask; saveNote(was); } else deleteNote(n);
    const fresh = was || { id: noteId(), kind: 'ask', anchor: n.anchor, text: '', thread: [] };
    if (whole(n.anchor)) { chatNote = was; openChat(was, back); } else openNote(fresh, !was, back);
  }
  async function askNote(n, q) {
    if (!D.cloudSync) return askLocalNote(n, q);
    if (!D.app) {   // a page made to share has no model behind it: the question stays on screen, with how to ask for real
      const shown = { ...n, kind: 'ask', thread: [...turnsOf(n).filter((x) => x.a || x.demo), { q, demo: true }] };
      delete shown.messages;
      if (whole(n.anchor)) { chatNote = shown; openChat(shown); } else openNote(shown, true);
      return;
    }
    if (asking.has(n.id)) return;
    const mine = { req: noteId(), ctl: new AbortController(), q, before: notes.find((x) => x.id === n.id) || null };
    const done = turnsOf(n).filter((x) => x.a), qlang = (done.find((x) => x.lang) || {}).lang || lang;
    const parent = done.at(-1)?.message_id || null;
    const nodes = n.messages || [];
    const note = { ...n, kind: 'ask', thread: [...done, { q, a: '', pending: true, lang: qlang }], messages: [...nodes,
      { id: mine.req + '-q', parent, role: 'user', text: q, pending: true },
      { id: mine.req + '-a', parent: mine.req + '-q', role: 'assistant', text: '', pending: true, lang: qlang }] };
    delete note.ask;
    selectedBranches.set(n.id, mine.req + '-a');
    asking.set(n.id, mine);
    try { await saveNote(note); } catch { asking.delete(n.id); return; }
    if (popNote && popNote.id === n.id) openNote(note);
    if (chatNote && chatNote.id === n.id) openChat(note);
    let answered;
    try {
      if (cloudOffline) throw new Error(T[ui].sync_unanswered);
      answered = await post('/api/ask', { paper: D.meta.id, id: note.id, question: q, ui: readerLang, req: mine.req, parent, tools: askTools && toolsHere() }, mine.ctl.signal);
    }
    catch (err) {
      if (mine.ctl.signal.aborted || err.message === 'stopped') return;   // (stopAsk put it all back)
      answered = { ...note, thread: note.thread.map((turn, i) => i === note.thread.length - 1 ? { ...turn, pending: false, error: String(err.message || err).slice(0, 200) } : turn), messages: note.messages.map((m) => m.id.startsWith(mine.req + '-') ? { ...m, pending: false, error: String(err.message || err).slice(0, 200) } : m) };
      try { answered = await saveNote(answered) || answered; } catch { /* saveNote already reports the failed local write */ }
    } finally { if (asking.get(n.id) === mine) asking.delete(n.id); }
    notes = notes.filter((x) => x.id !== answered.id).concat([answered]);
    acknowledged.set(answered.id, answered);
    indexNotes();
    notesChanged([keyOf(answered.anchor)]);
    if (popNote && popNote.id === answered.id) openNote(answered);
    if (chatNote && chatNote.id === answered.id) openChat(answered);
  }

  async function stopAsk(n) {
    if (!D.cloudSync) return stopLocalAsk(n);
    const a = asking.get(n.id);
    if (!a) return;
    asking.delete(n.id);
    a.ctl.abort();
    const field = whole(n.anchor) ? chat.querySelector('.ch-input') : npop.querySelector('.np-input');
    const back = field && field.value.trim() ? field.value : a.q;
    // the conversation as it was before the question; a first question, stopped, leaves no note (only the field again)
    let was;
    try {
      const data = await post('/api/ask/stop', { req: a.req, paper: D.meta.id, id: n.id });
      was = data.notes?.find((x) => x.id === n.id);
      if (was) { notes = notes.filter((x) => x.id !== n.id).concat([was]); acknowledged.set(n.id, was); indexNotes(); notesChanged([keyOf(n.anchor)]); }
    } catch (error) { alert(error.message); return; }
    const fresh = was || { id: noteId(), kind: 'ask', anchor: n.anchor, text: '', messages: [] };
    if (whole(n.anchor)) { chatNote = was; openChat(was, back); } else openNote(fresh, !was, back);
  }
  // a turn out of its conversation (not while a question is under way there): the note without it, or none
  function deleteTurn(n, k) {
    if (asking.has(n.id) || !confirm(T[ui].n_turn_del_q)) return;
    const thread = turnsOf(n).filter((x, i) => i !== k && !x.pending);
    if (!thread.length) {
      deleteNote(n);
      if (whole(n.anchor)) { chatNote = null; openChat(null); } else closeNote();
      return;
    }
    const note = { ...n, kind: 'ask', thread };
    delete note.ask;
    saveNote(note);
    if (whole(n.anchor)) { chatNote = note; openChat(note); } else openNote(note);
  }

  // ── the chat: questions about the whole paper. Each conversation is a note on the paper ({on: 'paper'}), so it
  //    is listed, exported and kept like any other; the chat shows the latest one, or a new one ──
  const chatBtn = h('button', 'chatbtn'), chat = h('div', 'chat');
  chatBtn.type = 'button';
  chat.hidden = true;
  let chatNote = null;
  document.body.append(chatBtn, chat);
  function chatLabel() { chatBtn.innerHTML = `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 4.5h12a1.5 1.5 0 0 1 1.5 1.5v6.5A1.5 1.5 0 0 1 16 14H9l-3.5 3v-3H4a1.5 1.5 0 0 1-1.5-1.5V6A1.5 1.5 0 0 1 4 4.5z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>${esc(T[ui].n_chat)}`; }
  chatLabel();
  const latestChat = () => notes.filter((x) => x.anchor && whole(x.anchor)).sort((x, y) => (y.updated || '').localeCompare(x.updated || ''))[0] || null;
  function openChat(n, back = '') {
    const t = T[ui], keep = !chat.hidden && chat.querySelector('.ch-input');
    const draft = back || (keep ? keep.value : '');
    chatNote = n;
    const turns = n ? turnsOf(n) : [], waiting = turns.some((x) => x.pending);
    chat.innerHTML = `<div class="ch-head"><b>${esc(t.n_chat)}</b><button type="button" class="ch-new">${esc(t.n_chat_new)}</button><button type="button" class="ch-x" aria-label="×">×</button></div>` +
      branchPicker(n) + `<div class="ch-body">${turns.length ? turns.map((x, k) => turnHtml(x, k === turns.length - 1, k, !waiting && !n?.messages)).join('')
        : `<div class="ch-hint">${esc(t.n_chat_hint)}</div>` + tryRow(t.n_starters, t.n_try)}</div>` +
      `<div class="ch-foot">${composer('ch-input', turns.some((x) => x.a) ? t.n_more : t.n_chat_ph, draft, waiting)}</div>`;
    texify(chat);
    chat.hidden = false;
    chatBtn.classList.add('on');
    const body = chat.querySelector('.ch-body'), field = chat.querySelector('.ch-input');
    body.scrollTop = body.scrollHeight;
    field.focus();
    field.setSelectionRange(field.value.length, field.value.length);
  }
  function closeChat() { chat.hidden = true; chatBtn.classList.remove('on'); if (chat.contains(document.activeElement)) document.activeElement.blur(); }
  chat.addEventListener('change', (e) => {
    if (e.target.matches('.conversation-branch') && chatNote) { selectedBranches.set(chatNote.id, e.target.value); openChat(chatNote); }
  });
  chatBtn.addEventListener('click', () => { if (!chat.hidden) closeChat(); else openChat(chatNote && notes.some((x) => x.id === chatNote.id) ? notes.find((x) => x.id === chatNote.id) : latestChat()); });
  chat.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.classList.contains('ch-x')) closeChat();
    else if (b.classList.contains('ch-new')) openChat(null);
    else if (b.classList.contains('np-ask')) fillAsk(chat.querySelector('.ch-input'), b.dataset.q);
    else if (b.classList.contains('np-cite') && b.dataset.to) showEvidence(b.dataset.to, null);
    else if (b.classList.contains('np-send')) chatSend(chat.querySelector('.ch-input'));
    else if (b.classList.contains('np-tools')) flipTools(b);
    else if (b.classList.contains('np-stop') && chatNote) stopAsk(chatNote);
    else if (b.classList.contains('np-tdel') && chatNote) deleteTurn(chatNote, +b.dataset.turn);
  });
  function chatSend(field) {
    const q = field.value.trim();
    if (!q || (chatNote && asking.has(chatNote.id))) return;
    field.value = '';
    const n = chatNote || { id: noteId(), kind: 'ask', text: '', thread: [],
      anchor: { unit: null, section: null, on: 'paper', lang, level, tokens: null, sents: null, quote: { exact: '' } } };
    chatNote = n;
    askNote(n, q);
  }
  chat.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); closeChat(); return; }
    if (e.key !== 'Enter' || e.shiftKey || e.isComposing || !e.target.matches('.ch-input')) return;
    e.preventDefault();
    chatSend(e.target);
  });
  // the field's words sent (not while the answer before is still being written: they wait in the field)
  function sendPop() {
    const f = npop.querySelector('.np-input'), q = f && f.value.trim();
    if (!q || !popNote || asking.has(popNote.id)) return;
    f.value = '';
    askNote(popNote, q);
  }
  // the send button, while there are words to send
  for (const box of [npop, chat]) box.addEventListener('input', (e) => {
    if (!e.target.matches('.np-input, .ch-input')) return;
    const b = e.target.parentElement.querySelector('.np-send');
    if (b) b.disabled = !e.target.value.trim();
  });

  // a citation under the pointer: its paragraph beats in the page, as the ends of an argument link do
  let litUnit = null;
  function lightCite(e) {
    const c = e.target.closest && e.target.closest('.np-cite[data-to]'), el = c ? unitEl.get(c.dataset.to) || null : null;
    if (el === litUnit) return;
    if (litUnit) litUnit.classList.remove('cite-lit');
    litUnit = el;
    if (el) el.classList.add('cite-lit');
  }
  for (const box of [chat, npop]) {
    box.addEventListener('mouseover', lightCite);
    box.addEventListener('mouseleave', () => { if (litUnit) { litUnit.classList.remove('cite-lit'); litUnit = null; } });
  }

  // ── the margin: notes with text, questions ──
  function renderNoteCards(key) {
    const list = notesOf.get(key) || [], t = T[ui], host = hostOf(key);
    if (!host || !host.el) return;
    // (the conversations about the whole paper live in the chat: its head carries the reader's own notes alone)
    const mine = list.filter((n) => n.kind !== 'ask' && n.text && n.text.trim()), asks = key === '¶' ? [] : list.filter((n) => n.kind === 'ask');
    const card = (n) => {
      const c = h('button', `ncard${n.kind === 'ask' ? ' ask' : toneCls(n)}`);
      c.type = 'button';
      c.draggable = true;   // (into another app, with a link back: the drags, below)
      c.dataset.note = n.id;
      const turns = turnsOf(n), last = turns[turns.length - 1] || {}, said = [...turns].reverse().find((x) => x.a);
      c.innerHTML = n.kind === 'ask'
        ? `<span class="nc-q">${esc((turns[0] || {}).q || '')}${turns.length > 1 ? ` <em class="nc-n">${turns.length}</em>` : ''}</span>` +
          `<span class="nc-t">${last.pending ? esc(t.n_thinking) : last.error ? esc(t.n_failed) : said ? mdLite(said.takeaway).replace(/<\/?p>/g, '') : ''}</span>` +
          `<span class="nc-at">${esc(askedAt(n.anchor))}</span>`
        : `<span class="nc-t">${esc(n.text).replace(/\n/g, '<br>')}</span>`;
      return c;
    };
    const right = asks.length ? ensureSide(key) : host.el.querySelector(':scope > .side');
    if (right) right.querySelector(':scope > .side-notes').replaceChildren(...asks.map(card));
    const left = mine.length ? ensureSide(key, true) : host.el.querySelector(':scope > .lside');
    if (left) left.replaceChildren(...mine.map(card));
    for (const m of [right, left]) if (m) texify(m);
  }
  // the note under a mark: the one whose passage holds the clicked word (the latest, when several do)
  function noteAt(mark) {
    if (mark.classList.contains('eq')) return (notesOf.get(mark._unit) || []).filter((n) => n.anchor.eq === mark.dataset.eq).pop() || null;
    let piece = mark.closest('[data-id]');
    if (!piece && mark.classList.contains('hlsp')) piece = mark.previousElementSibling;
    const i = piece ? piece._i : undefined, st = mark.closest('.stake');
    if (st) {   // a mark on a section's summary
      const sc = [...secs.values()].find((x) => x.st === st), list = sc && notesOf.get(`§${sc.c.id}`);
      return list ? list.filter((n) => n.anchor.tokens && i >= n.anchor.tokens[0] && i <= n.anchor.tokens[1]).pop() || list[list.length - 1] : null;
    }
    const u = mark.closest('.u'), list = u && notesOf.get(u.dataset.u);
    if (!list) return null;
    const v = view(u.dataset.u), j = piece ? piece._j : undefined;
    const holds = (n) => {
      const a = n.anchor;
      if (a.shown) { const at = shownAt(a); return !!at && j !== undefined && j >= at[0] && j <= at[1]; }   // a word of the line it marks
      if (i === undefined) return true;
      if (a.lang === lang && a.tokens) return i >= a.tokens[0] && i <= a.tokens[1];
      return (a.sents || []).some((k) => v.sents[k] && i >= v.sents[k][0] && i < v.sents[k][1]);
    };
    return list.filter(holds).sort((x, y) => (y.updated || '').localeCompare(x.updated || ''))[0] || list[list.length - 1];
  }

  // ── links to a note (#n=), a paragraph (#u=) or a section (#s=), and from the address to one ──
  function noteLink(n) {
    const base = D.app ? `${location.origin}/p/${D.meta.id}?lang=${encodeURIComponent(lang)}` : location.href.split('#')[0];
    return `${base}#n=${n.id}`;
  }
  // a link to a paragraph or section no longer in the text (ids.py: D.gone, by their ids): a section whose paragraphs
  // went to another opens that one; a paragraph, the section it was in, else the top of the text, and the page says so
  const GONE = D.gone || { units: {}, sections: {} };
  let linkMsg = null;
  function sayLink(text) {
    if (!linkMsg) { linkMsg = h('div', 'linkmsg'); linkMsg.setAttribute('role', 'status'); document.body.append(linkMsg); }
    linkMsg.textContent = text;
    linkMsg.classList.add('on');
    clearTimeout(linkMsg._t);
    linkMsg._t = setTimeout(() => linkMsg.classList.remove('on'), 6000);
  }
  function openFromHash() {
    const m = /[#&]n=([\w-]+)/.exec(location.hash);
    let identity = m?.[1], seen = new Set();
    while (noteAliases[identity] && !seen.has(identity)) { seen.add(identity); identity = noteAliases[identity]; }
    const n = notes.find((x) => x.id === identity);
    const arg = (k) => { const x = new RegExp(`[#&]${k}=([^&]+)`).exec(location.hash); return x ? decodeURIComponent(x[1]) : null; };
    let u = n ? n.anchor.unit : arg('u'), s = !n && !u ? arg('s') : null, lost = false;
    if (u && !n && !unitEl.has(u) && unitEl.has(ALIAS[u])) u = ALIAS[u];
    if (u && !n && !unitEl.has(u)) { s = (GONE.units[u] || {}).section || arg('s'); u = null; lost = true; }
    if (s && !secs.get(s)) { const now = (GONE.sections[s] || {}).now; s = now && secs.get(now) ? now : null; lost = lost || !s; }
    const sc = s && secs.get(s);
    if (lost) sayLink(sc ? T[ui].link_gone : T[ui].link_gone_top);
    // an equation (#eq=, with the paragraph it goes with as u=): itself, where the level shows it, else that paragraph
    const eqId = !n && arg('eq'), eqEl = eqId ? eqEls.find((x) => x.dataset.eq === eqId && x.getClientRects().length) : null;
    const el = eqEl || (n && !n.anchor.unit ? targetEl(n.anchor) : u ? unitEl.get(u) : sc ? (sc.hd.hidden ? sc.sec : sc.hd) : null);
    if (!el) return;
    if (el._stale) renderUnit(el.dataset.u, true);
    el.scrollIntoView({ block: 'center' });
    // all that the link names, flashed together: a run of paragraphs (#u=<first>&to=<last>), a section with the
    // subsections under it (#s=)
    const to = !n && u && arg('to');
    const all = eqEl ? [eqEl] : to ? unitsBetween(u, to).map((x) => unitEl.get(x)) : sc ? sectionWith(sc.c).map((x) => secs.get(x.id).sec) : [el];
    for (const x of all) { if (x._stale) renderUnit(x.dataset.u, true); flash(x); }
    const tm = n && /[#&]t=(\d{1,3})(?:&|$)/.exec(location.hash);
    if (n) setTimeout(() => { openNote(n); if (tm) showTurn(n, +tm[1]); }, 300);
  }
  // the paragraphs from one to another, in reading order (either way round; the first alone when the other is not here)
  function unitsBetween(a, b) {
    const i = order.get(a), j = order.has(b) ? order.get(b) : i;
    return i === undefined ? [] : IDS.slice(Math.min(i, j), Math.max(i, j) + 1);
  }
  // a section and the subsections under it, in order (the page keeps them side by side: a deeper one follows its own)
  function sectionWith(c) {
    const out = [c];
    for (let j = D.chunks.indexOf(c) + 1; j < D.chunks.length && (D.chunks[j].level || 2) > (c.level || 2); j++) out.push(D.chunks[j]);
    return out;
  }
  // one turn of a conversation, in the chat or in the note's window, brought into view (#n=<id>&t=<turn>)
  function showTurn(n, k) {
    const box = !chat.hidden && chatNote === n ? chat.querySelector('.ch-body') : popNote === n ? npop : null;
    const turn = box && box.querySelectorAll(':scope > .np-turn')[k];
    if (!turn) return;
    box.scrollTop += turn.getBoundingClientRect().top - box.getBoundingClientRect().top - 8;
    flash(turn);
  }

  // ── dragged into another app (a notes app, an editor): what was dragged, as it reads, with a textfold:// link back to
  //    exactly there (docs/drag-format.md). A paragraph, a heading or a section's summary shows a handle while the
  //    pointer is on it, so its text stays selectable, and so does each message of a conversation (the chat, a note's
  //    window); on the Topic map a section's card is dragged whole; a note's card and a selection are dragged as they
  //    are. One listener for every drag; the handle is outside the column, so the zoom's copies never hold it ──
  const LEVEL_NAMES = ['topic', 'takeaway', 'key', 'brief', 'full'];
  const handle = h('div', 'draghandle');
  handle.draggable = true;
  handle.hidden = true;
  const dragImg = h('div', 'dragimg');   // what the pointer carries: a line of what goes, not the whole paragraph
  document.body.append(handle, dragImg);
  let handleFor = null, handleTimer = 0;
  const HANDLED = '.u, .hd, .stake, .eq', SAID = '.np-q, .np-a';   // in the column (an equation set apart too); in a conversation
  function placeHandle(el, again = false) {
    clearTimeout(handleTimer);
    if (el === handleFor && !handle.hidden && !again) return;
    const r = el.getBoundingClientRect();
    if (!r.height) return;
    const said = el.matches(SAID);
    handleFor = el;
    handle.classList.toggle('slim', said);   // (in a conversation's margin, which is narrow)
    // (an item of a list: beside its list, where a paragraph's is, not over its number, which stands left of the item)
    const left = el.matches('li') ? el.parentElement.getBoundingClientRect().left : r.left;
    handle.style.left = `${Math.max(2, left - (said ? 12 : 22))}px`;
    handle.style.top = `${r.top + 2}px`;
    handle.hidden = false;
  }
  function hideHandle(now) {
    clearTimeout(handleTimer);
    if (now) { handle.hidden = true; handleFor = null; } else handleTimer = setTimeout(() => hideHandle(true), 220);
  }
  // (on a move, not on entering: a pointer that rested on a paragraph through a zoom gets its handle once it moves; on
  // the Topic map, none: its cards are dragged whole)
  docEl.addEventListener('mousemove', (e) => {
    if (level === 0 || busy || stage || (!handle.hidden && handleFor && handleFor.contains(e.target))) return;
    const el = e.target.closest(HANDLED);
    if (el && !el.hidden) placeHandle(el);
  }, { passive: true });
  for (const box of [chat, npop]) box.addEventListener('mousemove', (e) => {
    if (!handle.hidden && handleFor && handleFor.contains(e.target)) return;
    const el = e.target.closest(SAID);
    if (el) placeHandle(el);
  }, { passive: true });
  for (const box of [docEl, chat, npop]) box.addEventListener('mouseout', (e) => {
    const el = e.target.closest(box === docEl ? HANDLED : SAID);
    if (el && el === handleFor && !el.contains(e.relatedTarget) && e.relatedTarget !== handle) hideHandle();
  });
  handle.addEventListener('mouseenter', () => clearTimeout(handleTimer));
  handle.addEventListener('mouseleave', () => hideHandle());
  // a zoom or a key moves what it stood by: it comes back with the pointer; a scroll (the page's, the chat's) takes it
  // along, once a frame, while no zoom runs
  const hideNow = () => { if (!handle.hidden) hideHandle(true); };
  for (const ev of ['wheel', 'keydown', 'resize']) addEventListener(ev, hideNow, { passive: true });
  let follow = false;
  addEventListener('scroll', () => {
    if (handle.hidden || follow) return;
    follow = true;
    requestAnimationFrame(() => { follow = false; if (handle.hidden) return; if (busy || stage || !handleFor || !handleFor.isConnected) hideNow(); else placeHandle(handleFor, true); });
  }, { passive: true, capture: true });
  addEventListener('pointerdown', (e) => { if (e.target !== handle) hideNow(); }, { passive: true });

  // the Topic map: a section's card is dragged whole, grabbed anywhere (a click on a chip still opens it). It is made
  // draggable as it is pressed, and only then, so the words of a paragraph opened in it stay selectable
  let pressed = null;
  docEl.addEventListener('mousedown', (e) => {
    if (level !== 0 || e.button !== 0 || busy || stage) return;
    const card = e.target.closest('section.chunk'), u = e.target.closest('.u');
    if (!card || (u && u.dataset.l !== '0') || e.target.closest('a, button, details')) return;
    card.draggable = true;
    pressed = card;
  });
  const unpress = () => { if (pressed) { pressed.removeAttribute('draggable'); pressed = null; } };
  addEventListener('mouseup', unpress);
  addEventListener('dragend', unpress);

  // pieces as plain text: a formula as $LaTeX$, a gap as … (a CJK document's word may be a bare string)
  const piecesText = (pcs) => pcs.map((p, n) => (p.gap ? '…' : typeof p.tok === 'string' ? p.tok : tokPlain(p.tok)) + (n < pcs.length - 1 && (p.gap || p.tok.s || pcs[n + 1].gap) ? ' ' : '')).join('').replace(/\s+/g, ' ').trim();
  const secTitle = (c) => ((trOf(c) && trOf(c).title) || c.title || '').replace(/\s+/g, ' ').trim();
  const secOfEl = (el) => [...secs.values()].find((x) => x.hd === el || x.st === el);
  const secOfNote = (n) => (n.anchor && n.anchor.section ? (secs.get(n.anchor.section) || {}).c || null : null);
  // the selection's words as they read: a formula of the paper as $LaTeX$, one the model wrote as its source, never the
  // hidden source the page also holds; in a conversation, a citation as its label in brackets, as the exports write it,
  // and none of the buttons around the words
  function selectionText(range) {
    const f = range.cloneContents();
    for (const at of f.querySelectorAll('[data-id*="@"]')) { const k = at.dataset.id.split('@')[1].split('#')[0]; if (A[k]) at.replaceWith(tokPlain({ a: k })); }
    for (const k of f.querySelectorAll('.katex')) { const src = k.querySelector('annotation'); k.replaceWith(src ? `$${src.textContent}$` : k.textContent); }
    for (const x of f.querySelectorAll('math')) x.replaceWith(x.getAttribute('alttext') ? `$${x.getAttribute('alttext')}$` : x.textContent);
    for (const x of f.querySelectorAll('.np-cite.in, .np-inf')) x.replaceWith(` (${x.textContent.trim()})`);
    for (const x of f.querySelectorAll('.np-cite.bad, .np-tdel, .np-next, .ch-try')) x.remove();
    for (const x of f.querySelectorAll('.num')) x.after(' ');   // (a heading's number, set apart by its margin)
    return f.textContent.replace(/[\u200b\s]+/g, ' ').trim();
  }
  const headingOf = (c) => `${c.num || ''} ${secTitle(c)}`.trim();
  const summaryOf = (c) => { const lad = (trOf(c) && trOf(c).lad) || c.lad; return lad && lad.take.length ? toksText(lad.take, 0, lad.take.length - 1) : ''; };
  // what a handle's paragraph, heading, summary or equation gives (a heading: its whole section; an equation: its
  // LaTeX, with its number, and a link to it, by way of the paragraph it goes with for a reader that knows no eq=)
  function itemDrag(el) {
    if (el.classList.contains('eq')) {
      const uid = el._unit || null;
      return { kind: 'equation', uid, eq: el.dataset.eq, c: (uid && chunkOf[uid]) || D.chunks.find((x) => x.id === el.dataset.chunk) || null,
        L: level, text: `$$${eqTex(el)}${eqTag(el)}$$`, written: false, units: uid ? [uid] : [] };
    }
    if (el.classList.contains('u')) {
      const uid = el.dataset.u, L = levelOf(uid);
      return { kind: 'paragraph', uid, c: chunkOf[uid] || null, L, text: piecesText(pieces(uid, L)), written: L <= 2, units: [uid] };
    }
    const sc = secOfEl(el);
    if (!sc) return null;
    if (el === sc.st) return { kind: 'summary', c: sc.c, L: level, text: summaryOf(sc.c), written: true };
    return sectionDrag(sc.c);
  }
  // a section as it reads at the level shown: its summary (where the level shows it, Key and Takeaway), then its
  // paragraphs, a bullet each at the written levels, a paragraph each at Brief and Full; and so through the subsections
  // under it, each after its own heading
  function sectionDrag(c) {
    const written = level <= 2, blocks = [], units = [];
    for (const x of sectionWith(c)) {
      const head = [x === c ? '' : headingOf(x), level >= 1 && written ? summaryOf(x) : ''].filter(Boolean), paras = [];
      for (const uid of x.units) {
        const t = unitEl.has(uid) ? piecesText(pieces(uid, levelOf(uid))) : '';
        if (t) { paras.push(t); units.push(uid); }
      }
      if (written) blocks.push([...head, ...paras.map((t) => `- ${t}`)].join('\n'));
      else blocks.push(...head, ...paras);
    }
    return { kind: 'section', c, L: level, heading: headingOf(c), text: blocks.filter(Boolean).join('\n\n'), written, units };
  }
  // a section's card on the Topic map, as the card reads: its summary (the line under its title), then the topics of
  // its paragraphs (its chips)
  function cardDrag(card) {
    const sc = secs.get(card.dataset.chunk);
    if (!sc) return null;
    const c = sc.c, summary = sc.st.getClientRects().length ? summaryOf(c) : '';
    const units = c.units.filter((uid) => unitEl.has(uid) && piecesText(pieces(uid, 0)));
    const chips = units.map((uid) => piecesText(pieces(uid, 0)));
    return { kind: 'section', c, L: 0, heading: headingOf(c), text: [summary, chips.join(' · ')].filter(Boolean).join('\n'), written: true, units };
  }
  // a note's card, or its row in the notes list: a highlight or a note (its quote, and the reader's words), or a
  // conversation (the reader's question, and the model's answer in one sentence)
  function noteDrag(n) {
    const a = n.anchor || {}, quote = passage(a), c = secOfNote(n);
    if (n.kind === 'ask') {
      const turns = turnsOf(n), said = [...turns].reverse().find((x) => x.a);
      return { kind: 'ask', note: n, c, L: level, text: said ? said.takeaway || '' : '', written: !!said, quote, comment: (turns[0] || {}).q || '' };
    }
    const comment = (n.text || '').trim();
    return { kind: 'note', note: n, c, L: level, text: comment || quote, written: !comment && (a.on === 'summary' || !!a.shown), quote, comment };
  }
  // one message of a conversation, in the chat or a note's window: its question, or its answer (its Markdown, a
  // citation as its label), or words selected in it; with the turn, so the link opens there
  function turnDrag(el, words = '') {
    const inChat = chat.contains(el), n = inChat ? chatNote : popNote, turnEl = el && el.closest('.np-turn');
    if (!n) return null;
    if (!turnEl || n.kind !== 'ask') return inChat ? null : noteDrag(n);   // (the passage over a note's window)
    const k = [...turnEl.parentElement.querySelectorAll(':scope > .np-turn')].indexOf(turnEl), x = turnsOf(n)[k];
    if (!x) return null;
    const answer = !!el.closest('.np-a'), a = n.anchor || {};
    return { kind: 'ask', note: n, turn: k, part: answer ? 'a' : 'q', c: secOfNote(n), L: level, text: words || (answer ? answerText(x.a).trim() : x.q),
      written: answer, quote: passage(a), comment: answer ? x.q : '' };
  }
  // a selection: its words, a paragraph each (a heading or a summary it crosses too), and every paragraph it touches;
  // the link names them from the first to the last (in one paragraph, that paragraph; in headings alone, the section)
  function selectionDrag(range) {
    const parts = [];
    for (const el of docEl.querySelectorAll('.hd, .stake, .u, .eq')) {
      const box = el.classList.contains('u') ? el._tx : el;
      if (!box || el.hidden || !range.intersectsNode(box) || !box.getClientRects().length) continue;   // (a summary the level does not show)
      if (el.classList.contains('eq')) { parts.push({ el, uid: null, text: `$$${eqTex(el)}${eqTag(el)}$$` }); continue; }   // (an equation: whole)
      const r = range.cloneRange();
      if (!box.contains(range.startContainer)) r.setStart(box, 0);
      if (!box.contains(range.endContainer)) r.setEnd(box, box.childNodes.length);
      const text = selectionText(r);
      if (text) parts.push({ el, uid: el.classList.contains('u') ? el.dataset.u : null, text });
    }
    if (!parts.length) return null;
    const units = parts.filter((x) => x.uid).map((x) => x.uid), text = parts.map((x) => x.text).join('\n\n');
    const eqs = parts.filter((x) => x.el.classList.contains('eq'));
    if (!units.length && eqs.length) return { ...itemDrag(eqs[0].el), kind: 'selection', text, quote: text };   // (equations alone: to the first)
    const sc = units.length ? null : secOfEl(parts[0].el);
    if (!units.length && !sc) return null;
    const L = units.length ? levelOf(units[0]) : level;
    return { kind: 'selection', uid: units[0] || null, to: units.length > 1 ? units[units.length - 1] : null, c: units.length ? chunkOf[units[0]] || null : sc.c, L, text,
      written: units.some((x) => levelOf(x) <= 2) || parts.some((x) => x.el.classList.contains('stake')), quote: text, units };
  }
  function dragData(d) {
    const m = D.meta, title = m.name || (m.titles && m.titles[lang]) || m.title, c = d.c;
    const q = new URLSearchParams({ paper: m.id });
    if (d.note) { q.set('n', d.note.id); if (d.turn !== undefined) q.set('t', d.turn); } else if (d.uid) { q.set('u', d.uid); if (d.to) q.set('to', d.to); } else if (c) q.set('s', c.id);
    if (d.eq && !d.note) q.set('eq', d.eq);
    if (!d.note) q.set('level', LEVEL_NAMES[d.L]);
    q.set('lang', lang);
    const link = `textfold://open?${q}`, citekey = m.zotero && m.zotero.citekey;
    const json = { format: 'textfold/drag', version: 1, paper: m.id, title, authors: m.authors || [], ...(citekey ? { citekey } : {}),
      section: c ? { id: c.id, num: c.num || '', title: secTitle(c) } : null, ...(d.heading ? { heading: d.heading } : {}), kind: d.kind, level: LEVEL_NAMES[d.L], lang,
      ...(d.eq ? { equation: d.eq } : {}), text: d.text, written: d.written, ...(d.units ? { units: d.units } : {}), ...(d.quote ? { quote: d.quote } : {}), ...(d.comment ? { comment: d.comment } : {}), link };
    const where = c ? `${title} › ${`${c.num || ''} ${secTitle(c)}`.trim()}` : title;
    // after the quote: a note's text, or a conversation's question (in bold) and its answer (Markdown, the model's)
    const said = (d.part === 'q' ? [[d.text, true]] : d.kind === 'ask' ? [[d.comment, true], [d.text, false, true]] : d.kind === 'note' ? [[d.comment]] : []).filter(([x]) => x);
    const shown = d.kind === 'note' || d.kind === 'ask' ? d.quote : d.text;
    // the quote: each line a paragraph of it, bullets that follow one another one list
    const bullet = (l) => l.startsWith('- ');
    const quoted = (x) => x.split('\n').map((l, i, a) => (i && l && a[i - 1] && !(bullet(l) && bullet(a[i - 1])) ? '>\n' : '') + (l ? `> ${l}` : '>')).join('\n');
    const md = [shown ? quoted(shown) : '', ...said.map(([x, b]) => (b ? `**${x}**` : x)),
      `— [${where.replace(/([[\]])/g, '\\$1')}](${link})`].filter(Boolean).join('\n\n');
    const para = (x, b, md) => (md ? mdLite(x) : `<p>${b ? '<strong>' : ''}${esc(x).replace(/\n/g, '<br>')}${b ? '</strong>' : ''}</p>`);
    const blocks = (x) => x.split(/\n{2,}/).map((blk) => blk.split('\n').reduce((h, l) => (bullet(l)
      ? (h.endsWith('</ul>') ? h.slice(0, -5) : `${h}<ul>`) + `<li>${esc(l.slice(2))}</li></ul>` : `${h}<p>${esc(l)}</p>`), '')).join('');
    const html = `<div data-textfold="${esc(JSON.stringify(json))}">${shown ? `<blockquote>${blocks(shown)}</blockquote>` : ''}` +
      `${said.map(([x, b, md]) => para(x, b, md)).join('')}<p>— <a href="${esc(link)}">${esc(where)}</a></p></div>`;
    return { md, html, link, json: JSON.stringify(json), label: (d.heading || d.text || shown || where).replace(/\s+/g, ' ').slice(0, 90) };
  }
  document.addEventListener('dragstart', (e) => {
    const t = e.target.nodeType === 1 ? e.target : e.target.parentElement, sel = getSelection();
    const range = sel.rangeCount && !sel.isCollapsed ? sel.getRangeAt(0) : null;
    let d = null;
    if (e.target === handle) d = handleFor && handleFor.isConnected ? (handleFor.matches(SAID) ? turnDrag(handleFor) : itemDrag(handleFor)) : null;
    else if (t && t === pressed) d = cardDrag(t);
    else if (t && t.closest('.ncard[data-note], .nl-item[data-note]')) {
      const id = t.closest('[data-note]').dataset.note, n = notes.find((x) => x.id === id);
      d = n && noteDrag(n);
    } else if (t && range && range.intersectsNode(e.target)) {
      if (docEl.contains(t)) d = selectionDrag(range);
      else if (chat.contains(t) || npop.contains(t)) {   // (words of a conversation: the message they start in)
        const s0 = range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentElement;
        d = turnDrag(s0.closest('.np-turn') ? s0 : t, selectionText(range));
      }
    }
    if (!d) return;
    const out = dragData(d), dt = e.dataTransfer;
    dt.clearData();
    dt.setData('text/plain', out.md);
    dt.setData('text/uri-list', out.link);
    dt.setData('text/html', out.html);
    dt.setData('application/x-textfold+json', out.json);
    dt.effectAllowed = 'copy';
    if (e.target === handle) { dragImg.textContent = out.label; dt.setDragImage(dragImg, 12, 12); }
  });

  // ── the type's menu (Aa): the size a step at a time (its percentage: the usual size again), the column's width and
  //    the font. Each change shows at once, the paragraph being read kept where it was on the screen ──
  const typePick = h('div', 'typepick');
  const typeBtn = h('button', 'typebtn', '<span aria-hidden="true">Aa</span>');
  typeBtn.type = 'button';
  typeBtn.setAttribute('aria-haspopup', 'dialog');
  typeBtn.setAttribute('aria-expanded', 'false');
  const typeMenu = h('div', 'langmenu typemenu');
  typeMenu.hidden = true;
  typeMenu.setAttribute('role', 'dialog');
  typePick.append(typeBtn, typeMenu);
  const isType0 = () => type.size === TYPE0.size && type.width === TYPE0.width && type.font === TYPE0.font && !type.hide.length
    && type.mode === TYPE0.mode && type.style === TYPE0.style && type.lead === TYPE0.lead && Object.keys(HL0).every((g) => type.hl[g] === HL0[g]);
  function renderTypeMenu() {
    const t = T[ui], k = SIZES.indexOf(type.size);
    const seg = (key, vals, names) => `<div class="ty-seg" role="radiogroup" aria-label="${esc(t[`ty_${key}`])}">` +
      vals.map((v, i) => `<button type="button" role="radio" aria-checked="${type[key] === v}" data-${key}="${v}">${esc(names[i])}</button>`).join('') + '</div>';
    typeMenu.setAttribute('aria-label', t.ty_title);
    typeMenu.innerHTML = `<div class="ty-row"><span class="ty-l">${esc(t.ty_mode)}</span>${seg('mode', MODES, [t.ty_auto, t.ty_light, t.ty_dark])}</div>` +
      `<div class="ty-row"><span class="ty-l">${esc(t.ty_style)}</span>${seg('style', STYLES, [t.ty_default, t.ty_paper])}</div>` +
      `<div class="ty-row"><span class="ty-l">${esc(t.ty_size)}</span><div class="ty-size">` +
      `<button type="button" class="ty-a sm" data-step="-1" aria-label="${esc(t.ty_smaller)}" title="${esc(t.ty_smaller)}"${k === 0 ? ' disabled' : ''}>A</button>` +
      `<button type="button" class="ty-pct" data-step="0" title="${esc(t.ty_usual)}">${Math.round(type.size * 100)}%</button>` +
      `<button type="button" class="ty-a lg" data-step="1" aria-label="${esc(t.ty_larger)}" title="${esc(t.ty_larger)}"${k === SIZES.length - 1 ? ' disabled' : ''}>A</button></div></div>` +
      `<div class="ty-row"><span class="ty-l">${esc(t.ty_font)}</span>${seg('font', ['serif', 'sans'], [t.ty_serif, t.ty_sans])}</div>` +
      `<div class="ty-row"><span class="ty-l">${esc(t.ty_lead)}</span>${seg('lead', Object.keys(LEADS), [t.ty_tight, t.ty_standard, t.ty_loose])}</div>` +
      `<div class="ty-row ty-wrow"><span class="ty-l">${esc(t.ty_width)}</span>${seg('width', WIDTHS, [t.ty_narrow, t.ty_standard, t.ty_wide])}</div>` +
      `<div class="ty-row"><span class="ty-l">${esc(t.ty_margin)}</span><div class="ty-seg ty-checks" role="group" aria-label="${esc(t.ty_margin)}">` +
      MARGINS.map((k) => `<button type="button" aria-pressed="${!type.hide.includes(k)}" data-margin="${k}">${esc(t[`ty_m_${k}`])}</button>`).join('') + '</div></div>' +
      hueRows(t) +
      `<div class="ty-foot"><span class="ty-modes"><button type="button" class="ty-focus" data-focus="1">${esc(focusOn ? t.ty_focus_off : t.ty_focus)}<kbd>F</kbd></button>` +
      (canFull ? `<button type="button" class="ty-focus" data-full="1">${esc(fullOn ? t.ty_full_off : t.ty_full)}<kbd>${FULL_KEY}</kbd></button>` : '') + '</span>' +
      `<button type="button" class="ty-reset"${isType0() ? ' hidden' : ''}>${esc(t.ty_reset)}</button></div>`;
  }
  // the colour of the highlights made at each level, among the hues; two levels of one colour are said to be (a
  // highlight no longer tells where it was made)
  function hueRows(t) {
    const LV = { src: `${t.levels[4]} · ${t.levels[3]}`, key: t.levels[2], take: t.levels[1] }, names = Object.keys(HUES), gs = Object.keys(HL0);
    const same = gs.flatMap((g, i) => gs.slice(i + 1).filter((x) => type.hl[x] === type.hl[g]).map((x) => [LV[g], LV[x]]))[0];
    return `<div class="ty-hl"><span class="ty-l">${esc(t.ty_hl)}</span>` + Object.keys(HL0).map((g) =>
      `<div class="ty-hrow"><span>${esc(LV[g])}</span><div class="ty-hues" role="radiogroup" aria-label="${esc(LV[g])}">` +
      names.map((c, k) => `<button type="button" role="radio" class="hue-${c}" aria-checked="${type.hl[g] === c}" data-hl="${g}" data-hue="${c}" title="${esc(t.n_hues[k])}" aria-label="${esc(t.n_hues[k])}"></button>`).join('') +
      '</div></div>').join('') + (same ? `<p class="ty-same">${esc(t.ty_same.replace('{a}', same[0]).replace('{b}', same[1]))}</p>` : '') + '</div>';
  }
  // full screen: the app's window (as View › Toggle Full Screen, the key beside it), else the browser's
  const DESK = window.desktop && window.desktop.fullScreen ? window.desktop : null;
  const canFull = Boolean(DESK || document.documentElement.requestFullscreen);   // (none in an iPhone's Safari)
  const FULL_KEY = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌃⌘F' : 'F11';
  let fullOn = Boolean(document.fullscreenElement);
  const fullIs = (on) => { fullOn = on; if (!typeMenu.hidden) renderTypeMenu(); };
  if (DESK) { DESK.fullScreen().then(fullIs, () => {}); DESK.onFullScreen(fullIs); }
  else document.addEventListener('fullscreenchange', () => fullIs(Boolean(document.fullscreenElement)));
  function setFull(on) {
    if (DESK) DESK.fullScreen(on);
    else if (on) document.documentElement.requestFullscreen().catch(() => {});
    else if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  }
  function setType(next) {
    if (busy) return;   // (not while a zoom plays)
    snapNow();
    const anchor = defaultAnchor();
    type = { ...type, ...next };
    store.set(TYPE_KEY, JSON.stringify(type));
    applyType(); mirrorType();
    drawDiagrams();
    settle(anchor);
    capEpoch++; relayout(); geom = null; placeThumb(); warmSoon();
  }
  // a level's colour: nothing moves, so the page is only recoloured (the keyframes, which hold the marks, drawn again)
  function setHue(g, c) {
    if (!HUES[c] || type.hl[g] === c) return;
    type = { ...type, hl: { ...type.hl, [g]: c } };
    store.set(TYPE_KEY, JSON.stringify(type));
    applyType(); capEpoch++; warmSoon();
  }
  function closeTypeMenu() { typeMenu.hidden = true; typeBtn.setAttribute('aria-expanded', 'false'); }
  typeBtn.addEventListener('click', () => {
    if (!typeMenu.hidden) return closeTypeMenu();
    closeLangMenu();
    renderTypeMenu(); typeMenu.hidden = false; typeBtn.setAttribute('aria-expanded', 'true');
  });
  typeMenu.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b || b.disabled) return;
    const k = SIZES.indexOf(type.size), d = +b.dataset.step;
    if (b.dataset.step) setType({ size: d ? SIZES[Math.max(0, Math.min(SIZES.length - 1, k + d))] : 1 });
    else if (b.dataset.width) setType({ width: b.dataset.width });
    else if (b.dataset.font) setType({ font: b.dataset.font });
    else if (b.dataset.mode) setType({ mode: b.dataset.mode });
    else if (b.dataset.style) setType({ style: b.dataset.style });
    else if (b.dataset.lead) setType({ lead: b.dataset.lead });
    else if (b.dataset.hl) setHue(b.dataset.hl, b.dataset.hue);
    else if (b.dataset.focus) { closeTypeMenu(); setFocus(!focusOn); return; }
    else if (b.dataset.full) { closeTypeMenu(); setFull(!fullOn); return; }
    else if (b.dataset.margin) { const k = b.dataset.margin; setType({ hide: MARGINS.filter((x) => (x === k) !== type.hide.includes(x)) }); }
    else setType({ ...TYPE0 });
    // drawn anew: the key that was pressed keeps the focus (the reset, gone: the first)
    const sel = b.dataset.step ? `[data-step="${b.dataset.step}"]` : b.dataset.width ? `[data-width="${b.dataset.width}"]` : b.dataset.font ? `[data-font="${b.dataset.font}"]`
      : b.dataset.margin ? `[data-margin="${b.dataset.margin}"]` : b.dataset.mode ? `[data-mode="${b.dataset.mode}"]` : b.dataset.style ? `[data-style="${b.dataset.style}"]` : b.dataset.lead ? `[data-lead="${b.dataset.lead}"]`
      : b.dataset.hl ? `[data-hl="${b.dataset.hl}"][data-hue="${b.dataset.hue}"]` : null;
    renderTypeMenu();
    const again = (sel && typeMenu.querySelector(sel)) || typeMenu.querySelector('button');
    if (again && !again.disabled) again.focus(); else typeMenu.querySelector('.ty-pct').focus();
  });
  // (by where the click was when it happened: the menu is drawn anew by its own clicks)
  addEventListener('click', (e) => { if (!typeMenu.hidden && !e.composedPath().includes(typePick)) closeTypeMenu(); });

  // ── the notes button: how many, and the exports ──
  const nbtn = h('button', 'notesbtn');
  nbtn.type = 'button';
  nbtn.hidden = true;
  const nmenu = h('div', 'langmenu notesmenu');
  nmenu.hidden = true;
  const npick = h('div', 'notespick');
  const keepbtn = h('button', 'keepbtn');
  keepbtn.type = 'button';
  keepbtn.hidden = true;
  const savebtn = h('button', 'savebtn');   // the page as a file, or the notes alone: one place for every export
  savebtn.type = 'button';
  npick.append(keepbtn, nbtn, savebtn, nmenu);
  keepbtn.addEventListener('click', () => {
    keepMine = !keepMine;
    store.set('dr-keep-mine', keepMine ? '1' : '0');
    notesChanged([...notesOf.keys()]);
  });
  (document.querySelector('.langpick') || document.getElementById('langbtn')).before(npick, typePick);
  function updateNotesBtn() {
    const t = T[ui], n = notes.filter((x) => shown(x.anchor)).length;
    nbtn.hidden = false;   // always there: the exports (the paper as PDF too) are in its menu
    nbtn.textContent = n ? `${t.n_notes} ${n}` : t.n_notes;
    savebtn.innerHTML = `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.5v7.5M5 7l3 3 3-3M3 12.5h10"/></svg><span>${esc(t.sv_save)}</span>`;
    savebtn.title = t.sv_title;
    nbtn.classList.toggle('none', !n);
    keepbtn.hidden = !notes.some((x) => unitEl.has(x.anchor && x.anchor.unit) && !x.anchor.shown && (x.anchor.tokens || x.anchor.sents));   // (a mark on a written line is up there already)
    keepbtn.innerHTML = `<i></i>${esc(t.n_keep)}`;
    keepbtn.title = t.n_keep_tip;
    keepbtn.setAttribute('aria-pressed', String(keepMine));
    keepbtn.classList.toggle('on', keepMine);
  }
  // The menu: every highlight, note and question in reading order, by section, with a filter by kind. A click flies
  // to it; × deletes it (a second click confirms). Below them, the exports.
  let nmFilter = 'all', nmSure = null;
  const kindOf = (n) => (n.kind === 'ask' ? 'ask' : n.text && n.text.trim() ? 'note' : 'hl');
  const secOf = (a) => (whole(a) ? null : a.unit ? chunkOf[a.unit] : (secs.get(a.section) || {}).c);
  const groupOf = (a) => (whole(a) ? T[ui].n_whole : secOf(a) ? secName(secOf(a)) : '');
  // text with $formulas$ set as math, shortened without cutting a formula in two
  const clipTex = (x, n) => {
    if (x.length <= n || /^\$[^$]+\$$/.test(x)) return x;   // (an equation alone: whole)
    let c = x.slice(0, n - 1); if ((c.match(/\$/g) || []).length % 2) c = c.slice(0, c.lastIndexOf('$')); return c.trimEnd() + '…';
  };
  const texLite = (x) => esc(x).replace(/\$([^$]+)\$/g, (m, t) => `<span class="texsrc">${t}</span>`);
  const secName = (c) => `${c.num ? c.num + ' ' : ''}${(trOf(c) || c).title.replace(/\$/g, '')}`;
  function renderNotesMenu() {
    const t = T[ui], all = inOrder(), count = { all: all.length, hl: 0, note: 0, ask: 0 };
    for (const n of all) count[kindOf(n)]++;
    let sec = null, items = '';
    for (const n of nmFilter === 'all' ? all : all.filter((x) => kindOf(x) === nmFilter)) {
      const a = n.anchor, g = groupOf(a), k = kindOf(n), turns = turnsOf(n), said = [...turns].reverse().find((x) => x.a);
      if (g && g !== sec) { sec = g; items += `<div class="nl-sec">${esc(g)}</div>`; }
      const q = passage(a), gone = lineGone(a) ? `<em class="nl-gone">${esc(t.n_line_changed)}</em>` : '';
      const body = k === 'ask'
        ? `<b>${esc((turns[0] || {}).q || '')}</b>${said ? `<span class="nl-a">${texLite(clipTex(said.takeaway, 120))}</span>` : ''}${q ? `<span class="nl-q">“${texLite(clipTex(q, 80))}”</span>` : ''}${gone}`
        : k === 'note' ? `<span class="nl-t">${esc(clip(n.text.trim(), 150))}</span>${q ? `<span class="nl-q">“${texLite(clipTex(q, 80))}”</span>` : ''}${gone}`
        : `<span class="nl-hl">${texLite(clipTex(q, 150))}</span>${gone}`;
      const sure = nmSure === n.id;
      items += `<div class="nl-item ${k}${a.shown ? ` ai l${a.level}` : ''}${k === 'ask' ? '' : toneCls(n)}" draggable="true" data-note="${esc(n.id)}"><button type="button" class="nl-go">${body}</button>` +
        `<button type="button" class="nl-del${sure ? ' sure' : ''}" title="${esc(sure ? t.n_del_sure : t.n_delete)}">${sure ? esc(t.n_del_sure) : '×'}</button></div>`;
    }
    const tab = (f, label) => `<button type="button" class="nl-tab${nmFilter === f ? ' on' : ''}" data-f="${f}">${esc(label)}<em>${count[f]}</em></button>`;
    const list = nmenu.querySelector('.nl-list'), top = list ? list.scrollTop : 0;
    nmenu.innerHTML = `<div class="nl-tabs">${tab('all', t.n_all)}${tab('hl', t.n_highlight)}${tab('note', t.n_note)}${tab('ask', t.n_ask)}` +
      `<button type="button" class="nl-new" data-x="card" title="${esc(t.n_card_tip)}">＋ ${esc(t.n_card)}</button></div>` +
      `<div class="nl-list">${items || `<div class="nm-note">${esc(t.n_none)}</div>`}</div>` +
      `<div class="nl-x"><button type="button" class="lm-item" data-x="save"><span>${esc(t.sv_save)}…</span></button></div>` +
      (D.app ? '' : `<div class="nm-note">${esc(t.n_local)}</div>`);
    texify(nmenu);
    nmenu.querySelector('.nl-list').scrollTop = top;
  }
  nbtn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (!nmenu.hidden) { nmenu.hidden = true; return; }
    nmSure = null;
    renderNotesMenu();
    nmenu.hidden = false;
  });
  nmenu.addEventListener('click', (e) => {
    e.stopPropagation();   // (a redrawn menu leaves the clicked button detached: the page's click must not close it)
    const tab = e.target.closest('.nl-tab');
    if (tab) { nmFilter = tab.dataset.f; nmSure = null; renderNotesMenu(); return; }
    const item = e.target.closest('.nl-item'), n = item && notes.find((x) => x.id === item.dataset.note);
    if (n && e.target.closest('.nl-del')) {
      if (nmSure !== n.id) { nmSure = n.id; renderNotesMenu(); return; }
      nmSure = null;
      if (popNote && popNote.id === n.id) closeNote();
      deleteNote(n);
      if (notes.some((x) => shown(x.anchor))) renderNotesMenu(); else nmenu.hidden = true;
      return;
    }
    if (n) { nmenu.hidden = true; flyToNote(n); return; }
    if (nmSure) { nmSure = null; renderNotesMenu(); }
    if (e.target.closest('[data-x="save"]')) openSave();
    if (e.target.closest('[data-x="card"]')) { nmenu.hidden = true; newCard(); }
  });
  addEventListener('click', (e) => { if (!nmenu.hidden && !e.target.closest('.notesmenu')) nmenu.hidden = true; });
  // after any zoom under way (and a moment more)
  const afterZoom = (fn, wait = 0) => setTimeout(function tick() { if (busy) setTimeout(tick, 80); else fn(); }, wait);
  // a note from the list: the page flies to its passage (from the map, it first steps in to Key), opens the paragraph
  // to its full text in place where the zoom hides the passage, and opens the note there; Back returns
  // a note of the reader's own on the whole paper, not on a passage: written in a card by the paper's head (kept once
  // it has words; an empty one goes when it closes)
  function newCard() {
    if (popNote) closeNote();
    scrollTo({ top: 0, behavior: 'smooth' });   // (the head opens the page)
    const n = { id: noteId(), kind: 'note', text: '', thread: [],
      anchor: { unit: null, section: null, on: 'paper', lang, level, tokens: null, sents: null, quote: { exact: '' } } };
    afterZoom(() => openNote(n, true), 260);
  }
  function flyToNote(n) {
    const a = n.anchor, from = { y: scrollY, level, from: null };
    if (whole(a) && n.kind === 'ask') { openChat(n); return; }
    if (whole(a)) { if (popNote) closeNote(); scrollTo({ top: 0, behavior: 'smooth' }); afterZoom(() => openNote(n), 260); return; }
    if (busy || !targetEl(a)) return;
    if (popNote) closeNote();
    const fly = () => {
      const el = targetEl(a);
      go(el, null);
      backTo = from;
      const hidden = a.unit && level < 4 && !localLevel.has(a.unit) && !el.querySelector('.hl');
      afterZoom(() => {
        if (hidden && !localLevel.has(a.unit)) toggleUnit(a.unit);
        afterZoom(() => openNote(n), hidden ? 120 : 0);
      }, 480);
    };
    if (level === 0) {
      const el = unitEl.get(a.unit || (secOf(a).units || [])[0]) || targetEl(a), r = el.getBoundingClientRect();
      setLevel(2, { el, y: r.top, frac: 0 });
      afterZoom(fly, 120);
    } else fly();
  }
  function download(name, text, type) {
    const a = h('a');
    a.href = URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` }));
    a.download = name;
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
  // in reading order: by paragraph, then by where the passage starts
  const placeOf = (a) => (whole(a) ? -1e9 : a.unit ? order.get(a.unit) : (HEAD_IDX.get(secs.get(a.section).hd) ?? 0) - 0.5);   // the paper, then a section before its paragraphs
  const inOrder = (kinds) => notes.filter((n) => shown(n.anchor) && (!kinds || kinds.has(kindOf(n))))
    .sort((x, y) => (placeOf(x.anchor) - placeOf(y.anchor)) || (((x.anchor.tokens || [0])[0]) - ((y.anchor.tokens || [0])[0])));
  function notesJson(kinds) {
    const m = D.meta;
    return { format: 'textfold/notes', version: 1, made_by: `${BRAND} · ${REPO}`, paper: { id: m.id, version: m.version, title: m.title, url: m.abs_url, ...(m.snapshot ? { snapshot: m.snapshot } : {}) }, notes: inOrder(kinds).map((n) => Object.fromEntries(Object.entries(n).filter(([k]) => !['_base', '_revision'].includes(k)))) };
  }
  // Markdown for Obsidian: a note per paper; each highlight a quote with a block id (^n-…) other notes can link to,
  // each question a callout; every one links back to its place in the reader
  const mdQuote = (s) => s.split('\n').map((l) => `> ${l}`);
  // a Markdown file's head: its properties (Obsidian's), the title, the byline
  function mdHead(title, props, format) {
    const m = D.meta, q = (s) => JSON.stringify(String(s || ''));
    return ['---', `title: ${q(m.title)}`, `authors: ${JSON.stringify(m.authors || [])}`,
      m.abs_url ? `arxiv: ${q(m.id + (m.version || ''))}` : `source: ${q(sourceName(m))}`,
      ...(m.abs_url ? [`url: ${m.abs_url}`] : []), ...(m.snapshot ? [`snapshot: ${m.snapshot}`] : []), `license: ${m.license || ''}`, ...(D.app ? [`reader: ${location.origin}/p/${m.id}`] : []),
      ...props, `exported: ${new Date().toISOString().slice(0, 10)}`, `tags: [${PAPER ? 'paper' : 'text'}]`, ...(format ? [`format: ${format}`] : []), '---', '',
      `# ${title}`, '', ...(title !== m.title ? [`*${m.title}*`, ''] : []),
      [(m.authors || []).map(flipName).join(', '), m.abs_url ? `[${sourceName(m)}](${m.abs_url})` : sourceName(m)].filter(Boolean).join(' · '), ''];
  }
  // one note: its passage quoted (or the whole paragraph around it), its block id, the reader's words, the link back;
  // a conversation as one callout, the first question as its title, each later one in bold
  // (a mark on a written line quotes the line as the model wrote it, and says so: never as the author's words)
  function noteMd(n, para) {
    const out = [], said = n.kind === 'ask' ? turnsOf(n).filter((x) => x.a) : [], a = n.anchor, q = passage(a);
    if (said.length) {
      const around = !a.shown && para && paraOf(a, true);
      out.push(...mdQuote(`[!question] ${said[0].q}`), ...(around ? mdQuote(around) : q ? mdQuote(`*${q}*${a.shown ? ` (${lineAt(a)})` : ''}`) : []));
      said.forEach((x, k) => out.push('>', ...(k ? mdQuote(`**${x.q}**`) : []), ...(k ? ['>'] : []), ...mdQuote(answerText(x.a)), '>', ...mdQuote(`→ ${x.takeaway}`)));
    } else if (a.shown) out.push(...mdQuote(q), '>', ...mdQuote(`*${lineAt(a)}*`));
    else if (!whole(a)) out.push(...mdQuote((para && paraOf(a, true)) || q));   // (a note on the whole paper quotes nothing)
    out.push('', `^${n.id}`, '');
    if (n.kind !== 'ask' && shadeOf(n) !== 2) out.push(`#highlight/${shadeOf(n) === 1 ? 'light' : 'deep'}`, '');   // (its shade, as a tag Obsidian can search)
    if (n.text && n.text.trim()) out.push(n.text.trim(), '');
    out.push(`[${T[ui].n_open}](${noteLink(n)})`, '');
    return out;
  }
  function notesMarkdown(kinds, para) {
    const out = mdHead(D.meta.title, [], 'textfold/notes@1');
    let sec = null;
    for (const n of inOrder(kinds)) {
      const g = groupOf(n.anchor);
      if (g && g !== sec) { sec = g; out.push(`## ${g}`, ''); }
      out.push(...noteMd(n, para));
    }
    out.push('---', '', `*Made with [${BRAND}](${REPO})*`, '');
    return out.join('\n');
  }
  // the whole text as Markdown at the level being read (a paragraph opened in place, as it is open), in the language
  // being read: each heading, the section's summary where the level shows one (Topic, Takeaway, Key), then its
  // paragraphs, a bullet each at the written levels and a paragraph each at Brief and Full. The marks chosen sit
  // under their paragraph (or section, or the title) as the notes' own file writes them; a highlight alone, in the
  // author's words that Brief and Full show, is marked in them (==…==) instead. At Topic it is an outline: the
  // headings and the paragraphs' topics, which an outliner or a mind-map plugin draws as a map
  function docMarkdown(kinds) {
    const m = D.meta, t = T[ui], title = m.name || (m.titles && m.titles[lang]) || m.title;
    const out = mdHead(title, [`level: ${LEVEL_NAMES[level]}`, `lang: ${lang}`], null);
    out.push(`*${t.levels[level]} · ${BRAND}*`, '');
    const mine = new Map();
    for (const n of inOrder(kinds)) { const k = keyOf(n.anchor); if (!mine.has(k)) mine.set(k, []); mine.get(k).push(n); }
    const notesAt = (k) => (mine.get(k) || []).flatMap((n) => noteMd(n, false));
    out.push(...notesAt('¶'));
    const flat = (s) => String(s || '').replace(/\s+/g, ' ').trim();
    for (const c of D.chunks) {
      const sc = secs.get(c.id);
      if (!sc || (level < 4 && sc.sec.classList.contains('ref'))) continue;   // (dropped at the folded levels, as on the page)
      if (c.num || c.title) out.push(`${'#'.repeat(Math.min(6, c.level || 2))} ${headingOf(c)}`, '');
      const sum = level <= 2 ? summaryOf(c) : '';
      if (sum) out.push(`**${sum}**`, '');
      out.push(...notesAt(`§${c.id}`));
      let list = false;   // (bullets that follow one another: one list)
      for (const uid of c.units) {
        const L = levelOf(uid), u = U[uid], marks = mine.get(uid) || [], under = [];
        let text = unitEl.has(uid) ? piecesText(pieces(uid, L)) : '';
        for (const n of marks) {
          const a = n.anchor, ex = kindOf(n) === 'hl' && (a.shown ? L <= 2 && shownAt(a) && flat(a.shown.exact) : L >= 3 && flat(a.quote && a.quote.exact)), at = ex ? text.indexOf(ex) : -1;
          if (at >= 0) text = `${text.slice(0, at)}==${ex}==${text.slice(at + ex.length)}`; else under.push(n);
        }
        if (!text && !under.length) continue;
        const bullet = L <= 2 || u.role === 'item';
        if (text) {
          if (!bullet && list) out.push('');
          out.push(bullet ? `${L > 2 && u.marker && /\d/.test(u.marker) ? u.marker : '-'} ${text}` : u.role === 'caption' ? `*${text}*` : text);
          if (!bullet) out.push('');
          list = bullet;
        }
        if (under.length) { if (list) out.push(''); list = false; for (const n of under) out.push(...noteMd(n, false)); }
      }
      if (list) out.push('');
    }
    out.push('---', '', `*Made with [${BRAND}](${REPO})*`, '');
    return out.join('\n').replace(/\n{3,}/g, '\n\n');
  }
  // PDF of the paper as it stands: the level being read (with the paragraphs opened in place), the highlights, and
  // the notes and conversations under their paragraphs; a line naming the level on top, the TextFold mark at the
  // end. ⌘P prints the same. Every paragraph is drawn at this level first (far ones are otherwise drawn later).
  let unprint = null;
  function preparePaperPrint() {
    const m = D.meta, t = T[ui], root = document.documentElement, title = document.title;
    for (const x of staleQ) if (elOf(x)._stale) redraw(x);
    staleQ = [];
    const head = h('div', 'print-head'), mark = h('div', 'print-mark');
    head.innerHTML = `${LOGO}<span>${BRAND}</span><span>${esc(t.levels[level])} · ${esc(m.id + (m.version || ''))} · ${new Date().toISOString().slice(0, 10)}</span>`;
    mark.innerHTML = `${LOGO}<span>Made with ${BRAND}</span><a href="${REPO}">${REPO.replace('https://', '')}</a>`;
    docEl.before(head);
    docEl.after(mark);
    document.title = `${m.id} ${m.title} · ${t.levels[level]}`;
    root.classList.add('printing-paper');
    return () => { head.remove(); mark.remove(); document.title = title; root.classList.remove('printing-paper'); };
  }
  function paperPdf(kinds) {
    if (busy) return;
    if (popNote) closeNote();
    const all = notes, keep = kinds ? notes.filter((n) => kinds.has(kindOf(n))) : notes;
    if (keep.length !== all.length) {   // only the kinds of note chosen, while it prints
      const keys = [...new Set(all.map((n) => keyOf(n.anchor)))];
      notes = keep; indexNotes(); notesChanged(keys);
      const back = () => { notes = all; indexNotes(); notesChanged(keys); removeEventListener('afterprint', back); };
      addEventListener('afterprint', back);
    }
    unprint = preparePaperPrint();
    setTimeout(() => print(), 60);
  }
  addEventListener('beforeprint', () => {
    const root = document.documentElement;
    if (!unprint && !root.classList.contains('printing') && !busy) unprint = preparePaperPrint();
  });
  addEventListener('afterprint', () => { if (unprint) { unprint(); unprint = null; } });
  // PDF: the notes as a page the browser prints (and saves as PDF), in reading order, with the mark of where they
  // were made at the end. Drawn into this page (its fonts and math are here) and shown alone while printing.
  function notesPdf(kinds, para) {
    const m = D.meta, t = T[ui];
    let sec = null, body = '';
    for (const n of inOrder(kinds)) {
      const a = n.anchor, g = groupOf(a), said = n.kind === 'ask' ? turnsOf(n).filter((x) => x.a) : [], whole_ = para && paraOf(a);
      if (g && g !== sec) { sec = g; body += `<h2>${esc(g)}</h2>`; }
      body += `<div class="pf-n ${kindOf(n)}">${whole_ && !a.shown ? `<blockquote class="para">${whole_}</blockquote>` : passage(a) ? `<blockquote${a.shown ? ` class="ai l${a.level}"` : ''}>${texLite(passage(a))}</blockquote>` : ''}` +
        (a.shown ? `<div class="pf-c">${esc(lineAt(a))}</div>` : '') +
        (n.text && n.text.trim() ? `<div class="pf-t">${esc(n.text.trim()).replace(/\n/g, '<br>')}</div>` : '') +
        said.map((x) => `<div class="pf-q">${esc(x.q)}</div><div class="pf-a">${mdLite(answerText(x.a))}</div>` +
          (x.cites && x.cites.length && !/\[\[/.test(x.a || '') ? `<div class="pf-c">${x.cites.map((u) => esc(citeLabel(u))).join(' · ')}</div>` : '')).join('') +
        (said.length ? `<div class="pf-at">${esc(whole(a) ? T[ui].n_whole : askedAt(a))}${said[said.length - 1].model ? ` · ${esc(said[said.length - 1].model)}` : ''}</div>` : '') + '</div>';
    }
    const pr = h('div', 'printout');
    pr.innerHTML = `<header><h1>${esc(m.title)}</h1><p>${esc((m.authors || []).map(flipName).join(', '))}</p>` +
      `<p>${m.abs_url ? `<a href="${esc(m.abs_url)}">${esc(sourceName(m))}</a>` : esc(sourceName(m))} · ${esc(t.n_notes)} · ${new Date().toISOString().slice(0, 10)}</p></header>${body}` +
      `<footer class="pf-mark">${LOGO}<span>Made with ${BRAND}</span><a href="${REPO}">${REPO.replace('https://', '')}</a></footer>`;
    texify(pr);
    document.body.append(pr);
    const title = document.title, root = document.documentElement;
    document.title = `${m.id} ${m.title} · ${t.n_notes}`;
    root.classList.add('printing');
    const done = () => { pr.remove(); document.title = title; root.classList.remove('printing'); removeEventListener('afterprint', done); };
    addEventListener('afterprint', done);
    setTimeout(() => print(), 60);
  }

  // ── save: the whole text as one file (its levels, and the notes chosen), to keep; or the notes alone, with the
  //    passages they quote and a link to the source, to share. Every export is here, the notes menu leads here ──
  const svdlg = h('dialog', 'svdlg');
  document.body.append(svdlg);
  const SV_KEY = 'textfold.save';
  const sv = { what: 'whole', kinds: ['hl', 'note', 'ask'], fmt: 'html', langs: 'current', para: false, pdf: true,
    ...(() => { try { return JSON.parse(store.get(SV_KEY) || 'null'); } catch { return null; } })() };
  const fileName = () => `${D.meta.id} ${D.meta.title}`.replace(/[\\/:*?"<>|]+/g, ' ').slice(0, 90).trim();
  function openSave() {
    nmenu.hidden = true;
    renderSave();
    svdlg.showModal();
  }
  function renderSave(err) {
    const t = T[ui], counts = { hl: 0, note: 0, ask: 0 };
    for (const n of notes) if (shown(n.anchor)) counts[kindOf(n)]++;
    const total = counts.hl + counts.note + counts.ask;
    if (!total && sv.what === 'marks') sv.what = 'whole';
    const fmts = sv.what === 'whole' ? [...(D.app ? ['html'] : []), 'pdf', 'md'] : ['html', 'pdf', 'md', 'json'];   // (a page that is a file already prints)
    if (!fmts.includes(sv.fmt)) sv.fmt = fmts[0];
    const fmtName = { html: t.sv_html, pdf: sv.what === 'whole' ? t.sv_pdf_level.replace('{level}', t.levels[level]) : t.sv_pdf, md: sv.what === 'whole' ? t.sv_md_level.replace('{level}', t.levels[level]) : t.sv_md, json: t.sv_json };
    const radio = (name, value, label, sub, off) => `<label class="sv-opt${off ? ' off' : ''}"><input type="radio" name="${name}" value="${value}"` +
      `${sv[name] === value ? ' checked' : ''}${off ? ' disabled' : ''}><span><b>${esc(label)}</b>${sub ? `<em>${esc(sub)}</em>` : ''}</span></label>`;
    const box = (k, label) => `<label class="sv-box${counts[k] ? '' : ' off'}"><input type="checkbox" name="kind" value="${k}"` +
      `${sv.kinds.includes(k) && counts[k] ? ' checked' : ''}${counts[k] ? '' : ' disabled'}><span>${esc(label)}</span><i>${counts[k]}</i></label>`;
    const langs = sv.what === 'whole' && sv.fmt === 'html' && AVAIL.length > 1;
    const picked = sv.kinds.filter((k) => counts[k]).length;
    const para = sv.what === 'marks' && sv.fmt !== 'json'   // each mark in its paragraph: easier to read than the words alone
      ? `<label class="sv-box sv-para"><input type="checkbox" name="para"${sv.para ? ' checked' : ''}><span>${esc(t.sv_para)}</span></label>`
      : sv.what === 'whole' && sv.fmt === 'html' && D.app && PDF_OK   // the original beside it, in the file too
        ? `<label class="sv-box sv-para"><input type="checkbox" name="pdf"${sv.pdf ? ' checked' : ''}><span>${esc(t.sv_pdfin)}</span></label>` : '';
    svdlg.innerHTML = `<form method="dialog" class="sv-form">
      <div class="sv-head"><h2>${esc(t.sv_title)}</h2><button type="button" class="sv-x" aria-label="${esc(t.close)}">×</button></div>
      <div class="sv-row"><div class="sv-l">${esc(t.sv_what)}</div><div class="sv-opts">${radio('what', 'whole', t.sv_whole, t.sv_whole_d)}${radio('what', 'marks', t.sv_marks, t.sv_marks_d, !total)}</div></div>
      <div class="sv-row"><div class="sv-l">${esc(t.sv_incl)}</div><div class="sv-boxes">${box('hl', t.sv_hl)}${box('note', t.sv_notes)}${box('ask', t.sv_asks)}${para}</div></div>
      <div class="sv-row"><div class="sv-l">${esc(t.sv_fmt)}</div><div class="sv-opts sv-fmts">${fmts.map((f) => radio('fmt', f, fmtName[f])).join('')}</div></div>
      ${langs ? `<div class="sv-row"><div class="sv-l">${esc(t.sv_langs)}</div><div class="sv-opts sv-fmts">${radio('langs', 'current', t.sv_lang_cur.replace('{lang}', nameOf(lang)))}${radio('langs', 'all', t.sv_lang_all.replace('{n}', AVAIL.length))}</div></div>` : ''}
      <p class="sv-lic">${esc(t.sv_lic)}</p>
      ${err ? `<p class="sv-err">${esc(err)}</p>` : ''}
      <div class="sv-acts"><button type="button" class="sv-cancel">${esc(t.cancel)}</button><button type="submit" class="sv-go"${sv.what === 'marks' && !picked ? ' disabled' : ''}>${esc(t.sv_go)}</button></div>
    </form>`;
  }
  svdlg.addEventListener('change', (e) => {
    const f = svdlg.querySelector('form'), at = e.target;
    const off = sv.kinds.filter((k) => { const x = f.querySelector(`input[name="kind"][value="${k}"]`); return x && x.disabled; });   // kept for a text that has them
    sv.what = f.elements.what.value;
    sv.fmt = f.elements.fmt.value;
    if (f.elements.langs) sv.langs = f.elements.langs.value;
    sv.kinds = [...new Set([...[...f.querySelectorAll('input[name="kind"]:checked')].map((x) => x.value), ...off])];
    if (f.elements.para) sv.para = f.elements.para.checked;
    if (f.elements.pdf) sv.pdf = f.elements.pdf.checked;
    store.set(SV_KEY, JSON.stringify(sv));
    renderSave();
    const again = svdlg.querySelector(`input[name="${at.name}"][value="${at.value}"]`);
    if (again) again.focus();
  });
  svdlg.addEventListener('click', (e) => { if (e.target === svdlg || e.target.closest('.sv-x, .sv-cancel')) svdlg.close(); });
  svdlg.addEventListener('submit', (e) => { e.preventDefault(); doSave(); });
  savebtn.addEventListener('click', (e) => { e.stopPropagation(); openSave(); });
  // where a file goes, the reader chooses: the app's own save dialog, or the browser's (Chrome's file picker); a browser
  // without one keeps it in its downloads. make() gives the file's text once the place is chosen; null when cancelled
  async function saveAs(name, make, type) {
    if (window.desktop && window.desktop.saveFile) return window.desktop.saveFile(name, await make());
    if (window.showSaveFilePicker) {
      let handle = null;
      try {
        const ext = name.slice(name.lastIndexOf('.'));
        handle = await window.showSaveFilePicker({ suggestedName: name, types: [{ description: ext.slice(1).toUpperCase(), accept: { [type]: [ext] } }] });
      } catch (x) {
        if (x.name === 'AbortError') return null;   // (another refusal: the downloads, below)
      }
      if (handle) {
        const text = await make(), w = await handle.createWritable();
        await w.write(new Blob([text], { type: `${type};charset=utf-8` }));
        await w.close();
        return handle.name;
      }
    }
    download(name, await make(), type);
    return name;
  }
  // while a file is made (seconds, for a whole text with its PDF): the mark folding and unfolding, in place of the buttons
  function working() {
    const acts = svdlg.querySelector('.sv-acts');
    if (acts) acts.innerHTML = `<div class="sv-busy" role="status"><svg viewBox="0 0 20 20" aria-hidden="true"><rect class="b1" x="2" y="3" width="16" height="2.4" rx="1.2"/>` +
      `<rect class="b2" x="2" y="8.8" width="11" height="2.4" rx="1.2"/><rect class="b3" x="2" y="14.6" width="5" height="2.4" rx="1.2"/></svg><span>${esc(T[ui].sv_working)}</span></div>`;
  }
  function doSave() {   // (no wait before the file picker: it opens only on the click itself)
    const t = T[ui], kinds = new Set(sv.kinds), name = fileName(), para = sv.para;
    if (sv.fmt === 'pdf') {   // the system's print dialog, where it is saved as PDF wherever the reader chooses
      svdlg.close();
      if (sv.what === 'whole') paperPdf(kinds); else notesPdf(kinds, para);
      return;
    }
    const whole = sv.what === 'whole';
    const [file, type, make] = whole && sv.fmt === 'md' ? [`${name} (${t.levels[level]}).md`, 'text/markdown', () => docMarkdown(kinds)]
      : whole ? [`${name}.html`, 'text/html', async () => {   // made by the server: the levels, the languages, the notes chosen
        working();
        const r = await fetch('/api/export', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ paper: D.meta.id, lang, langs: sv.langs === 'all' ? 'all' : 'current', kinds: [...kinds], pdf: !!sv.pdf && PDF_OK }) });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status);
        return r.text();
      }]
      : sv.fmt === 'html' ? [`${name}.notes.html`, 'text/html', () => marksHtml(kinds, para)]
      : sv.fmt === 'md' ? [`${name}.md`, 'text/markdown', () => notesMarkdown(kinds, para)]
      : [`${name}.notes.json`, 'application/json', () => JSON.stringify(notesJson(kinds), null, 1)];
    saveAs(file, make, type).then((done) => { if (done) svdlg.close(); else renderSave(); },
      (x) => renderSave(t.sv_failed.replace('{e}', x.message)));
  }
  // the notes alone as one page anyone can open: the passages they quote (short, as a quotation is), the notes, the
  // questions and answers (their citations as labels: the paragraphs are not in it), the source's link, the mark.
  // Formulas as MathML, which a browser draws with no fonts to carry
  function paraOf(a, md) {
    const u = a.unit && U[a.unit];
    if (!u) return null;
    const z = u.tr && u.tr[a.lang], toks = z ? z.toks : u.toks, sents = z ? z.sents : u.sents;
    const spans = a.tokens ? [a.tokens] : (a.sents || []).map((k) => sents[k] && [sents[k][0], sents[k][1] - 1]).filter(Boolean);
    const on = (i) => spans.some(([x, y]) => i >= x && i <= y);
    let out = '';
    toks.forEach((tk, i) => {
      const w = tokPlain(tk), first = on(i) && !on(i - 1), last = on(i) && !on(i + 1);
      out += (first ? (md ? '**' : '<mark>') : '') + (md ? w : texLite(w)) + (last ? (md ? '**' : '</mark>') : '');
      if (tk.s && i < toks.length - 1) out += ' ';
    });
    return out;
  }
  async function marksHtml(kinds, para) {
    const k = await loadKatex(), m = D.meta, t = T[ui];
    const unesc = (x) => x.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&amp;/g, '&');
    const math = (html) => html.replace(/<span class="texsrc">([^<]*)<\/span>/g, (all, src) => {
      if (!k) return esc(`$${unesc(src)}$`);
      try { return k.renderToString(unesc(src), { output: 'mathml', throwOnError: false }); } catch { return esc(`$${unesc(src)}$`); }
    });
    let sec = null, body = '';
    for (const n of inOrder(kinds)) {
      const a = n.anchor, g = groupOf(a), said = n.kind === 'ask' ? turnsOf(n).filter((x) => x.a) : [];
      if (g && g !== sec) { sec = g; body += `<h2>${esc(g)}</h2>`; }
      const whole_ = para && paraOf(a);
      body += `<article class="n ${kindOf(n)}${n.kind === 'ask' ? '' : toneCls(n)}">${whole_ && !a.shown ? `<blockquote class="para">${whole_}</blockquote>` : passage(a) ? `<blockquote${a.shown ? ` class="ai l${a.level}"` : ''}>${texLite(passage(a))}</blockquote>` : ''}` +
        (a.shown && !said.length ? `<p class="at">${esc(lineAt(a))}</p>` : '') +
        (n.text && n.text.trim() ? `<div class="t">${esc(n.text.trim()).replace(/\n/g, '<br>')}</div>` : '') +
        said.map((x) => `<div class="qa"><p class="q">${esc(x.q)}</p><div class="a">${mdLite(answerText(x.a))}</div></div>`).join('') +
        (said.length ? `<p class="at">${esc(whole(a) ? t.n_whole : askedAt(a))}${said[said.length - 1].model ? ` · ${esc(said[said.length - 1].model)}` : ''}</p>` : '') + '</article>';
    }
    const src = m.abs_url ? `<a href="${esc(m.abs_url)}">${esc(sourceName(m))}</a>` : esc(sourceName(m));
    const lic = m.license ? ` · <a href="${esc(m.license)}">${esc(licenseName(m.license, t))}</a>` : '';
    return `<!doctype html>
<html lang="${esc(I18N.htmlLang(ui))}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(m.title)} · ${esc(t.n_notes)}</title>
<style>${MARKS_CSS}${hueCss()}</style>
</head>
<body>
<div class="wrap">
<a class="brand" href="${REPO}">${LOGO}<span>${BRAND}</span></a>
<header><h1>${esc(m.title)}</h1>${(m.authors || []).length ? `<p class="by">${esc(m.authors.map(flipName).join(', '))}</p>` : ''}
<p class="src">${src}${lic} · ${esc(t.n_notes)} · ${new Date().toISOString().slice(0, 10)}</p></header>
<main>${math(body)}</main>
<footer><a href="${REPO}">${LOGO}<span>${esc(t.sv_madewith)}</span></a></footer>
</div>
</body>
</html>
`;
  }
  // the colours of the levels in an export, as the reader has them now (reader.css: the same rules)
  function hueCss() {
    const lv = (k) => Object.entries(type.hl).map(([g, c]) => `--lv-${g}: ${HUES[c][k]}; --lv-${g}-ink: ${HUES[c][k + 1]};`).join(' ');
    return `
:root { ${lv(0)} --hl: var(--lv-src); --hl-ink: var(--lv-src-ink); --surface: var(--bg); }
@media (prefers-color-scheme: dark) { :root { ${lv(2)} } }
.tn-src { --t: var(--lv-src); --ti: var(--lv-src-ink); } .tn-key { --t: var(--lv-key); --ti: var(--lv-key-ink); } .tn-take { --t: var(--lv-take); --ti: var(--lv-take-ink); }
.tn-src, .tn-key, .tn-take { --hl: var(--t); --hl-ink: var(--ti); }
.tn-src.sh1, .tn-key.sh1, .tn-take.sh1 { --hl: color-mix(in oklab, var(--t) 45%, var(--surface)); }
.tn-src.sh3, .tn-key.sh3, .tn-take.sh3 { --hl: color-mix(in oklab, var(--t) 68%, var(--ti)); }`;
  }
  const MARKS_CSS = `
:root { --bg: #FAF9F7; --card: #fff; --ink: #1A1A1A; --ink-2: #4A4540; --muted: #6B6560; --line: #E9E5DF; --accent: #6B5CE7; color-scheme: light dark; }
@media (prefers-color-scheme: dark) { :root { --bg: #1C1B19; --card: #252421; --ink: #ECE8E1; --ink-2: #C9C3B9; --muted: #9A938A; --line: #3A3732; --accent: #A99CF5; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 16px/1.6 -apple-system, 'Inter', 'PingFang SC', 'Segoe UI', sans-serif; }
.wrap { max-width: 720px; margin: 0 auto; padding: 28px 16px 48px; }
a { color: var(--accent); }
.brand { display: inline-flex; align-items: center; gap: 8px; font-weight: 700; font-size: 15px; color: var(--ink); text-decoration: none; }
.brand svg, footer svg { width: 20px; height: 20px; flex: none; }
header { margin: 28px 0 24px; }
h1 { font-size: 28px; line-height: 1.25; margin: 0 0 8px; letter-spacing: -.01em; }
.by, .src { margin: 2px 0; color: var(--muted); font-size: 14px; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); margin: 32px 0 10px; }
.n { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; margin: 10px 0; }
blockquote { margin: 0; padding: 2px 0 2px 12px; border-left: 3px solid var(--hl-ink); font-family: 'Source Serif 4', Georgia, 'Songti SC', serif; font-size: 17px; color: var(--ink); }
.n.hl blockquote:not(.para) { background: color-mix(in srgb, var(--hl) 35%, transparent); border-radius: 0 6px 6px 0; }
blockquote.ai { font-family: inherit; font-size: 15.5px; }   /* the model's words, as on its line */
blockquote.para { font-size: 15.5px; color: var(--ink-2); border-left-color: var(--line); }
mark { background: color-mix(in srgb, var(--hl) 70%, transparent); color: var(--ink); padding: 0 1px; border-radius: 2px; }
.t { margin-top: 10px; white-space: normal; }
.qa { margin-top: 10px; }
.q { font-weight: 650; margin: 0 0 4px; }
.a { color: var(--ink-2); }
.a p { margin: 6px 0; }
.a ul { margin: 6px 0; padding-left: 20px; }
.at { margin: 8px 0 0; font-size: 12.5px; color: var(--muted); }
footer { margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--line); font-size: 13px; }
footer a { display: inline-flex; align-items: center; gap: 8px; color: var(--muted); text-decoration: none; }
math { font-size: 1.05em; }
`;

  // the paragraph's marks and cards follow the language
  function notesForLang() {
    madeLabel();
    for (const key of notesOf.keys()) renderNoteCards(key);
    updateNotesBtn();
    chatLabel();
    cmpLabel();
    if (!chat.hidden) openChat(chatNote);
  }

  // ── beside the original pages: the paper's PDF at the left (fetched once by the app, from arXiv or PubMed Central),
  //    each paragraph found on it by its own words (the paper's language, whatever language it is read in). A click
  //    on a paragraph shows where it is on its page; reading on carries the pages along; a click on a page goes to its
  //    paragraph ──
  const PDF_OK = (!!D.app && /^(\d{4}\.\d{4,5}|pmc\d+|ppr\d+|(biorxiv|medrxiv)-[0-9.]+|pdf-[0-9a-f]{16})$/.test(D.meta.id))   // (pdf.has_source)
    || !!(D.pdf && D.pdfjs)   // a saved page that carries its PDF, and PDF.js to show it
    || !!D.pdfAt;   // a gallery's page: the PDF and PDF.js beside it, on the same host
  const CMP_KEY = 'pf-compare';
  // the pane's width: dragged at its right edge (or with the arrow keys there) and kept for next time, the reader's place
  // held while the text reflows beside it; a double-click goes back to the default (46% of the window, at most 860 px)
  const PDFW = 'textfold.pdfw';
  const keptWidth = () => { try { return +localStorage.getItem(PDFW) || 0; } catch { return 0; } };
  function setPdfWidth(w) {   // the pane from 280 px, the text beside it at least 420
    const rail = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--rail')) || 0;
    document.documentElement.style.setProperty('--pdfw', `${Math.round(Math.max(280, Math.min(w, innerWidth - rail - 420)))}px`);
  }
  function keepWidth(pane) { try { localStorage.setItem(PDFW, String(Math.round(pane.getBoundingClientRect().width))); } catch { /* private window */ } }
  function gripFor(pane) {
    const grip = h('div', 'pp-grip');
    grip.setAttribute('role', 'separator');
    grip.setAttribute('aria-orientation', 'vertical');
    grip.tabIndex = 0;
    grip.title = T[ui].sb_resize;
    grip.setAttribute('aria-label', T[ui].sb_resize);
    grip.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      grip.setPointerCapture(e.pointerId);
      document.documentElement.classList.add('pp-resizing');
      const keep = unitAtReading(), top = keep && keep.getBoundingClientRect().top, left = pane.getBoundingClientRect().left;
      let x = null, frame = 0;
      const apply = () => {
        frame = 0;
        setPdfWidth(x - left);
        if (cmp && cmp.pages.length) sizePages();   // (the pages stretch until the drag ends, then are drawn again)
        if (keep && keep.isConnected) scrollBy(0, keep.getBoundingClientRect().top - top);
      };
      const move = (ev) => { x = ev.clientX; if (!frame) frame = requestAnimationFrame(apply); };
      const up = () => {
        grip.removeEventListener('pointermove', move); grip.removeEventListener('pointerup', up); grip.removeEventListener('pointercancel', up);
        if (frame) { cancelAnimationFrame(frame); apply(); }
        document.documentElement.classList.remove('pp-resizing');
        keepWidth(pane);
        reflowKeeping(keep);   // the reader's own layout at the new width, and the pages drawn sharp
      };
      grip.addEventListener('pointermove', move); grip.addEventListener('pointerup', up); grip.addEventListener('pointercancel', up);
    });
    grip.addEventListener('dblclick', () => {
      const keep = unitAtReading();
      try { localStorage.removeItem(PDFW); } catch { /* private window */ }
      document.documentElement.style.removeProperty('--pdfw');
      reflowKeeping(keep);
    });
    grip.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      const keep = unitAtReading();
      setPdfWidth(pane.getBoundingClientRect().width + (e.key === 'ArrowLeft' ? -24 : 24));
      keepWidth(pane);
      reflowKeeping(keep);
    });
    return grip;
  }
  const cmpBtn = h('button', 'cmpbtn');
  cmpBtn.type = 'button';
  cmpBtn.hidden = !PDF_OK;
  npick.before(cmpBtn);
  let cmp = null;   // while open: the pane, the pages, every word on them with its boxes, and each paragraph's place
  const pdfNorm = (s) => s.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
  function cmpLabel() {
    cmpBtn.textContent = T[ui].compare;
    cmpBtn.title = T[ui].compare_tip;
    cmpBtn.classList.toggle('on', !!cmp);
    cmpBtn.setAttribute('aria-pressed', cmp ? 'true' : 'false');
  }
  cmpLabel();
  cmpBtn.addEventListener('click', () => { if (cmp) closeCompare(); else openCompare(); });

  // the paragraph being read: where it is on screen now, to keep it there while the column changes width
  function unitAtReading() {
    const r = docEl.getBoundingClientRect();
    for (const f of [0.3, 0.36, 0.42, 0.24]) {
      const el = document.elementFromPoint(r.left + Math.min(r.width / 2, 320), innerHeight * f);
      const fig = el && el.closest('figure.fig');
      const u = el && (el.closest('.u[data-u]') || (fig && fig.querySelector('figcaption.u')));
      if (u) return u;
    }
    return null;
  }
  function reflowKeeping(u) {
    const top = u && u.getBoundingClientRect().top;
    dispatchEvent(new Event('resize'));   // the reader's own: snaps the zoom, lays the margins out again
    requestAnimationFrame(() => requestAnimationFrame(() => { if (u && u.isConnected) scrollBy(0, u.getBoundingClientRect().top - top); }));
  }

  async function openCompare() {
    if (cmp || !PDF_OK) return;
    const keep = unitAtReading();
    const pane = h('aside', 'pdfpane'), bar = h('div', 'pp-bar'), title = h('span', 'pp-t'), count = h('span', 'pp-n');
    const x = h('button', 'pp-x', '×'), scroller = h('div', 'pp-scroll');
    x.type = 'button';
    x.setAttribute('aria-label', T[ui].close);
    pane.setAttribute('aria-label', T[ui].compare_tip);
    const src = { pmc: 'PubMed Central', ppr: 'Preprint', biorxiv: 'bioRxiv', medrxiv: 'medRxiv' }[D.meta.id.match(/^[a-z]+/)?.[0]];
    const own = D.meta.id.startsWith('pdf-');   // a PDF read through the engine: the file itself, not one fetched for it
    title.textContent = own ? (D.meta.label || 'PDF') : src ? `${src} · PDF` : `arXiv · PDF ${D.meta.version || ''}`.trim();
    bar.append(title, count, x);
    pane.append(bar, scroller, gripFor(pane));
    if (keptWidth()) setPdfWidth(keptWidth());
    document.body.append(pane);
    cmp = { pane, scroller, count, pages: [], words: [], grams: null, spans: new Map(), current: null, follow: null, epoch: 0 };
    x.addEventListener('click', closeCompare);
    scroller.addEventListener('scroll', () => requestAnimationFrame(pageCount), { passive: true });
    scroller.addEventListener('click', onPageClick);
    document.documentElement.classList.add('compare');
    try { localStorage.setItem(CMP_KEY, '1'); } catch { /* private window */ }
    cmpLabel();
    reflowKeeping(keep);
    // a PDF dropped on the pane (one the server could not fetch): kept by the server as the paper's, then read here.
    // The page's own drop (a Markdown file to open) does not see it.
    for (const t of ['dragenter', 'dragover', 'dragleave']) pane.addEventListener(t, (e) => { e.stopPropagation(); if (t !== 'dragleave') e.preventDefault(); });
    pane.addEventListener('drop', (e) => {
      e.preventDefault(); e.stopPropagation();
      const f = e.dataTransfer.files[0];
      if (f && D.app) givePdf(pane, f);   // (only the app can keep it)
    });
    loadPdf(pane);
  }
  async function loadPdf(pane) {
    const msg = h('div', 'pp-msg');
    msg.textContent = T[ui].pdf_loading;
    cmp.scroller.replaceChildren(msg);
    try {
      let lib, data;
      if (D.pdf && D.pdfjs) {   // a saved page: PDF.js and the file from inside it
        const js = (text) => URL.createObjectURL(new Blob([text], { type: 'text/javascript' }));
        lib = await import(js(D.pdfjs.lib));
        lib.GlobalWorkerOptions.workerSrc = js(D.pdfjs.worker);
        data = Uint8Array.from(atob(D.pdf), (c) => c.charCodeAt(0));
      } else if (D.pdfAt) {   // a gallery's page: the file and PDF.js next to it
        const at = (p) => new URL(p, location.href).href;
        lib = await import(at(`${D.pdfAt.pdfjs}pdf.min.mjs`));
        lib.GlobalWorkerOptions.workerSrc = at(`${D.pdfAt.pdfjs}pdf.worker.min.mjs`);
        const r = await fetch(at(D.pdfAt.file));
        if (!r.ok) throw Object.assign(new Error('no pdf'), { open: D.meta.abs_url });
        data = new Uint8Array(await r.arrayBuffer());
      } else {
        lib = await import('/static/vendor/pdfjs/pdf.min.mjs');
        lib.GlobalWorkerOptions.workerSrc = '/static/vendor/pdfjs/pdf.worker.min.mjs';
        const r = await fetch(`/api/pdf/${encodeURIComponent(D.meta.id)}`);
        if (!r.ok) throw Object.assign(new Error('no pdf'), { open: (await r.json().catch(() => ({}))).open });
        data = new Uint8Array(await r.arrayBuffer());
      }
      const loading = lib.getDocument({ data, isEvalSupported: false });
      const doc = await loading.promise;
      if (!cmp || cmp.pane !== pane) { loading.destroy(); return; }
      Object.assign(cmp, { lib, doc, loading, pages: [], spans: new Map() });
      await layPages();
      msg.remove();
      await indexWords();
      await layOut();
      // a PDF whose words are not this paper's (another paper dropped by mistake): said, and another can be dropped.
      // (A PDF read through the engine is the text's own file: never another's.)
      const ids = [...order.keys()];
      if (!D.meta.id.startsWith('pdf-') && ids.length && ids.filter((id) => locate(id)).length < ids.length * 0.3) cmp.scroller.prepend(h('div', 'pp-warn', esc(T[ui].pdf_mismatch)));
      cmp.follow = null;
      followNow();
    } catch (e) {
      if (cmp && cmp.pane === pane) offerPdf(msg, e.open);
    }
  }
  // none to show: where to open it, and a place to drop it once downloaded
  function offerPdf(msg, open) {
    const t = T[ui];
    msg.replaceChildren(h('p', null, esc(open ? t.pdf_none : t.pdf_error)));
    if (open) {
      const a = h('a', null, `${esc(t.pdf_open)} ↗`);
      a.href = open; a.target = '_blank'; a.rel = 'noopener';
      const p = h('p');
      p.append(a);
      msg.append(p);
    }
    if (!D.app) return;   // a page that is a file keeps no PDF given to it
    const drop = h('div', 'pp-drop', esc(t.pdf_drop)), pick = h('button', 'pp-pick', esc(t.pdf_pick)), input = document.createElement('input');
    pick.type = 'button';
    input.type = 'file'; input.accept = 'application/pdf,.pdf'; input.hidden = true;
    pick.addEventListener('click', () => input.click());
    input.addEventListener('change', () => { if (input.files[0]) givePdf(cmp.pane, input.files[0]); });
    drop.append(pick, input);
    msg.append(drop);
  }
  async function givePdf(pane, file) {
    if (!cmp || cmp.pane !== pane) return;
    const msg = h('div', 'pp-msg');
    msg.textContent = T[ui].pdf_saving;
    cmp.scroller.replaceChildren(msg);
    const r = await fetch(`/api/pdf/${encodeURIComponent(D.meta.id)}`, { method: 'POST', headers: { 'Content-Type': 'application/pdf' }, body: file }).catch(() => null);
    if (r && r.ok) loadPdf(pane);
    else { msg.replaceChildren(h('p', null, esc(T[ui].pdf_bad))); offerPdf(msg, null); }
  }
  function closeCompare() {
    if (!cmp) return;
    const keep = unitAtReading();
    if (cmp.io) cmp.io.disconnect();
    if (cmp.loading) cmp.loading.destroy();   // the document and its worker
    cmp.pane.remove();
    cmp = null;
    document.documentElement.classList.remove('compare');
    document.documentElement.style.removeProperty('--pdfw');
    try { localStorage.setItem(CMP_KEY, '0'); } catch { /* private window */ }
    cmpLabel();
    reflowKeeping(keep);
  }

  // the pages: sized at once (the pane scrolls through all of them), drawn when they come near
  async function layPages() {
    const { doc, scroller } = cmp;
    if (cmp.io) cmp.io.disconnect();
    for (let n = 1; n <= doc.numPages; n++) {
      const page = await doc.getPage(n), vp = page.getViewport({ scale: 1 });
      const el = h('div', 'pp-page'), canvas = document.createElement('canvas'), marks = h('div', 'pp-marks');
      el.dataset.n = n;
      el.append(canvas, marks);
      scroller.append(el);
      cmp.pages.push({ n, page, vp, el, canvas, marks, drawn: 0, task: null });
    }
    sizePages();
    cmp.io = new IntersectionObserver((es) => { for (const e of es) if (e.isIntersecting) drawPage(cmp.pages[+e.target.dataset.n - 1]); },
      { root: scroller, rootMargin: '800px 0px' });
    for (const p of cmp.pages) cmp.io.observe(p.el);
    pageCount();
  }
  function sizePages() {
    const width = cmp.scroller.clientWidth - 24, widest = Math.max(...cmp.pages.map((p) => p.vp.width));
    cmp.scale = Math.max(0.3, width / widest);
    cmp.epoch++;
    for (const p of cmp.pages) {
      p.el.style.width = `${Math.round(p.vp.width * cmp.scale)}px`;
      p.el.style.height = `${Math.round(p.vp.height * cmp.scale)}px`;
    }
  }
  async function drawPage(p) {
    if (!cmp || !p || p.drawn === cmp.epoch) return;
    const epoch = cmp.epoch, dpr = Math.min(devicePixelRatio || 1, 2), vp = p.page.getViewport({ scale: cmp.scale * dpr });
    p.drawn = epoch;
    if (p.task) p.task.cancel();
    const c = p.canvas;
    c.width = Math.floor(vp.width);
    c.height = Math.floor(vp.height);
    p.task = p.page.render({ canvasContext: c.getContext('2d'), viewport: vp });
    try { await p.task.promise; } catch { if (p.drawn === epoch) p.drawn = 0; }
    p.task = null;
    paintMarks(p);
  }
  function pageCount() {
    if (!cmp || !cmp.pages.length) return;
    const top = cmp.scroller.scrollTop + cmp.scroller.clientHeight * 0.3;
    const p = cmp.pages.find((q) => q.el.offsetTop + q.el.offsetHeight > top) || cmp.pages[cmp.pages.length - 1];
    cmp.count.textContent = `${p.n} / ${cmp.pages.length}`;
  }

  // every word on the pages, in reading order, with its boxes (scale 1): a word broken by a hyphen at a line's end is
  // one word, and so is a word the PDF wrote in two pieces
  async function indexWords() {
    const words = [];
    for (const p of cmp.pages) {
      const tc = await p.page.getTextContent();
      let lastY = null, lastEnd = null;
      for (const it of tc.items) {
        if (!it.str || !it.str.trim()) { lastEnd = null; continue; }
        const tx = cmp.lib.Util.transform(p.vp.transform, it.transform);
        const fh = Math.hypot(tx[2], tx[3]) || it.height || 8, x = tx[4], base = tx[5], s = it.str, L = s.length;
        const newLine = lastY === null || Math.abs(base - lastY) > fh * 0.5;
        lastY = base;
        const re = /\S+/g;
        let m, first = true;
        while ((m = re.exec(s))) {
          const x0 = x + it.width * (m.index / L), x1 = x + it.width * ((m.index + m[0].length) / L);
          const box = { x: x0, y: base - fh * 0.82, w: x1 - x0, h: fh * 1.08 };
          const w = pdfNorm(m[0]), prev = words[words.length - 1];
          const glued = first && m.index === 0 && !newLine && lastEnd !== null && x0 - lastEnd < fh * 0.12;   // two pieces of one word
          const broken = first && newLine && prev && prev.hyph && prev.page === p.n;                          // net- / work
          first = false;
          if (!w) continue;
          if ((glued || broken) && prev) { prev.w += w; prev.boxes.push(box); prev.hyph = /[-‐]$/.test(m[0]); continue; }
          words.push({ w, page: p.n, boxes: [box], hyph: /[-‐]$/.test(m[0]) });
        }
        lastEnd = /\s$/.test(s) ? null : x + it.width;
      }
    }
    const grams = new Map();
    for (let i = 0; i + 2 < words.length; i++) {
      const k = `${words[i].w} ${words[i + 1].w} ${words[i + 2].w}`;
      const a = grams.get(k);
      if (a) a.push(i); else grams.set(k, [i]);
    }
    Object.assign(cmp, { words, grams });
  }

  // a PDF read through the engine: each paragraph's place on its page, as the engine found the block it came from.
  // A scan has no words of its own to search, and even a PDF with words is found more exactly this way.
  async function layOut() {
    cmp.layout = null;
    if (!D.meta.id.startsWith('pdf-')) return;
    const r = D.pdfmap ? null : await fetch(`/api/pdfmap/${encodeURIComponent(D.meta.id)}`).catch(() => null);
    const map = D.pdfmap || (r && r.ok ? await r.json() : {});
    if (!cmp || !Object.keys(map).length) return;
    cmp.layout = new Map();
    for (const [uid, at] of Object.entries(map)) {
      const p = cmp.pages[at.page - 1];
      if (!p) continue;
      const W = p.vp.width, H = p.vp.height;
      const boxes = at.boxes.map(([x0, y0, x1, y1]) => ({ page: at.page, x: x0 * W, y: y0 * H, w: (x1 - x0) * W, h: (y1 - y0) * H }));
      cmp.layout.set(uid, { page: at.page, boxes, area: boxes.reduce((a, b) => a + b.w * b.h, 0) });
    }
  }
  // where a paragraph is: its runs of three words found on the pages, the run of agreeing places they vote for, and
  // the words from its first match to its last (formula symbols on the page sit between them, so a place may drift)
  function locate(uid) {
    if (cmp.layout) return cmp.layout.get(uid) || null;
    if (cmp.spans.has(uid)) return cmp.spans.get(uid);
    const u = U[uid], uw = u ? u.toks.filter((t) => t.t).map((t) => pdfNorm(t.t)).filter(Boolean) : [];
    const hitsOf = (i) => { const a = cmp.grams.get(`${uw[i]} ${uw[i + 1]} ${uw[i + 2]}`); return a && a.length <= 30 ? a : null; };
    let span = null;
    if (uw.length >= 3) {
      const votes = new Map();
      for (let i = 0; i + 2 < uw.length; i++) for (const j of hitsOf(i) || []) { const k = Math.round((j - i) / 8); votes.set(k, (votes.get(k) || 0) + 1); }
      let k0 = null, n0 = 0;
      for (const [k, n] of votes) { const all = n + (votes.get(k - 1) || 0) + (votes.get(k + 1) || 0); if (all > n0) { n0 = all; k0 = k; } }
      if (k0 !== null && n0 >= Math.max(2, Math.min(5, Math.floor((uw.length - 2) * 0.25)))) {
        let lo = -1, hi = -1, lastI = 0;
        for (let i = 0; i + 2 < uw.length; i++) {
          for (const j of hitsOf(i) || []) {
            const ok = lo < 0 ? Math.abs(Math.round((j - i) / 8) - k0) <= 1 : j > hi - 2 && j - hi <= (i - lastI) + 40;
            if (ok) { if (lo < 0) lo = j; hi = j + 2; lastI = i; break; }
          }
        }
        if (lo >= 0) span = { lo, hi, page: cmp.words[lo].page, boxes: boxesOf(lo, hi) };
      }
    }
    cmp.spans.set(uid, span);
    return span;
  }
  function boxesOf(lo, hi) {   // the words' boxes, one per line
    const lines = [];
    for (let i = lo; i <= hi; i++) {
      const w = cmp.words[i];
      for (const b of w.boxes) {
        const line = lines.find((l) => l.page === w.page && Math.abs(l.y + l.h / 2 - (b.y + b.h / 2)) < b.h * 0.45 && b.x < l.x + l.w + b.h * 3 && b.x + b.w > l.x - b.h * 3);
        if (line) {
          const x1 = Math.max(line.x + line.w, b.x + b.w), y1 = Math.max(line.y + line.h, b.y + b.h);
          line.x = Math.min(line.x, b.x); line.y = Math.min(line.y, b.y); line.w = x1 - line.x; line.h = y1 - line.y;
        } else lines.push({ page: w.page, x: b.x, y: b.y, w: b.w, h: b.h });
      }
    }
    return lines;
  }

  // a paragraph shown on its page: the pane scrolls to it when it is out of sight (or always, when picked)
  function showSpan(span, how) {
    const before = cmp.current;
    cmp.current = span;
    cmp.picked = how === 'pick';
    for (const p of cmp.pages) if ((before && before.boxes.some((b) => b.page === p.n)) || span.boxes.some((b) => b.page === p.n)) paintMarks(p);
    if (how === 'stay') return;
    const b = span.boxes[0], p = cmp.pages[b.page - 1], sc = cmp.scroller;
    const y = p.el.offsetTop + b.y * cmp.scale, last = span.boxes[span.boxes.length - 1];
    const yEnd = cmp.pages[last.page - 1].el.offsetTop + (last.y + last.h) * cmp.scale;
    const seen = y >= sc.scrollTop + 40 && yEnd <= sc.scrollTop + sc.clientHeight - 20;
    if (how === 'pick' || !seen) sc.scrollTo({ top: y - sc.clientHeight * 0.28, behavior: how === 'pick' && !reduced ? 'smooth' : 'auto' });
  }
  function paintMarks(p) {
    if (!cmp) return;
    const s = cmp.scale, span = cmp.current;
    p.marks.replaceChildren(...(span ? span.boxes.filter((b) => b.page === p.n) : []).map((b) => {
      const m = h('div', `pp-mark${cmp.picked ? ' pick' : ''}`);
      m.style.cssText = `left:${(b.x - 2) * s}px;top:${(b.y - 1) * s}px;width:${(b.w + 4) * s}px;height:${(b.h + 2) * s}px`;
      return m;
    }));
  }

  // reading on: the paragraph at the reading line, shown on its page
  function followNow() {
    if (!cmp || !cmp.grams) return;
    const u = unitAtReading();
    if (!u || u.dataset.u === cmp.follow) return;
    cmp.follow = u.dataset.u;
    const span = locate(u.dataset.u);
    if (span) showSpan(span, 'follow');
  }
  let followTimer = null;
  addEventListener('scroll', () => { if (cmp) { clearTimeout(followTimer); followTimer = setTimeout(followNow, 140); } }, { passive: true });
  addEventListener('resize', () => {
    if (cmp && keptWidth()) setPdfWidth(keptWidth());   // (a narrower window: the pane gives way, the text keeps its room)
    if (!cmp || !cmp.pages.length) return;
    clearTimeout(cmp.resizeTimer);
    cmp.resizeTimer = setTimeout(() => {
      if (!cmp) return;
      sizePages();
      const sc = cmp.scroller;
      for (const p of cmp.pages) if (p.el.offsetTop < sc.scrollTop + sc.clientHeight + 800 && p.el.offsetTop + p.el.offsetHeight > sc.scrollTop - 800) drawPage(p);
    }, 150);
  });
  // a paragraph clicked: its place on the page
  docEl.addEventListener('click', (e) => {
    if (!cmp || !cmp.grams) return;
    const fig = e.target.closest('figure.fig'), u = e.target.closest('.u[data-u]') || (fig && fig.querySelector('figcaption.u'));
    if (!u) return;
    const span = locate(u.dataset.u);
    cmp.follow = u.dataset.u;
    if (span) showSpan(span, 'pick');
  });
  // a page clicked: the paragraph there (the narrowest that holds the word under the pointer)
  function onPageClick(e) {
    const el = e.target.closest('.pp-page');
    if (!el || !cmp.grams) return;
    const n = +el.dataset.n, r = el.getBoundingClientRect(), px = (e.clientX - r.left) / cmp.scale, py = (e.clientY - r.top) / cmp.scale;
    if (cmp.layout) {   // the smallest paragraph whose place holds the point
      let hit = null;
      for (const [uid, s] of cmp.layout) {
        if (s.boxes.some((b) => b.page === n && px >= b.x - 4 && px <= b.x + b.w + 4 && py >= b.y - 4 && py <= b.y + b.h + 4) && (!hit || s.area < hit.s.area)) hit = { uid, s };
      }
      if (!hit) return;
      cmp.follow = hit.uid;
      showSpan(hit.s, 'stay');
      const target = unitEl.get(hit.uid);
      if (target) go(target.closest('figure.fig') || target, null);
      return;
    }
    let at = -1, best = Infinity;
    cmp.words.forEach((w, i) => {
      if (w.page !== n) return;
      for (const b of w.boxes) {
        const dx = Math.max(b.x - px, 0, px - b.x - b.w), dy = Math.max(b.y - py, 0, py - b.y - b.h), d = dx * dx + 4 * dy * dy;
        if (d < best) { best = d; at = i; }
      }
    });
    if (at < 0 || best > 900) return;
    let hit = null;
    for (const uid of order.keys()) {
      const s = locate(uid);
      if (s && s.lo <= at && at <= s.hi && (!hit || s.hi - s.lo < hit.s.hi - hit.s.lo)) hit = { uid, s };
    }
    if (!hit) return;
    cmp.follow = hit.uid;
    showSpan(hit.s, 'stay');
    const target = unitEl.get(hit.uid);
    if (target) go(target.closest('figure.fig') || target, null);
  }

  // ── the legend, both ways: hovering a kind lights its paragraphs' dots (and on the map, its chips) while the rest
  //    step back; hovering a paragraph lights its kind. A click on a kind keeps it lit until clicked again (or Esc).
  let rolePinned = null;
  const legendEl = document.getElementById('legend');
  function legendFocus(role, pin = false) {
    if (pin) rolePinned = role;
    const r = role || rolePinned;
    if (r) docEl.dataset.role = r; else delete docEl.dataset.role;
    legendEl.querySelectorAll('[data-role]').forEach((it) => it.classList.toggle('pin', it.dataset.role === rolePinned));
  }
  function legendMark(role) { legendEl.querySelectorAll('[data-role]').forEach((it) => it.classList.toggle('on', it.dataset.role === role)); }
  legendEl.addEventListener('mouseover', (e) => { const it = e.target.closest('[data-role]'); if (it) legendFocus(it.dataset.role); });
  legendEl.addEventListener('mouseleave', () => legendFocus(null));
  legendEl.addEventListener('click', (e) => {
    const it = e.target.closest('[data-role]');
    if (it) legendFocus(rolePinned === it.dataset.role ? null : it.dataset.role, true);
  });

  // ── chrome: the bar, the legend, the language ───────────────────────
  // The bar is a slider, in the manner of a segmented control: a raised thumb sits under the level, follows the
  // zoom position continuously, and follows the finger while dragged (dragging scrubs the zoom itself). On
  // release it springs to the nearer level. A click jumps.
  const bar = document.getElementById('zoombar');
  const thumb = h('span', 'zthumb');
  bar.append(thumb);
  BAR.forEach((L, i) => {
    if (i) bar.append(Object.assign(h('span', 'sep'), { textContent: '›' }));
    const b = h('button'); b.type = 'button'; b.dataset.l = L;
    b.title = `${i + 1}`;
    b.addEventListener('click', (e) => { if (e.detail === 0) setLevel(L); });   // the keyboard's Enter (pointer clicks: see pointerup)
    bar.append(b);
  });
  const barButtons = () => [...bar.querySelectorAll('button')];
  let geom = null;   // centres and sizes of the labels, measured once per layout
  function measureBar() {
    const bars = bar.getBoundingClientRect();
    geom = barButtons().map((b) => { const r = b.getBoundingClientRect(); return { l: +b.dataset.l, left: r.left - bars.left, width: r.width, centre: r.left + r.width / 2 }; });
  }
  function placeThumb() {
    if (!geom) measureBar();
    const i = 4 - pos, lo = Math.max(0, Math.min(4, Math.floor(i))), hi = Math.min(4, lo + 1), f = i - lo;
    const a = geom[lo], b = geom[hi];
    if (!a || !b) return;
    const x = a.left + (b.left - a.left) * f, w = a.width + (b.width - a.width) * f;
    thumb.style.transform = `translateX(${x}px)`;
    thumb.style.width = `${w}px`;
    const on = Math.round(pos);
    barButtons().forEach((btn) => btn.classList.toggle('on', +btn.dataset.l === on));
  }
  function posAt(x) {   // the zoom position under a point of the bar
    if (!geom) measureBar();
    const c = geom.map((g) => g.centre);
    if (x <= c[0]) return 4;
    if (x >= c[4]) return 0;
    const i = c.findIndex((v, k) => x >= v && x <= c[k + 1]);
    return 4 - i - (x - c[i]) / (c[i + 1] - c[i]);
  }
  let drag = null;
  bar.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || (busy && !tr && !stage)) return;
    bar.setPointerCapture(e.pointerId);
    bar.classList.add('dragging');
    measureBar();
    drag = { x0: e.clientX, moved: false };
    clearTimeout(snapTimer);
  });
  bar.addEventListener('pointermove', (e) => {
    if (!drag) return;
    if (!drag.moved && Math.abs(e.clientX - drag.x0) > 3) { drag.moved = true; holding = true; aimLevel = null; gesturePoint = null; }
    if (drag.moved) { goal = posAt(e.clientX); kick(); }
  });
  const endDrag = (e) => {
    if (!drag) return;
    const moved = drag.moved;
    drag = null;
    bar.classList.remove('dragging');
    if (moved) release(); else setLevel(Math.round(posAt(e.clientX)));   // a click on a label
  };
  bar.addEventListener('pointerup', endDrag);
  bar.addEventListener('pointercancel', endDrag);
  bar.setAttribute('role', 'slider');
  bar.setAttribute('aria-valuemin', '0');
  bar.setAttribute('aria-valuemax', '4');
  function updateBar() {
    findStale();
    if (mapReady) { tview.hidden = level !== 0; mapSync(); savePosSoon(); }
    barButtons().forEach((b) => b.setAttribute('aria-pressed', String(+b.dataset.l === level)));
    bar.setAttribute('aria-valuenow', String(level));
    bar.setAttribute('aria-valuetext', T[ui].levels[level]);
    placeThumb();
  }
  const legend = document.getElementById('legend');
  const tagline = document.querySelector('.tagline');
  const usedRoles = new Set(Object.values(U).map((u) => u.lad.role));
  // the language menu: generated languages switch in place; in the app, the others can be generated, and the
  // current one regenerated (a new sample from the model replaces the stored version)
  const langBtn = document.getElementById('langbtn');
  const langMenu = document.getElementById('langmenu');
  function renderLangMenu() {
    const t = T[ui];
    langMenu.replaceChildren();
    if (D.app && STALE.has(lang)) {   // first: a translation built on an earlier English, made again of this one (the
      // replies kept that still fit are used again: no new sample, so nothing is asked first, as Regenerate asks)
      const f = h('button', 'lm-item lm-regen lm-fill'); f.type = 'button';
      f.innerHTML = `<span>↻ ${esc(t.stale_fill)}</span><em>${esc(nameOf(lang))}</em>`;
      f.addEventListener('click', () => { closeLangMenu(); window.DRApp.generate(lang, false); });
      langMenu.append(f);
    }
    if (D.app && gapTag(lang, t)) {   // first: the paragraphs left untranslated, asked for again (the rest stays as it is)
      const f = h('button', 'lm-item lm-regen lm-fill'); f.type = 'button';
      f.innerHTML = `<span>↻ ${esc(t.gaps_fill.replace('{n}', GAPS[lang]))}</span><em>${esc(nameOf(lang))}</em>`;
      f.addEventListener('click', () => { closeLangMenu(); window.DRApp.fill(lang); });
      langMenu.append(f);
    }
    for (const code of Object.keys(NAMES)) {
      const have = AVAIL.includes(code);
      if (!have && !D.app) continue;
      const b = h('button', `lm-item${code === lang ? ' on' : ''}${have ? '' : ' gen'}`);
      b.type = 'button';
      b.innerHTML = `<span>${esc(nameOf(code))}</span>${!have ? `<em>+ ${esc(t.gen_lang)}</em>` : STALE.has(code) ? `<em class="stale">${esc(t.stale)}</em>` : gapTag(code, t)}`;
      b.addEventListener('click', () => { closeLangMenu(); if (have) setLang(code); else window.DRApp.generate(code, false); });
      langMenu.append(b);
    }
    if (D.app) {
      const r = h('button', 'lm-item lm-regen'); r.type = 'button';
      r.innerHTML = `<span>↻ ${esc(t.regen)}</span><em>${esc(nameOf(lang))}</em>`;
      r.addEventListener('click', () => { closeLangMenu(); window.DRApp.generate(lang, true); });
      langMenu.append(r);
    }
  }
  function closeLangMenu() { langMenu.hidden = true; langBtn.setAttribute('aria-expanded', 'false'); }
  langBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    closeTypeMenu();
    if (!langMenu.hidden) return closeLangMenu();
    renderLangMenu(); langMenu.hidden = false; langBtn.setAttribute('aria-expanded', 'true');
  });
  addEventListener('click', (e) => { if (!langMenu.hidden && !e.target.closest('.langpick')) closeLangMenu(); });
  if (!D.app && AVAIL.length < 2) document.querySelector('.langpick').remove();

  // ── a figure, enlarged: a click on its picture opens it over the page with its caption (as the reader sees it now:
  //    this language, this level); Esc, a click beside it or × closes it. A picture larger than the screen opens to
  //    fit, and a click on it shows it at full size, to scroll ──
  const lightbox = h('div', 'lightbox');
  lightbox.hidden = true;
  lightbox.setAttribute('role', 'dialog');
  lightbox.setAttribute('aria-modal', 'true');
  document.body.append(lightbox);
  let lightboxFrom = null;
  function openLightbox(pic) {
    const fig = pic.closest('figure.fig'), cap = fig && fig.querySelector('figcaption');
    const shown = pic.cloneNode(true);
    shown.removeAttribute('style');
    shown.classList.add('lb-pic');
    if (shown.tagName.toLowerCase() === 'svg') { shown.removeAttribute('width'); shown.removeAttribute('height'); }
    const stage = h('div', 'lb-stage');
    stage.append(shown);
    const x = h('button', 'lb-x', '×');
    x.type = 'button';
    x.setAttribute('aria-label', T[ui].close);
    lightbox.replaceChildren(stage, x);
    if (cap) {
      const label = cap.querySelector('.flabel'), text = cap._tx ? cap._tx.innerText.trim() : '';
      const c = h('div', 'lb-cap');
      c.textContent = [label && label.textContent.trim(), text].filter(Boolean).join(' ');
      if (c.textContent) lightbox.append(c);
      lightbox.setAttribute('aria-label', c.textContent || 'Figure');
    }
    lightbox.classList.remove('lb-full');
    lightbox.hidden = false;
    document.documentElement.classList.add('lb-open');
    lightboxFrom = document.activeElement;
    x.focus({ preventScroll: true });
  }
  function closeLightbox() {
    lightbox.hidden = true;
    lightbox.replaceChildren();
    document.documentElement.classList.remove('lb-open');
    if (lightboxFrom && lightboxFrom.focus) lightboxFrom.focus({ preventScroll: true });
  }
  lightbox.addEventListener('click', (e) => {
    const pic = e.target.closest('.lb-pic');
    if (pic && pic.tagName.toLowerCase() === 'img') {   // larger than the screen: full size, to scroll; and back
      const fits = pic.naturalWidth <= pic.clientWidth + 2 && pic.naturalHeight <= pic.clientHeight + 2;
      if (!fits || lightbox.classList.contains('lb-full')) { lightbox.classList.toggle('lb-full'); return; }
    }
    if (!pic) closeLightbox();
  });

  // ── where this text was being read (kept in this browser, the last 300 texts): its level and the paragraph at the
  //    reading line, saved as the reader scrolls or zooms. Opened again (and with no place asked for in its link),
  //    it comes back there, says so, and offers the beginning ──
  const POS_KEY = 'textfold.pos';
  const readPos = () => { try { return JSON.parse(store.get(POS_KEY) || '{}') || {}; } catch { return {}; } };
  let posTimer = 0;
  function savePos() {
    if (busy || stage || mapShown) return;
    const a = defaultAnchor(), el = a && a.el;
    const uid = el && (el.dataset.u || (el.classList.contains('hd') && IDS[HEAD_IDX.get(el)]));
    if (!uid) return;
    const all = readPos();
    all[D.meta.id] = { u: uid, l: level, y: Math.round(unitEl.get(uid).getBoundingClientRect().top), t: Date.now() };
    const keep = Object.entries(all).sort((x, y) => y[1].t - x[1].t).slice(0, 300);
    store.set(POS_KEY, JSON.stringify(Object.fromEntries(keep)));
  }
  const savePosSoon = () => { clearTimeout(posTimer); posTimer = setTimeout(savePos, 700); };
  addEventListener('scroll', savePosSoon, { passive: true });
  addEventListener('pagehide', savePos);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') savePos(); });

  // a word over the page, for a while: where the text was opened, the focus just begun
  const pillEl = h('div', 'rpill');
  pillEl.hidden = true;
  pillEl.setAttribute('role', 'status');
  document.body.append(pillEl);
  let pillTimer = 0;
  function pill(html, ms = 2800) {
    pillEl.innerHTML = html;
    pillEl.hidden = false;
    clearTimeout(pillTimer);
    pillTimer = setTimeout(() => { pillEl.hidden = true; }, ms);
  }
  pillEl.addEventListener('click', (e) => { if (e.target.closest('[data-top]')) { pillEl.hidden = true; scrollTo({ top: 0, behavior: 'smooth' }); } });

  // ── focus (F, or the Aa menu): the text alone. The top bar, the zoom bar, the sidebar and the chat move off and
  //    come back while the pointer is at their edge; F or Esc ends it. Nothing in the column moves but for the room
  //    the sidebar's rail gave up (a resize: the margins and the keyframes laid out again) ──
  let focusOn = false;
  function setFocus(on) {
    if (on === focusOn) return;
    focusOn = on;
    const r = document.documentElement;
    r.classList.toggle('focus', on);
    r.classList.remove('show-top', 'show-bottom', 'show-left');
    if (on) pill(esc(T[ui].focus_on)); else { clearTimeout(pillTimer); pillEl.hidden = true; }
    dispatchEvent(new Event('resize'));
  }
  addEventListener('mousemove', (e) => {
    if (!focusOn) return;
    const r = document.documentElement, at = (sel) => !!(e.target.closest && e.target.closest(sel));
    r.classList.toggle('show-top', e.clientY < 70 || at('.topbar') || !typeMenu.hidden || !langMenu.hidden);
    r.classList.toggle('show-bottom', e.clientY > innerHeight - 96 || at('footer.zoom, .chatbtn'));
    r.classList.toggle('show-left', !!D.app && (e.clientX < 60 || at('#sidebar')));
  }, { passive: true });
  // (a finger has no hover: a touch at an edge brings that edge's bar back for a while, kept while its menu is open)
  let edgeTimer = 0;
  addEventListener('pointerdown', (e) => {
    if (!focusOn || e.pointerType === 'mouse') return;
    const r = document.documentElement, top = e.clientY < 70, bottom = e.clientY > innerHeight - 96, left = !!D.app && e.clientX < 40;
    if (!top && !bottom && !left) return;
    r.classList.toggle('show-top', top); r.classList.toggle('show-bottom', bottom); r.classList.toggle('show-left', left);
    clearTimeout(edgeTimer);
    const off = () => { if (!typeMenu.hidden || !langMenu.hidden) { edgeTimer = setTimeout(off, 1000); return; } r.classList.remove('show-top', 'show-bottom', 'show-left'); };
    edgeTimer = setTimeout(off, 4000);
  }, { passive: true });

  // ── Topic as a mind map: the title on the left, its sections and theirs to the right, each paragraph's topic at a
  //    branch's end. Chosen beside the zoom bar (kept in this browser), it is drawn over the page at rest at Topic and
  //    only then: the page stays under it as it was, so a zoom plays from the cards as ever, the map gone as it starts.
  //    A section folds and unfolds with a click; a topic opens its paragraph at Key, as its chip on a card does ──
  const TV_KEY = 'textfold.topicview';
  let topicView = store.get(TV_KEY) === 'map' ? 'map' : 'cards';
  const tview = h('div', 'tview');
  tview.setAttribute('role', 'radiogroup');
  tview.hidden = true;
  const zrow = h('div', 'zrow');
  bar.before(zrow);
  zrow.append(h('span', 'zside'), bar, tview);
  const ICON = {
    cards: '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="1.5" y="2" width="5.5" height="5" rx="1.3" fill="none" stroke="currentColor" stroke-width="1.4"/><rect x="9" y="2" width="5.5" height="5" rx="1.3" fill="none" stroke="currentColor" stroke-width="1.4"/><rect x="1.5" y="9" width="5.5" height="5" rx="1.3" fill="none" stroke="currentColor" stroke-width="1.4"/><rect x="9" y="9" width="5.5" height="5" rx="1.3" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>',
    map: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="3" cy="8" r="2" fill="currentColor"/><path d="M5 8 C8 8 8 3 11 3 M5 8 H11 M5 8 C8 8 8 13 11 13" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="13" cy="3" r="1.6" fill="currentColor"/><circle cx="13" cy="8" r="1.6" fill="currentColor"/><circle cx="13" cy="13" r="1.6" fill="currentColor"/></svg>',
  };
  function renderTview() {
    const t = T[ui];
    tview.setAttribute('aria-label', t.tv_label);
    tview.innerHTML = ['cards', 'map'].map((k) => `<button type="button" role="radio" aria-checked="${topicView === k}" data-view="${k}">${ICON[k]}<span>${esc(t[`tv_${k}`])}</span></button>`).join('');
  }
  tview.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b || b.dataset.view === topicView) return;
    topicView = b.dataset.view;
    store.set(TV_KEY, topicView);
    renderTview();
    mapSync();
  });
  const mapEl = h('div', 'mmap'), mapTools = h('div', 'mm-tools'), mapCanvas = h('div', 'mm-canvas');
  mapEl.hidden = true;
  mapEl.append(mapTools, mapCanvas);
  document.body.append(mapEl);
  let mapRoot = null, mapKey = '';
  // the tree: a section under the last one above it of a higher level; a paragraph's topic under its section (the
  // opening text's, under the title); the topics before the subsections, as the text has them
  function mapTree() {
    const m = D.meta, root = { kind: 'root', html: esc(m.name || (m.titles && m.titles[lang]) || m.title), kids: [] };
    const stack = [{ lv: 0, node: root }];
    for (const c of D.chunks) {
      const sc = secs.get(c.id);
      if (!sc || sc.sec.classList.contains('ref')) continue;
      const lv = c.level || 2;
      let node = stack[stack.length - 1].node;
      if (c.num || c.title) {
        while (stack.length > 1 && stack[stack.length - 1].lv >= lv) stack.pop();
        node = { kind: 'sec', c, html: sc.hd.innerHTML, kids: [], depth: stack.length };
        stack[stack.length - 1].node.kids.push(node);
        stack.push({ lv, node });
      }
      for (const uid of c.units) {
        const pcs = unitEl.has(uid) ? pieces(uid, 0) : [];
        if (!pcs.length) continue;
        const span = h('span', 'mm-tx');
        pcs.forEach((x, k) => {   // (a CJK document's word may be a bare string)
          span.append(x.gap ? '…' : typeof x.tok === 'string' ? x.tok : tokEl(x.tok, ''));
          if (k < pcs.length - 1 && (x.gap || (x.tok && x.tok.s))) span.append(' ');
        });
        node.kids.push({ kind: 'leaf', uid, html: span.innerHTML, role: U[uid].lad.role, kids: [] });
      }
    }
    const paint = (n, b) => { n.b = b; n.kids.forEach((k, i) => paint(k, n.kind === 'root' ? i % 6 : b)); };
    paint(root, 0);
    return root;
  }
  const leavesIn = (n) => n.kids.reduce((s, k) => s + (k.kind === 'leaf' ? 1 : leavesIn(k)), 0);
  // a section folded shows nothing under it (closed), or its sections without its topics (bare); either says how
  // many topics it keeps out of sight
  const shownKids = (n) => (n.closed ? [] : n.bare ? n.kids.filter((k) => k.kind !== 'leaf') : n.kids);
  const hiddenIn = (n) => (n.closed ? leavesIn(n) : n.bare ? n.kids.filter((k) => k.kind === 'leaf').length : 0);
  function mapRender() {
    const t = T[ui];
    mapKey = `${lang}|${ui}`;
    const was = new Map();   // (what was folded stays folded)
    (function keep(n) { if (n && n.c) was.set(n.c.id, [n.closed, n.bare]); (n ? n.kids : []).forEach(keep); })(mapRoot);
    mapRoot = mapTree();
    (function fold(n) { if (n.c && was.has(n.c.id)) [n.closed, n.bare] = was.get(n.c.id); n.kids.forEach(fold); })(mapRoot);
    mapTools.innerHTML = `<button type="button" data-mm="all">${esc(t.mm_all)}</button><button type="button" data-mm="secs">${esc(t.mm_secs)}</button><span class="mm-hint">${esc(t.mm_hint)}</span>`;
    mapLayout();
  }
  // each node right of its parent; a node's children one under another, the node level with the first of them (a long
  // text reads down the map as down its outline, the title at the top)
  function mapLayout() {
    mapCanvas.replaceChildren();
    const els = [], svgNS = 'http://www.w3.org/2000/svg', svg = document.createElementNS(svgNS, 'svg');
    mapCanvas.append(svg);
    (function make(n) {
      const cls = n.kind === 'root' ? 'mm-root' : n.kind === 'sec' ? `mm-sec d${n.depth}` : `mm-leaf${notesOf.has(n.uid) ? ' has-notes' : ''}`;
      const el = h('div', `mm-n ${cls}`);
      el.style.setProperty('--b', `var(--br-${n.b})`);
      if (n.kind === 'leaf') { el.style.setProperty('--d', `var(--r-${n.role})`); el.dataset.u = n.uid; el.innerHTML = `<i class="mm-dot"></i><span>${n.html}</span>`; }
      else el.innerHTML = n.html + (hiddenIn(n) ? `<span class="mm-more">+${hiddenIn(n)}</span>` : '');
      if (n.kind === 'sec') el.dataset.sec = n.c.id;
      texify(el);
      n.el = el;
      mapCanvas.append(el);
      els.push(n);
      shownKids(n).forEach(make);
    })(mapRoot);
    for (const n of els) { n.w = n.el.offsetWidth; n.h = n.el.offsetHeight; }
    const GAPX = 46, PAD = 24;
    let right = 0;
    (function place(n, x, y) {
      n.x = x;
      right = Math.max(right, x + n.w);
      const kids = shownKids(n);
      if (!kids.length) { n.y = y; return n.h; }
      let cy = y;
      kids.forEach((k, i) => { cy += place(k, x + n.w + GAPX, cy) + (i < kids.length - 1 ? (k.kind === 'leaf' && kids[i + 1].kind === 'leaf' ? 5 : 14) : 0); });
      const a = kids[0];
      n.y = Math.max(y, a.y + a.h / 2 - n.h / 2);
      return Math.max(cy - y, n.y + n.h - y);
    })(mapRoot, PAD, PAD + 10);
    let bottom = 0, paths = '';
    for (const n of els) {
      n.el.style.transform = `translate(${n.x}px, ${n.y}px)`;
      bottom = Math.max(bottom, n.y + n.h);
      for (const k of shownKids(n)) {
        const x1 = n.x + n.w, y1 = n.y + n.h / 2, x2 = k.x, y2 = k.y + k.h / 2, xm = (x1 + x2) / 2;
        paths += `<path class="${k.kind === 'leaf' ? 'leaf' : ''}" style="--b: var(--br-${k.b})" d="M${x1} ${y1} C${xm} ${y1} ${xm} ${y2} ${x2} ${y2}"/>`;
      }
    }
    svg.innerHTML = paths;
    mapCanvas.style.width = `${right + PAD}px`;
    mapCanvas.style.height = `${bottom + PAD}px`;
  }
  // where it lies: under the bar at the top, beside the sidebar (the app's) and the original pages (where the column
  // starts: the body's padding); laid again whenever the window or the pages' width changes (reflowKeeping's resize)
  function mapFrame() {
    const tb = document.querySelector('.topbar'), bs = getComputedStyle(document.body);
    mapEl.style.top = `${Math.max(0, tb ? tb.getBoundingClientRect().bottom : 0)}px`;
    mapEl.style.left = `${parseFloat(bs.paddingLeft) || 0}px`;
  }
  function mapSync() {
    const can = mapReady && topicView === 'map' && level === 0 && !localLevel.size;
    if (!can) { if (mapShown) mapAway(); return; }
    if (mapShown || busy || stage) return;   // (over the page already; or, once the motion is over: rest(), done())
    mapFrame();
    mapEl.hidden = false;   // (shown first: the nodes are measured as they are laid out)
    mapShown = true;
    if (mapKey !== `${lang}|${ui}` || !mapRoot) mapRender();
  }
  function mapAway() { mapEl.hidden = true; mapShown = false; }
  mapTools.addEventListener('click', (e) => {
    const b = e.target.closest('[data-mm]');
    if (!b) return;
    const secsOnly = b.dataset.mm === 'secs';
    (function walk(n) { if (n.kind === 'sec') { n.closed = false; n.bare = secsOnly; } n.kids.forEach(walk); })(mapRoot);
    mapLayout();
  });
  mapCanvas.addEventListener('click', (e) => {
    const leaf = e.target.closest('.mm-leaf'), sec = e.target.closest('.mm-sec');
    if (leaf) {   // its paragraph at Key, where the topic was on the screen
      const el = unitEl.get(leaf.dataset.u);
      if (!el) return;
      const y = leaf.getBoundingClientRect().top;
      mapAway();
      scrollBy(0, el.getBoundingClientRect().top - y);
      setLevel(2, { el, y, frac: 0 });
      return;
    }
    if (!sec) return;
    const find = (n) => (n.c && n.c.id === sec.dataset.sec ? n : n.kids.reduce((f, k) => f || find(k), null));
    const n = find(mapRoot);
    if (!n || !n.kids.length) return;
    const y0 = sec.getBoundingClientRect().top;
    if (n.closed || n.bare) (function open(x) { x.closed = x.bare = false; x.kids.forEach(open); })(n);   // (open with what is under it)
    else n.closed = true;
    mapLayout();
    const again = mapCanvas.querySelector(`[data-sec="${CSS.escape(n.c.id)}"]`);   // (the section stays where it was clicked)
    if (again) mapEl.scrollTop += again.getBoundingClientRect().top - y0;
  });
  // ⌘/Ctrl + wheel and a pinch zoom from the page under it: the map goes before the zoom looks for what it is over
  addEventListener('wheel', (e) => { if (mapShown && (e.ctrlKey || e.metaKey)) mapAway(); }, { capture: true, passive: true });
  addEventListener('gesturestart', () => { if (mapShown) mapAway(); }, { capture: true });
  addEventListener('resize', () => { if (mapShown) mapFrame(); });
  renderTview();
  mapReady = true;

  // ── input ────────────────────────────────────────────────────────────
  // ⌘/Ctrl + wheel and a trackpad pinch zoom around the pointer; over the slider, the plain wheel zooms
  addEventListener('wheel', (e) => {
    const overBar = !!(e.target.closest && e.target.closest('#zoombar'));
    if (!e.ctrlKey && !e.metaKey && !overBar) { snapNow(); return; }
    e.preventDefault();
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? innerHeight : 1;
    const d = (Math.abs(e.deltaY) >= Math.abs(e.deltaX) ? e.deltaY : e.deltaX) * unit;
    zoomBy(-d / 240, overBar ? null : { x: e.clientX, y: e.clientY });
  }, { passive: false });
  let g0 = 0;     // Safari's pinch
  addEventListener('gesturestart', (e) => { e.preventDefault(); g0 = goal; if (!tr && !stage) gesturePoint = { x: e.clientX, y: e.clientY }; holding = true; });
  addEventListener('gesturechange', (e) => {
    e.preventDefault();
    if (busy && !tr && !stage) return;
    goal = Math.max(0, Math.min(4, g0 + Math.log(e.scale) * 2.4));
    kick();
  });
  addEventListener('gestureend', (e) => { e.preventDefault(); release(); });
  // ── find in the text (⌘F, Ctrl+F) ────────────────────────────────────
  // The words as the page reads them, each paragraph at its level (the model's line at Key, Takeaway and Topic), and
  // the author's words that level leaves out: a step to one of those opens its paragraph at Full in place, as a note's
  // does. Section titles too. Case and accents aside. Enter and ⇧Enter (⌘G, ⇧⌘G) step through them, Esc closes.
  const fbar = h('div', 'findbar'), finput = h('input'), fcount = h('span', 'fd-n');
  fbar.hidden = true;
  fbar.setAttribute('role', 'search');
  finput.type = 'text';
  finput.enterKeyHint = 'search';
  finput.spellcheck = false;
  const fbtn = (f, text) => { const b = h('button', '', text); b.type = 'button'; b.dataset.f = f; return b; };
  fbar.append(finput, fcount, fbtn('prev', '↑'), fbtn('next', '↓'), fbtn('close', '×'));
  document.body.append(fbar);
  const fold = (s) => s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  // the places a unit's pieces hold the words k (folded): the pieces of each
  function hitsIn(pcs, k) {
    let s = '';
    const at = pcs.map((p, n) => {
      const a = s.length;
      s += p.gap ? '' : fold(typeof p.tok === 'string' ? p.tok : tokPlain(p.tok));
      const b = s.length;
      if (n < pcs.length - 1 && (p.gap || (p.tok && p.tok.s) || pcs[n + 1].gap)) s += ' ';
      return [a, b];
    });
    const out = [];
    for (let i = s.indexOf(k); i >= 0; i = s.indexOf(k, i + k.length)) out.push(pcs.filter((p, n) => at[n][1] > i && at[n][0] < i + k.length));
    return out;
  }
  // every place in reading order: { uid, ids (its pieces), open (left out at the paragraph's level) } or { sec }
  function findAll(q) {
    const k = fold(q.replace(/\s+/g, ' ').trim()), hits = [];
    if (!k) return hits;
    const inUnit = (uid) => {
      const el = unitEl.get(uid);
      if (!el) return;
      const L = levelOf(uid), seen = new Set();
      if (!el.hidden) for (const ps of hitsIn(pieces(uid, L), k)) {
        for (const p of ps) if (p.i !== undefined) seen.add(p.i);
        hits.push({ uid, ids: ps.map((p) => p.id) });
      }
      if (L < 4 || el.hidden) for (const ps of hitsIn(pieces(uid, 4), k)) {   // (on the page already, in the line's words: once)
        if (!ps.some((p) => seen.has(p.i))) hits.push({ uid, ids: ps.map((p) => p.id), open: true });
      }
    };
    const inSec = new Set(D.chunks.flatMap((c) => c.units));
    IDS.filter((uid) => !inSec.has(uid)).forEach(inUnit);
    for (const c of D.chunks) {
      const sc = secs.get(c.id);
      if (sc && !sc.hd.hidden && fold(headingOf(c)).includes(k)) hits.push({ sec: c.id });
      c.units.forEach(inUnit);
    }
    return hits;
  }
  // the words found marked (the one stepped to more strongly), drawn again where that changed (only, the units given)
  function paintFound(only) {
    const was = new Set(findMarks.keys());
    findMarks.clear();
    finder.hits.forEach((x, n) => {
      if (!x.uid) return;
      const m = findMarks.get(x.uid) || new Map();
      for (const id of x.ids) m.set(id, n === finder.at ? 2 : Math.max(m.get(id) || 0, 1));
      findMarks.set(x.uid, m);
    });
    for (const uid of only || new Set([...was, ...findMarks.keys()])) {
      const el = uid && unitEl.get(uid);
      if (el && !el._stale) renderUnit(uid, el._mode === 'tok');
    }
    capEpoch++;
    warmSoon();
  }
  function showFound() {
    const t = T[ui], n = finder.hits.length, q = finder.q.trim();
    fcount.textContent = !q ? '' : n ? t.find_n.replace('{i}', finder.at + 1).replace('{n}', n) : t.find_none;
    fbar.classList.toggle('none', !!q && !n);
  }
  const hitEl = (x) => (x.sec ? secs.get(x.sec).hd : unitEl.get(x.uid));
  function stepTo(n) {
    const H = finder.hits;
    if (!H.length) { showFound(); return; }
    const before = H[finder.at];
    finder.at = (n + H.length) % H.length;
    const x = H[finder.at];
    if (mapShown) mapAway();
    // words the paragraph's level leaves out: the paragraph at Full, in place, while it holds the one stepped to (one
    // at a time, so stepping through does not leave the page unfolded; the last stays open when the search closes)
    const was = finder.opened, keep = x.open ? x.uid : null;
    if (was && was !== keep) {
      finder.opened = null;
      if (localLevel.get(was) === 4) { localLevel.delete(was); renderUnit(was, unitEl.get(was)._mode === 'tok'); }
    }
    if (x.open && levelOf(x.uid) < 4) { localLevel.set(x.uid, 4); finder.opened = x.uid; renderUnit(x.uid, true); }
    if (was !== finder.opened) relayout();
    paintFound([before && before.uid, x.uid]);
    showFound();
    const el = hitEl(x);
    if (x.uid && el._mode !== 'tok') renderUnit(x.uid, true);
    const s = x.uid && spans.get(x.uid).get(x.ids[0]), r = (s && s.isConnected ? s : el).getBoundingClientRect();
    if (r.top < 80 || r.bottom > innerHeight - 100) window.scrollTo({ top: scrollY + r.top - innerHeight * 0.35, behavior: 'auto' });
    if (x.sec) flash(el);
  }
  // the places found again: for new words, from the first on screen on; for the page read at another level or in
  // another language, from the one stepped to before
  function refind(fresh) {
    if (busy) { clearTimeout(findTimer); findTimer = setTimeout(() => refind(fresh), 120); return; }
    const was = finder.hits[finder.at], same = (x) => was && x.uid === was.uid && x.sec === was.sec && (!x.ids || x.ids[0] === was.ids[0]);
    finder.hits = findAll(finder.q);
    finder.at = -1;
    paintFound();
    const H = finder.hits;
    let n = fresh ? -1 : H.findIndex(same);
    if (n < 0) n = Math.max(0, H.findIndex((x) => hitEl(x).getBoundingClientRect().bottom > 80));
    if (!H.length) showFound();
    else if (fresh) stepTo(n);
    else { finder.at = n; paintFound([H[n].uid]); showFound(); }
  }
  let findTimer = 0;
  function findStale() { if (finder.open) { clearTimeout(findTimer); findTimer = setTimeout(() => refind(false), 80); } }
  function openFind() {
    const t = T[ui], sel = String(getSelection()).replace(/\s+/g, ' ').trim();
    finput.placeholder = t.find_ph;
    for (const [f, label] of [['prev', t.find_prev], ['next', t.find_next], ['close', t.find_close]]) {
      const b = fbar.querySelector(`[data-f="${f}"]`);
      b.title = label;
      b.setAttribute('aria-label', label);
    }
    if (sel && sel.length <= 80) finput.value = sel;
    fbar.hidden = false;
    finder.open = true;
    finput.focus();
    finput.select();
    if (finput.value !== finder.q || sel) { finder.q = finput.value; refind(true); }
  }
  function closeFind() {
    fbar.hidden = true;
    finder.open = false;
    finder.opened = null;
    finder.hits = [];
    finder.at = -1;
    clearTimeout(findTimer);
    paintFound();
  }
  finput.addEventListener('input', () => {
    clearTimeout(findTimer);
    findTimer = setTimeout(() => { finder.q = finput.value; refind(true); }, 120);
  });
  finput.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeFind(); return; }
    if (e.key !== 'Enter' || e.isComposing) return;
    e.preventDefault();
    if (finput.value !== finder.q) { clearTimeout(findTimer); finder.q = finput.value; refind(true); } else stepTo(finder.at + (e.shiftKey ? -1 : 1));
  });
  fbar.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.f === 'close') closeFind(); else stepTo(finder.at + (b.dataset.f === 'prev' ? -1 : 1));
  });
  addEventListener('keydown', (e) => {
    const mod = (e.metaKey || e.ctrlKey) && !e.altKey;
    if (mod && !e.shiftKey && (e.key === 'f' || e.key === 'F')) { e.preventDefault(); openFind(); }
    else if (mod && (e.key === 'g' || e.key === 'G') && finder.open) { e.preventDefault(); stepTo(finder.at + (e.shiftKey ? -1 : 1)); }
  });

  addEventListener('keydown', (e) => {
    if (!lightbox.hidden) {   // an enlarged figure takes the keys: Esc closes it, the rest do nothing behind it
      if (e.key === 'Escape') { e.preventDefault(); closeLightbox(); }
      return;
    }
    if (e.target.closest && e.target.closest('input, textarea, [contenteditable]')) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === 'Escape' && (popNote || !selbar.hidden)) { closeNote(); hideSelbar(); e.preventDefault(); return; }
    if (e.key === 'Escape' && rolePinned) { legendFocus(null, true); e.preventDefault(); return; }
    if (e.key === '-' || e.key === '_') setLevel(level - 1);
    else if (e.key === '=' || e.key === '+') setLevel(level + 1);
    else if (/^[1-5]$/.test(e.key)) setLevel(BAR[+e.key - 1]);
    else if (e.key === 'l' && AVAIL.length > 1) setLang(AVAIL[(AVAIL.indexOf(lang) + 1) % AVAIL.length]);
    else if (e.key === 'Escape' && !langMenu.hidden) closeLangMenu();
    else if (e.key === 'Escape' && !typeMenu.hidden) { closeTypeMenu(); typeBtn.focus(); }
    else if (e.key === 'Escape' && localLevel.size) setLevel(level);
    else if (e.key === 'f' || e.key === 'F') setFocus(!focusOn);
    else if (e.key === 'Escape' && focusOn) setFocus(false);
    else return;
    e.preventDefault();
  });
  docEl.addEventListener('click', (e) => {
    const pic = e.target.closest('figure.fig .fbody img, figure.fig .fbody svg');
    if (pic && !String(getSelection()).length) { e.preventDefault(); openLightbox(pic.closest('svg') || pic); return; }
    const card = e.target.closest('.ncard');
    if (card) { e.preventDefault(); const n = notes.find((x) => x.id === card.dataset.note); if (n) openNote(n); return; }
    const mark = e.target.closest('.hl, .hlsp');
    if (mark && !String(getSelection()).length) { const n = noteAt(mark); if (n) { e.preventDefault(); openNote(n); return; } }
    const lk = e.target.closest('.lk');
    if (lk) { e.preventDefault(); hidePeek(); go(edgeTarget(lk.dataset.tk, lk.dataset.to), lk.closest('.u')); return; }
    const a = e.target.closest('a[href^="#"]');
    if (a) {
      const id = decodeURIComponent(a.getAttribute('href').slice(1));
      e.preventDefault();
      if (id.startsWith('bib')) { showPeek(a); return; }       // a citation: show the reference, stay here
      hidePeek();
      go(resolve(id), a.closest('.u'));
      return;
    }
    if (e.target.closest('a, details, summary')) return;
    if (level === 4 && !localLevel.size) return;
    if (String(getSelection()).length) return;
    const el = e.target.closest('.u');
    if (!el || !el.dataset.u) return;
    if (level === 0 && !localLevel.size) {
      const r = el.getBoundingClientRect();
      setLevel(2, { el, y: r.top, frac: 0 });
    } else toggleUnit(el.dataset.u);
  });

  let relayoutTimer = null;
  function relayout() {
    clearTimeout(relayoutTimer);
    relayoutTimer = setTimeout(() => { const t0 = performance.now(); layoutSides(); perf('sides', t0); }, 30);
  }
  addEventListener('resize', () => { snapNow(); capEpoch++; relayout(); geom = null; placeThumb(); warmSoon(); });

  // ── start ────────────────────────────────────────────────────────────
  build();
  drawDiagrams();
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => drawDiagrams());
  for (const [c, hd, st] of headEls) secs.set(c.id, { c, sec: hd.parentNode, hd, st });
  {   // the Topic map's rows (reader.css): a section with sections under it heads theirs, and one after a deeper one starts its own
    const lv = (c) => c.level || 2, top = Math.min(...D.chunks.map(lv));
    D.chunks.forEach((c, i) => {
      const sc = secs.get(c.id), next = D.chunks[i + 1], prev = D.chunks[i - 1];
      if (!sc) return;
      if (next && lv(next) > lv(c) && (c.num || c.title)) sc.sec.classList.add('parent', ...(lv(c) === top ? ['top'] : []));
      if (prev && lv(prev) > lv(c)) sc.sec.classList.add('newrow');
    });
  }
  IDS = [...order.keys()];
  {   // a heading's place in reading order: the first unit after it
    let pend = [];
    for (const el of docEl.querySelectorAll('.hd, .u')) {
      if (el.classList.contains('hd')) pend.push(el);
      else { for (const hd of pend) HEAD_IDX.set(hd, order.get(el.dataset.u)); pend = []; }
    }
    for (const hd of pend) HEAD_IDX.set(hd, IDS.length - 1);
  }
  // ?lang= (the app), #lang= (a static file), then the reader's language, then the one last read in
  const asked = new URLSearchParams(location.search).get('lang') || (/[#&]lang=([\w-]+)/.exec(location.hash) || [])[1];
  lang = [asked, D.lang, readerLang, store.get('dr-lang'), BASE, AVAIL[0]].find((l) => l && AVAIL.includes(l));
  const start = /[#&]level=([^&]+)/.exec(location.hash);
  // where it was being read, unless the link names a place or the page comes again after a generation (pf-keep, below)
  let wasAt = null;
  try { if (!/[#&](?:u|n|s|level)=/.test(location.hash) && !sessionStorage.getItem('pf-keep')) wasAt = readPos()[D.meta.id] || null; } catch { /* private window */ }
  if (wasAt && !unitEl.has(wasAt.u)) wasAt = null;
  if (wasAt && typeof wasAt.l === 'number' && wasAt.l >= 0 && wasAt.l <= 4) level = wasAt.l;
  if (start) {
    const want = decodeURIComponent(start[1]).toLowerCase();
    const L = I18N.CODES.map((c) => T[c].levels.findIndex((n) => n.toLowerCase() === want)).find((i) => i >= 0);
    if (L !== undefined) level = L;
  }
  docEl.dataset.level = level;
  document.documentElement.dataset.level = level;
  pos = goal = level;
  applyLang();
  renderAll();
  updateBar();
  relayout();
  if (document.fonts) {
    document.fonts.ready.then(() => { capEpoch++; relayout(); geom = null; placeThumb(); warmSoon(30); });   // the keyframes, as soon as the type is in
    document.fonts.addEventListener('loadingdone', () => { capEpoch++; warmSoon(); });
  }
  docEl.addEventListener('load', () => { capEpoch++; warmSoon(); }, true);   // an image arrived: the layout moved
  addEventListener('load', () => { relayout(); warmSoon(); });
  if (window.__drPerf) window.__drPerf.push(['boot', Math.round(performance.now())]);
  hydrateSoon();
  warmSoon(600);
  loadNotes();
  try { if (PDF_OK && innerWidth >= 1100 && localStorage.getItem(CMP_KEY) === '1') openCompare(); } catch { /* private window */ }
  if (wasAt) {   // (once laid out; again once the type is in, which moves the lines)
    const el = unitEl.get(wasAt.u), back = () => scrollBy(0, el.getBoundingClientRect().top - wasAt.y);
    requestAnimationFrame(() => {
      back();
      if (IDS.indexOf(wasAt.u) > 2) pill(`${esc(T[ui].pos_back)}<button type="button" data-top="1">${esc(T[ui].pos_top)}</button>`, 6000);
    });
    if (document.fonts) document.fonts.ready.then(() => requestAnimationFrame(back));
  }
  // the page again after a generation (app.js): the level and the paragraph that were being read, where they were
  try {
    const k = JSON.parse(sessionStorage.getItem('pf-keep') || 'null');
    sessionStorage.removeItem('pf-keep');
    const el = k && k.pid === D.meta.id && unitEl.get(k.uid);
    if (el) {
      const back = () => requestAnimationFrame(() => scrollBy(0, el.getBoundingClientRect().top - k.top));
      if (typeof k.level === 'number' && k.level !== level) { setLevel(k.level); setTimeout(back, DUR + 120); } else back();
    }
  } catch { /* private window */ }
  // the reader's language, from the app (its settings, the landing's menus): the interface speaks it when it has the
  // words, else English; the text goes with it when the paper has that language
  function setUi(code) {
    if (!(T[code] || NAMES[code]) || code === readerLang) return;
    readerLang = code;
    ui = I18N.uiFor(code);
    store.set(UI_KEY, code);
    if (AVAIL.includes(code) && code !== lang) setLang(code); else applyLang();
  }
  // the name the reader gave the text in the library, changed while it is open: its head and the window's title follow
  function rename(name) {
    D.meta.name = name || undefined;
    document.title = name || D.meta.title;
    applyLang();
  }
  window.__reader = { setLevel, setLang, setUi, rename, get ui() { return ui; }, zoomBy,
    get compared() { if (!cmp || !cmp.grams) return null; const ids = [...order.keys()]; return { units: ids.length, found: ids.filter((id) => locate(id)).length, words: cmp.words.length }; }, get level() { return level; }, get lang() { return lang; }, get pos() { return pos; },
    get staged() { return !!stage; }, get _caps() { return caps; }, get keyframes() { return caps.map((c) => (c ? c.P.size : 0)); },
    // holding the zoom at a position, as a hand would (for looking at the motion frame by frame)
    scrubTo(p) { clearTimeout(snapTimer); holding = true; aimLevel = null; goal = Math.max(0, Math.min(4, p)); kick(); }, letGo() { release(); } };
})();
