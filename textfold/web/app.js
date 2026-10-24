// The app around the reader: the sidebar of papers, the landing form, settings, and generation jobs.
// On a reader page it exposes window.DRApp so the reader's language menu can generate a language.
(() => {
  'use strict';
  const I18N = window.DR_I18N;
  const bootEl = document.getElementById('boot');
  const BOOT = bootEl ? JSON.parse(bootEl.textContent) : {};
  const readerEl = document.getElementById('paper-data');
  const DATA = readerEl ? JSON.parse(readerEl.textContent) : null;
  const PAPER = DATA ? DATA.meta : null;   // the paper open in the reader, if any
  const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch { /* private window */ } } };
  // the brightness and the style chosen in a paper's Aa menu (reader.js, textfold.type), on the landing too
  try {
    const v = JSON.parse(store.get('textfold.type') || 'null') || {}, r = document.documentElement;
    if (['light', 'dark'].includes(v.mode)) r.dataset.theme = v.mode;
    if (v.style === 'paper') r.dataset.style = v.style;
  } catch { /* (written by hand) */ }
  const SHORT = { en: 'EN', zh: '中', 'zh-Hant': '繁', ja: '日', ko: '한', es: 'ES', fr: 'FR', de: 'DE', pt: 'PT', it: 'IT', ru: 'RU', hi: 'हि' };   // (any other: its code)
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const h = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  const api = (path, body) => fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(async (r) => { const d = await r.json(); if (!r.ok) throw new Error(d.error || r.statusText); return d; });
  let modelData = BOOT.modelData || null, modelRefresh = null;
  function refreshModels() {
    if (!modelRefresh) modelRefresh = (async () => {
      let d = await api('/api/models/refresh', {});
      while (d.discovery.refreshing || d.scanning) {
        await new Promise((r) => setTimeout(r, 400));
        d = await api('/api/models');
      }
      modelData = d;
      return d;
    })().finally(() => { modelRefresh = null; });
    return modelRefresh;
  }

  // The ChatGPT bridge started, and waited for: a sign-in first when Codex has none (a browser opens; its link shows
  // too), then the bridge. What stopped it ends the wait at once, with openai-oauth's words, not five minutes on (#12).
  // onState sees each status as it comes, and stops the wait by returning false.
  async function startBridge(onState) {
    let st = await api('/api/bridge/start', {});
    for (let k = 0; k < 160; k++) {   // a little over 5 minutes: openai-oauth's own sign-in waits as long
      if (onState(st) === false || st.running || st.error) return st;
      await new Promise((r) => setTimeout(r, 2000));
      st = await api('/api/bridge');
    }
    return st;
  }
  const bridgeNote = (st, T) => !st || st.running ? '' : st.error
    ? `<p class="st-note warn">${esc(T.bridge_failed.replace('{error}', st.error))}</p>`
    : st.login ? `<p class="st-note">${esc(T.bridge_login)} <a href="${esc(st.login)}" target="_blank" rel="noopener">${esc(T.bridge_login_open)}</a></p>` : '';

  // the reader's language (settings; else the browser's): papers are read and made in it, the interface speaks it when it
  // has the words (else English), and a switch of a paper's language is a switch of it (reader.js)
  const UI_KEY = 'pf-ui-lang';
  const READ = BOOT.langs || (DATA && DATA.langs) || I18N.NATIVE;   // every language a text can be read in, by its own name
  const nameOf = (code) => READ[code] || I18N.NATIVE[code] || code;
  let readLang = [store.get(UI_KEY), I18N.guess(READ)].find((l) => l && READ[l]) || 'en';
  let uiLang = I18N.uiFor(readLang);
  // The page keeps the reader's language itself (UI_KEY). The server is told it too, when it changes, and as the landing
  // opens while it has none (the settings' "lang"; they are every server's, so a page elsewhere never overwrites a
  // choice): for the programs that work with TextFold (an editor's plugin), which cannot read this window's storage.
  // Nothing here reads it back.
  let told = modelData ? modelData.lang : undefined;
  function tellLang(code) {
    if (!code || code === told || !READ[code]) return;
    told = code;
    // (keepalive: a language just made is told as the page goes to it)
    fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ lang: code }), keepalive: true })
      .then((r) => { if (!r.ok) told = undefined; }, () => { told = undefined; });
  }
  function setReaderLang(code) {
    if (!READ[code]) return;
    readLang = code;
    uiLang = I18N.uiFor(code);
    store.set(UI_KEY, code);
    tellLang(code);
    renderSidebar();
    if (landing) renderLanding();
    if (window.__reader && window.__reader.setUi) window.__reader.setUi(code);
    document.dispatchEvent(new Event('tf-lang'));   // (what else speaks it: the app's update)
  }
  let papers = BOOT.papers || [];
  let folders = BOOT.folders || { folders: [], of: {} };   // the reader's folders (each in its parent, or at the top), and which document is in which (library.py)
  let sbQuery = '';        // the sidebar's search, as typed
  let editing = null;      // a folder's name being written: 'new', or the folder's id
  let dragging = null;     // the documents being dragged to a folder (one, or those picked)
  let draggingFolder = null;   // the folder being dragged into another, or to the top
  let moving = null;       // the document a new folder is being named for (its menu's New folder…)
  let within = null;       // the folder a new one is being named inside (its menu's New subfolder…); none: at the top
  let renaming = null;     // the document whose name in the library is being written
  let menu = null;         // a folder's or a document's menu, while it is open
  let stale = false;       // the list as polled while a menu was open: drawn when it closes
  const chosen = new Set();   // the documents picked to be moved or deleted together (⌘/Ctrl- or Shift-click, a row's Select)
  let chosenAt = null;        // the one picked last: a Shift-click picks every row shown from it to the one clicked
  const MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  const FOLDED = 'textfold.folded';   // the folders shut, kept for next time
  const SORT = 'textfold.sort';       // the list's order: 'kind' (the default), 'added' (when it came in) or 'opened' (last)
  const folded = new Set((store.get(FOLDED) || '').split(' ').filter(Boolean));
  let jobs = BOOT.jobs || [];
  let modelName = BOOT.model || '';       // the model generations use, as the settings name it
  let modelsLooking = !modelName;         // the agents and local services still being looked for (the landing's first refresh)
  let askName = BOOT.askName || '';       // the one questions are answered with, when it is another
  const t = () => I18N.t(uiLang);
  // an item picked in the Zotero library: on the landing it is read there; from a paper's page it goes there to be (?zotero=)
  let pickItem = (item, title) => { location.href = `/?zotero=${encodeURIComponent(item)}&t=${encodeURIComponent(title)}`; };

  // ── sidebar ──────────────────────────────────────────────────────────
  const side = document.getElementById('sidebar');
  const MARK = '<svg viewBox="0 0 20 20" aria-hidden="true"><rect class="m1" x="2" y="3" width="16" height="2.4" rx="1.2"/><rect class="m2" x="2" y="8.8" width="11" height="2.4" rx="1.2"/><rect class="m3" x="2" y="14.6" width="5" height="2.4" rx="1.2"/></svg>';
  const BOOKS = '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 3.5h3v13H4z"/><path d="M8.5 3.5h3v13h-3z"/><path d="M13 4.2l2.8-.7 2.6 12.3-2.8.7z"/></svg>';
  const LIST = '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="2.5" y="3.5" width="15" height="13" rx="2.5"/><path d="M7.5 3.5v13"/></svg>';
  const PIN = '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"><path class="pin-head" d="M7.2 2.8h5.6l-.9 4.6 2.9 3v1.4H5.2v-1.4l2.9-3z"/><path d="M10 11.8v5.4"/></svg>';
  // the sidebar pinned beside a paper (below: setPinned), kept in this browser
  const PIN_KEY = 'textfold.sbpin';
  let pinned = store.get(PIN_KEY) === '1';
  const FOLDER = '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M2.5 5.5a1.5 1.5 0 0 1 1.5-1.5h3.6l1.8 2H16a1.5 1.5 0 0 1 1.5 1.5v7A1.5 1.5 0 0 1 16 16H4a1.5 1.5 0 0 1-1.5-1.5z"/></svg>';
  const FOLDER_NEW = '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 5.5a1.5 1.5 0 0 1 1.5-1.5h3.6l1.8 2H16a1.5 1.5 0 0 1 1.5 1.5v7A1.5 1.5 0 0 1 16 16H4a1.5 1.5 0 0 1-1.5-1.5z"/><path d="M10 8.5v5M7.5 11h5"/></svg>';
  const CHEV = '<svg class="sb-chev" viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 6l4 4-4 4"/></svg>';
  // a paper opens in the reader's language, else the one last read in, else its first
  const pick = (p) => [readLang, store.get('dr-lang')].find((l) => l && p.langs.includes(l)) || p.langs[0] || 'en';
  const modelLabel = () => {   // and the one for questions, when it is another (its agent's name said once: Claude Code · Sonnet · answers: Opus)
    if (!askName) return modelName;
    const head = modelName.split(' · ')[0], short = askName.startsWith(`${head} · `) ? askName.slice(head.length + 3) : askName;
    return `${modelName} · ${t().sb_ask_with.replace('{m}', short)}`;
  };
  const live = (j) => j.status === 'queued' || j.status === 'running';
  function jobFor(pid) { return jobs.find((j) => j.pid === pid && live(j)); }
  function pct(j) { return j.total ? Math.round((100 * j.done) / j.total) : (j.stage === 'done' ? 100 : 6); }
  // where a job is: its stage and count, or that it is stopping
  const stageOf = (j, T) => {
    const st = j.stage === 'convert' && j.file && !/\.pdf$/i.test(j.file) ? 'convert-file' : j.stage;   // a Word file, a picture
    return j.stopping ? T.stopping : `${T.stages[st] || (T.pstages || {})[st] || st}${j.total ? ` ${j.done}/${j.total}` : ''}`;
  };
  const stopBtn = (j, T) => `<button type="button" class="sb-stop" data-stop="${j.id}" title="${esc(T.stop)}" aria-label="${esc(T.stop)}"${j.stopping ? ' disabled' : ''}><i></i></button>`;
  const progress = (j, T) => `<span class="sb-prog"><i style="width:${pct(j)}%"></i></span><span class="sb-stage">${esc(stageOf(j, T))}${j.lang ? ` · ${esc(nameOf(j.lang))}` : ''}</span>`;
  const jobName = (j, T) => (j.kind === 'install' ? T.engine : j.title || j.pid);
  let lastSidebar = '';
  function renderSidebar() {
    stale = false;
    const T = t();
    const listed = new Set(papers.map((p) => p.id));
    for (const id of chosen) if (!listed.has(id)) chosen.delete(id);   // (deleted meanwhile)
    const pending = jobs.filter((j) => !listed.has(j.pid) && live(j));
    const item = (p) => {
      const j = jobFor(p.id);
      const own = (p.titles && p.titles[readLang]) || p.title, title = p.name || own;
      if (renaming === p.id) {
        return `<div class="sb-row" data-pid="${p.id}" data-q="${esc(hay(p))}"><input class="sb-dname-in" value="${esc(title)}" placeholder="${esc(own)}" ` +
          `aria-label="${esc(T.d_name)}" title="${esc(T.d_name)}" maxlength="200" spellcheck="false"></div>`;
      }
      const langs = p.langs.map((l) => `<span${l === readLang ? ' class="on"' : ''}>${SHORT[l] || l.toUpperCase()}</span>`).join('');
      const active = PAPER && PAPER.id === p.id;
      const unfinished = !j && !p.langs.length && !p.readable;   // a cached original can be read without generating levels
      return `<div class="sb-row${chosen.has(p.id) ? ' chosen' : ''}" data-pid="${p.id}" data-q="${esc(hay(p))}"><a class="sb-item${active ? ' active' : ''}${unfinished ? ' unfinished' : ''}" href="${unfinished ? '#' : `/p/${p.id}?lang=${pick(p)}`}"${unfinished ? ` data-resume="${p.id}"` : ''} title="${esc(p.title)}">
        <span class="sb-t">${esc(title)}</span><span class="sb-m"><span class="sb-id">${p.id}</span><span class="sb-langs">${langs}</span></span>
        ${j ? progress(j, T) : unfinished ? `<span class="sb-stage">${esc(T.unfinished)}</span>` : ''}</a>` +
        (j ? stopBtn(j, T) : chosen.size ? '<span class="sb-check" aria-hidden="true"></span>'
          : `<button type="button" class="sb-del" data-del="${p.id}" title="${esc(T.del_paper)}" aria-label="${esc(T.del_paper)}">×</button>`) + '</div>';
    };
    // papers, Wikipedia articles, GitHub READMEs and documents of one's own, each under its name when there is more than one kind
    // (a file read from a Zotero item is among the papers: Zotero says what it is)
    const kindOf = (p) => (p.zotero ? 'paper' : /^wiki-/.test(p.id) ? 'wiki' : /^gh-/.test(p.id) ? 'gh' : /^web-/.test(p.id) ? 'web' : /^(md|pdf|doc)-/.test(p.id) ? 'doc' : 'paper');
    // by kind, or by when each came in or was last opened (one never opened: when it came in), newest first, in
    // folders too; by kind, in the server's order (the latest layer first)
    const order = sortOrder(), when = (p) => (order === 'opened' ? p.opened || p.added : p.added) || p.updated;
    const docs = order === 'kind' ? papers : [...papers].sort((a, b) => when(b) - when(a));
    // the reader's folders first (inside one another; a document in one at most), then the documents in none
    const fs = folders.folders, loose = docs.filter((p) => !folders.of[p.id]);
    const today = new Date().setHours(0, 0, 0, 0);
    const since = (p) => { const at = when(p) * 1000; return at >= today ? 0 : Date.now() - at < 7 * 864e5 ? 1 : Date.now() - at < 30 * 864e5 ? 2 : 3; };
    const groups = (order !== 'kind' ? [[0, T.d_today], [1, T.d_week], [2, T.d_month], [3, T.d_older]].map(([k, name]) => [name, loose.filter((p) => since(p) === k)])
      : [['paper', T.papers], ['wiki', T.g_wiki], ['gh', 'GitHub'], ['web', T.g_web], ['doc', T.g_docs]].map(([k, name]) => [name, loose.filter((p) => kindOf(p) === k)]))
      .filter(([, ps]) => ps.length);
    const named = (value) => `<input class="sb-fname-in" value="${esc(value)}" placeholder="${esc(T.f_name)}" aria-label="${esc(T.f_name)}" maxlength="80" spellcheck="false">`;
    const fresh = (parent) => (editing === 'new' && within === parent ? `<div class="sb-folder"><div class="sb-frow">${named('')}</div></div>` : '');
    // a folder: its folders, then its documents; its count, the documents in it and in the folders inside it
    const folder = (f) => {
      const ps = docs.filter((p) => folders.of[p.id] === f.id), subs = kidsOf(f.id), shut = folded.has(f.id), under = inside(f.id);
      const head = editing === f.id ? named(f.name)
        : `<button type="button" class="sb-fhead" aria-expanded="${!shut}">${CHEV}${FOLDER}<span class="sb-fname">${esc(f.name)}</span>` +
          `<span class="sb-fn">${docs.filter((p) => under.has(folders.of[p.id])).length}</span></button>` +
          `<button type="button" class="sb-fmenu" aria-label="${esc(T.f_actions)}" title="${esc(T.f_actions)}">⋯</button>`;
      const inner = fresh(f.id) + subs.map(folder).join('') + ps.map(item).join('');
      return `<div class="sb-folder${shut ? ' shut' : ''}" data-folder="${f.id}"><div class="sb-frow"${editing === f.id ? '' : ' draggable="true"'}>${head}</div>` +
        `<div class="sb-fitems">${inner || `<div class="sb-fempty">${esc(T.f_empty)}</div>`}</div></div>`;
    };
    const kinds = groups.length > 1 || fs.length
      ? groups.map(([name, ps]) => `<div class="sb-group">${esc(name)}</div>${ps.map(item).join('')}`).join('')
      : loose.map(item).join('');
    const listed_ = papers.length || fs.length || editing
      ? fresh(null) + kidsOf(null).map(folder).join('') +
        (fs.length ? `<div class="sb-out">${esc(T.f_out)}</div>` : '') + `<div class="sb-loose">${kinds}</div><div class="sb-nomatch" hidden>${esc(T.sb_nomatch)}</div>`
      : '';
    const pend = (j) => `<div class="sb-row" data-q="${esc(fold(`${jobName(j, T)} ${j.pid}`))}"><div class="sb-item pending"><span class="sb-t">${esc(jobName(j, T))}</span>` +
      `<span class="sb-m"><span class="sb-id">${j.pid}</span></span>${progress(j, T)}</div>${stopBtn(j, T)}</div>`;
    const html = `
      <div class="sb-head"><a class="sb-brand" href="/">${MARK}<span>TextFold</span></a>
        <span class="sb-hbtns"><button type="button" class="sb-pin" aria-pressed="${pinned}" aria-label="${esc(pinned ? T.sb_unpin : T.sb_pin)}" title="${esc(pinned ? T.sb_unpin : T.sb_pin)}">${PIN}</button>
        <button type="button" class="sb-toggle" aria-label="${esc(T.collapse)}" title="${esc(T.collapse)}">‹</button></span></div>
      <div class="sb-rail">
        <a class="rail-btn rail-home" href="/" title="${esc(T.home)}">${MARK}</a>
        <button type="button" class="rail-btn rail-open" title="${esc(T.expand)}">${LIST}</button>
        <a class="rail-btn" href="/" title="${esc(T.new_paper)}">＋</a>
        <button type="button" class="rail-btn rail-settings" title="${esc(T.settings)}">⚙</button>
        ${window.TFTutorial && PAPER ? `<button type="button" class="rail-btn rail-help" title="${esc(T.tut_open)}">?</button>` : ''}
      </div>
      <div class="sb-find"><input type="search" class="sb-q" value="${esc(sbQuery)}" placeholder="${esc(T.sb_search)}" aria-label="${esc(T.sb_search)}" autocomplete="off" spellcheck="false">` +
        `<button type="button" class="sb-newf" title="${esc(T.f_new)}" aria-label="${esc(T.f_new)}">${FOLDER_NEW}</button></div>
      <div class="sb-order" role="radiogroup" aria-label="${esc(T.sort_label)}">${['kind', 'added', 'opened'].map((o) =>
        `<button type="button" role="radio" aria-checked="${o === order}" data-sort="${o}" title="${esc(T[`sort_${o}`])}">${esc(T[`by_${o}`])}</button>`).join('')}</div>
      ${groups.length > 1 || fs.length ? '' : `<div class="sb-label">${esc(groups.length ? groups[0][0] : T.papers)}</div>`}
      <nav class="sb-list${chosen.size ? ' choosing' : ''}">${pending.map(pend).join('')}${listed_ || (pending.length ? '' : `<div class="sb-empty">${esc(T.empty)}</div>`)}</nav>
      ${chosen.size ? `<div class="sb-chosen" role="toolbar" aria-label="${esc(T.sel_n.replace('{n}', chosen.size))}" title="${esc(selTip(T))}">` +
        `<span class="sb-cn">${esc(T.sel_n.replace('{n}', chosen.size))}</span><button type="button" class="sb-cmove">${esc(T.sel_move)}</button>` +
        `<button type="button" class="sb-cdel">${esc(T.sel_delete)}</button>` +
        `<button type="button" class="sb-cx" title="${esc(T.sel_clear)}" aria-label="${esc(T.sel_clear)}">×</button></div>` : ''}
      <div class="sb-foot"><button type="button" class="sb-zotero">${BOOKS}${esc(T.z_lib)}</button><button type="button" class="sb-settings">⚙ ${esc(T.settings)}</button>${window.TFTutorial && PAPER ? `<button type="button" class="sb-help">? ${esc(T.tut_open)}</button>` : ''}<div class="sb-model">${esc(modelLabel())}</div></div>
      <div class="sb-resize" role="separator" aria-orientation="vertical" tabindex="0" title="${esc(T.sb_resize)}" aria-label="${esc(T.sb_resize)}"></div>`;
    // polled every second while something generates: drawn again only when it changed, and where it was scrolled to
    const busy = jobs.some(live);
    if (dragging || draggingFolder) return;   // (drawn again after the drop: the row being dragged stays where the drag began)
    if (html === lastSidebar) { side.querySelector('.rail-open').classList.toggle('busy', busy); return; }
    const scrolled = side.querySelector('.sb-list') ? side.querySelector('.sb-list').scrollTop : 0;
    const act = document.activeElement, typing = act && side.contains(act) && act.matches('.sb-q, .sb-fname-in, .sb-dname-in')
      ? { sel: ['.sb-q', '.sb-fname-in', '.sb-dname-in'].find((s) => act.matches(s)), value: act.value, a: act.selectionStart, b: act.selectionEnd } : null;
    closeMenu();
    side.innerHTML = html;
    lastSidebar = html;
    side.querySelector('.sb-list').scrollTop = scrolled;
    const field = typing && side.querySelector(typing.sel);
    if (field) { field.value = typing.value; field.focus(); try { field.setSelectionRange(typing.a, typing.b); } catch { /* (a search field) */ } }
    applySearch();
    side.querySelector('.sb-toggle').onclick = () => setCollapsed(true);
    side.querySelector('.sb-pin').onclick = () => setPinned(!pinned);
    side.querySelector('.rail-open').onclick = () => setCollapsed(false);
    side.querySelectorAll('.sb-settings, .rail-settings').forEach((b) => { b.onclick = openSettings; });
    side.querySelector('.sb-zotero').onclick = () => openLibrary();
    side.querySelectorAll('.sb-help, .rail-help').forEach((b) => { b.onclick = (e) => { e.stopPropagation(); window.TFTutorial.open(uiLang); }; });
    side.querySelectorAll('.sb-del').forEach((b) => { b.onclick = (e) => { e.preventDefault(); e.stopPropagation(); deletePaper(b.dataset.del); }; });
    side.querySelectorAll('.sb-stop').forEach((b) => { b.onclick = (e) => { e.preventDefault(); e.stopPropagation(); stopJob(b.dataset.stop); }; });
    side.querySelectorAll('[data-resume]').forEach((a) => { a.onclick = (e) => { e.preventDefault(); resume(a.dataset.resume); }; });
    // folded to a rail (over a paper): a dot on the list button while something generates
    side.querySelector('.rail-open').classList.toggle('busy', busy);
  }
  // a job stopped from anywhere (the sidebar, the landing's card, the toast over a paper): what is written stays cached
  async function stopJob(id) {
    try {
      const j = await api('/api/jobs/stop', { id });
      jobs = [...jobs.filter((x) => x.id !== id), j];
      const cb = watchers.get(id);
      if (cb) cb(j);
    } catch { /* finished meanwhile */ }
    renderSidebar(); startPolling();
  }
  // an unfinished paper goes on in the language it was last asked for (else the reader's), in the background
  function resume(pid) {
    const last = [...jobs].reverse().find((j) => j.pid === pid);
    const lang = (last && last.lang) || readLang;
    submit(pid, lang, false, () => {}).catch(() => {});
  }
  // a paper off the list, or those picked, after a prompt that says what goes with them; the server moves their
  // folders to papers/.trash, and for a while the corner offers them back (Undo), here or on the page it goes to
  async function deletePaper(pid) {
    const T = t(), p = papers.find((x) => x.id === pid);
    if (!p) return;
    const title = p.name || (p.titles && p.titles[readLang]) || p.title;
    if (!confirm(T.del_confirm.replace('{title}', title).replace('{n}', p.notes || 0))) return;
    await deleteAll([pid], T.del_done.replace('{title}', title));
  }
  async function deleteChosen(ids) {
    const T = t(), ps = papers.filter((p) => ids.includes(p.id));
    if (ps.length < 2) { if (ps.length) await deletePaper(ps[0].id); return; }
    const n = ps.reduce((k, p) => k + (p.notes || 0), 0);
    if (!confirm(T.sel_del_confirm.replace('{k}', ps.length).replace('{n}', n))) return;
    await deleteAll(ps.map((p) => p.id), T.del_done_n.replace('{n}', ps.length));
  }
  async function deleteAll(ids, said) {
    const T = t();
    let r;
    try { r = await api('/api/papers/delete', { papers: ids }); } catch (err) { alert(err.message); return; }
    papers = r.papers;
    for (const id of r.deleted) chosen.delete(id);
    if (r.busy.length) alert(ids.length === 1 ? T.del_busy : T.sel_busy.replace('{n}', r.busy.length));
    if (!r.deleted.length) { renderSidebar(); return; }
    const undo = { ids: r.deleted, said: r.deleted.length === ids.length ? said : T.del_done_n.replace('{n}', r.deleted.length), until: Date.now() + UNDO_MS };
    if (PAPER && r.deleted.includes(PAPER.id)) {   // (the paper being read: the landing says it, and offers it back)
      try { sessionStorage.setItem(UNDO, JSON.stringify(undo)); } catch { /* private window */ }
      location.href = '/';
      return;
    }
    renderSidebar();
    offerUndo(undo);
  }
  function setCollapsed(c) {
    side.classList.toggle('collapsed', c);
    document.documentElement.classList.toggle('sb-open', !c);
    if (!PAPER || pinned) store.set('dr-sidebar', c ? '1' : '0');
    dockState();
  }
  // over a paper the sidebar folds to a rail and opens over the page; on the landing it stays open beside the form.
  // Pinned (its pin, kept in this browser), it stays open beside the paper too, as on the landing, and is folded or
  // opened as there: the page makes room for it (app.css, html.sb-docked)
  // a paper beside the open sidebar: the page starts at its edge, and a window too narrow for the notes in the margins
  // as well has them in the column (html.sb-narrow, as beside the PDF); the reader lays its margins out again
  function dockState() {
    const r = document.documentElement, was = `${r.classList.contains('sb-docked')}${r.classList.contains('sb-narrow')}`;
    const on = !!PAPER && pinned && !side.classList.contains('collapsed') && innerWidth >= 720 && !r.classList.contains('focus');   // (focus: the text alone)
    const sb = parseFloat(getComputedStyle(r).getPropertyValue('--sb')) || 280;
    r.classList.toggle('sb-docked', on);
    r.classList.toggle('sb-narrow', on && innerWidth - sb < 1248);
    if (was !== `${r.classList.contains('sb-docked')}${r.classList.contains('sb-narrow')}`) dispatchEvent(new Event('resize'));
  }
  function setPinned(p) {
    pinned = p;
    store.set(PIN_KEY, p ? '1' : '0');
    if (p) store.set('dr-sidebar', side.classList.contains('collapsed') ? '1' : '0');
    const b = side.querySelector('.sb-pin'), t = I18N.t(uiLang);
    if (b) { b.setAttribute('aria-pressed', String(p)); b.title = p ? t.sb_unpin : t.sb_pin; b.setAttribute('aria-label', b.title); }
    dockState();
  }
  if (PAPER && !pinned) side.classList.add('collapsed');
  else if (store.get('dr-sidebar') === '1' || innerWidth < 720) setCollapsed(true);
  else setCollapsed(false);   // (a paper's page comes with its sidebar folded)
  // (by where the click was when it happened: what it hit may be drawn anew by then, a folder shut, an order picked)
  if (PAPER) document.addEventListener('click', (e) => { if (!pinned && !side.classList.contains('collapsed') && !e.composedPath().includes(side)) setCollapsed(true); });
  dockState();

  // its width, dragged at its edge (or with the arrow keys there) and kept for next time; a double-click goes back to the default
  const SBW = 'textfold.sbw';
  let sbw = +store.get(SBW) || 0;
  function setWidth(w) {
    sbw = Math.round(Math.min(Math.max(280, Math.min(640, innerWidth - 360)), Math.max(220, w)));
    document.documentElement.style.setProperty('--sb', `${sbw}px`);
  }
  if (sbw) setWidth(sbw);
  addEventListener('resize', () => { if (+store.get(SBW)) setWidth(+store.get(SBW)); dockState(); });
  side.addEventListener('pointerdown', (e) => {
    const grip = e.target.closest('.sb-resize');
    if (!grip || e.button !== 0) return;
    e.preventDefault();
    grip.setPointerCapture(e.pointerId);
    document.documentElement.classList.add('sb-resizing');
    const move = (ev) => setWidth(ev.clientX);
    const up = () => {
      grip.removeEventListener('pointermove', move); grip.removeEventListener('pointerup', up); grip.removeEventListener('pointercancel', up);
      document.documentElement.classList.remove('sb-resizing');
      store.set(SBW, String(sbw));
      if (document.documentElement.classList.contains('sb-docked')) dispatchEvent(new Event('resize'));   // (the page beside it is narrower or wider)
    };
    grip.addEventListener('pointermove', move); grip.addEventListener('pointerup', up); grip.addEventListener('pointercancel', up);
  });
  side.addEventListener('dblclick', (e) => {
    if (!e.target.closest('.sb-resize')) return;
    sbw = 0; store.set(SBW, '');
    document.documentElement.style.removeProperty('--sb');
  });
  side.addEventListener('keydown', (e) => {
    if (!e.target.closest('.sb-resize') || !['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    e.preventDefault();
    setWidth((sbw || side.getBoundingClientRect().width) + (e.key === 'ArrowLeft' ? -16 : 16));
    store.set(SBW, String(sbw));
  });

  // ── the library's search and folders ────────────────────────────────
  // a search narrows the list as it is typed: every word, in a title (any language's), an author or an id, accents aside
  function fold(s) { return String(s || '').normalize('NFD').replace(/\p{M}/gu, '').toLowerCase(); }
  function hay(p) { return fold([p.name || '', p.title, ...Object.values(p.titles || {}), ...(p.authors || []), p.id].join(' ')); }
  // the folders inside a folder (null: at the top), in the order they were made; and a folder with all inside it
  function kidsOf(id) { return folders.folders.filter((f) => (f.parent || null) === id); }
  function inside(id) {
    const out = new Set([id]);
    for (let more = true; more;) {
      more = false;
      for (const f of folders.folders) if (out.has(f.parent) && !out.has(f.id)) { out.add(f.id); more = true; }
    }
    return out;
  }
  function applySearch() {
    const list = side.querySelector('.sb-list');
    if (!list) return;
    const words = fold(sbQuery).split(/\s+/).filter(Boolean), on = words.length > 0;
    let any = false;
    list.querySelectorAll('.sb-row').forEach((r) => { const ok = !on || words.every((w) => (r.dataset.q || '').includes(w)); r.hidden = !ok; any = any || ok; });
    list.querySelectorAll('.sb-folder[data-folder]').forEach((f) => {
      const n = f.querySelectorAll('.sb-row:not([hidden])').length;
      f.hidden = on && !n;
      f.classList.toggle('found', on && n > 0);   // (open while it shows what was found)
    });
    list.querySelectorAll('.sb-group').forEach((g) => {   // a kind's heading, while one of its own shows
      let n = g.nextElementSibling, shows = false;
      for (; n && !n.classList.contains('sb-group'); n = n.nextElementSibling) if (n.matches('.sb-row:not([hidden])')) shows = true;
      g.hidden = on && !shows;
    });
    list.classList.toggle('searching', on);
    const none = list.querySelector('.sb-nomatch');
    if (none) none.hidden = !on || any;
  }
  function openSearch() {
    if (side.classList.contains('collapsed')) setCollapsed(false);
    const q = side.querySelector('.sb-q');
    if (q) { q.focus(); q.select(); }
  }
  // ⌘K (Ctrl+K), or / outside a field: the search
  addEventListener('keydown', (e) => {
    const typing = e.target.closest && e.target.closest('input, textarea, select, [contenteditable]');
    const k = e.key === 'k' && (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey;
    if (!k && !(e.key === '/' && !typing && !e.metaKey && !e.ctrlKey && !e.altKey)) return;
    if (document.querySelector('dialog[open]')) return;
    e.preventDefault();
    openSearch();
  });
  side.addEventListener('input', (e) => {
    if (!e.target.matches('.sb-q')) return;
    sbQuery = e.target.value;
    applySearch();
  });
  side.addEventListener('keydown', (e) => {
    if (e.target.matches('.sb-q')) {
      if (e.key === 'Escape') {   // the words out, then the field
        e.preventDefault(); e.stopPropagation();
        if (e.target.value) { e.target.value = ''; sbQuery = ''; applySearch(); } else e.target.blur();
      } else if (e.key === 'Enter' || e.key === 'ArrowDown') {   // the first found: opened, or the arrows go on from it
        const first = side.querySelector('.sb-list .sb-row:not([hidden]) .sb-item');
        if (!first) return;
        e.preventDefault();
        if (e.key === 'Enter') first.click(); else first.focus();
      }
    } else if (e.target.matches('.sb-fname-in')) {
      if (e.key === 'Enter') { e.preventDefault(); saveName(e.target.value); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); editing = null; moving = null; within = null; renderSidebar(); }
    } else if (e.target.matches('.sb-dname-in')) {
      if (e.key === 'Enter') { e.preventDefault(); saveDocName(e.target.value); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); renaming = null; renderSidebar(); }
    } else if (e.target.matches('.sb-list .sb-item') && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      const all = [...side.querySelectorAll('.sb-list .sb-row:not([hidden]) .sb-item')].filter((a) => a.offsetParent);
      const n = all.indexOf(e.target) + (e.key === 'ArrowDown' ? 1 : -1);
      e.preventDefault();
      if (n >= 0 && n < all.length) all[n].focus(); else if (n < 0) side.querySelector('.sb-q').focus();
    }
  });
  side.addEventListener('focusout', (e) => {   // a name left: kept when it has one
    if (e.target.matches('.sb-fname-in') && editing !== null) saveName(e.target.value);
    else if (e.target.matches('.sb-dname-in') && renaming !== null) saveDocName(e.target.value);
  });
  // a folder's name written in place: a new one's (inside `parent`, which opens for it), or the folder's own
  function editName(id, pid = null, parent = null) {
    editing = id; moving = pid; within = parent;
    if (parent && folded.delete(parent)) store.set(FOLDED, [...folded].join(' '));
    renderSidebar();
    const f = side.querySelector('.sb-fname-in');
    if (f) { f.focus(); f.select(); }
  }
  async function saveName(value) {
    const id = editing, name = value.trim(), papers = moving ? [].concat(moving) : [], parent = within, many = Array.isArray(moving);
    editing = null; moving = null; within = null;
    if (many && name) chosen.clear();
    if (name && id) {
      try { folders = await api('/api/folders', id === 'new' ? { op: 'new', name, papers, parent } : { op: 'rename', id, name }); }
      catch (err) { alert(err.message); }
    }
    renderSidebar();
  }
  // a document's name in the library, written in its row: what the reader calls it (its own title stays its own;
  // written back, or emptied, the name goes)
  function editDocName(pid) {
    renaming = pid;
    renderSidebar();
    const f = side.querySelector('.sb-dname-in');
    if (f) { f.focus(); f.select(); }
  }
  async function saveDocName(value) {
    const pid = renaming, p = papers.find((x) => x.id === pid), name = value.trim();
    renaming = null;
    if (!p || name === (p.name || '')) { renderSidebar(); return; }
    const own = [p.title, ...Object.values(p.titles || {})].includes(name);
    if (!p.name && (own || !name)) { renderSidebar(); return; }
    await nameDoc(pid, own ? '' : name);
  }
  async function nameDoc(pid, name) {
    try { papers = await api('/api/papers/name', { paper: pid, name }); }
    catch (err) { alert(err.message); }
    renderSidebar();
    const p = papers.find((x) => x.id === pid);
    if (PAPER && PAPER.id === pid && p && window.__reader && window.__reader.rename) window.__reader.rename(p.name || '');
  }
  async function nestFolder(id, parent) {
    try { folders = await api('/api/folders', { op: 'nest', id, parent }); }
    catch (err) { alert(err.message); }
    renderSidebar();
  }
  async function moveTo(pids, folder) {   // (one, or those picked: they are let go of)
    const many = Array.isArray(pids);
    try { folders = await api('/api/folders', { op: 'move', papers: [].concat(pids), folder }); if (many) chosen.clear(); }
    catch (err) { alert(err.message); }
    renderSidebar();
  }
  async function deleteFolder(id) {
    const T = t(), f = folders.folders.find((x) => x.id === id);
    if (!f || !confirm(T.f_del_confirm.replace('{name}', f.name))) return;
    try { folders = await api('/api/folders', { op: 'delete', id }); }
    catch (err) { alert(err.message); }
    renderSidebar();
  }
  function sortOrder() { const o = store.get(SORT); return ['added', 'opened'].includes(o) ? o : 'kind'; }
  // a folder's actions, in a small menu by its ⋯ (or at a right click on its name)
  function closeMenu() {
    if (!menu) return;
    menu.remove(); menu = null;
    side.querySelectorAll('.menu-on').forEach((x) => x.classList.remove('menu-on'));
    if (stale) renderSidebar();
  }
  // a folder's: a folder inside it, its name, up out of the folder it is in, deleted
  function folderMenu(btn, id, at) {
    const T = t(), f = folders.folders.find((x) => x.id === id), up = f && f.parent ? folders.folders.find((x) => x.id === f.parent) : null;
    showMenu(btn, `<button type="button" role="menuitem" data-act="sub">${FOLDER_NEW}<span class="sb-mname">${esc(T.f_new_sub)}…</span></button>` +
      `<button type="button" role="menuitem" data-act="rename">${esc(T.f_rename)}</button>` +
      (up ? `<button type="button" role="menuitem" data-act="up">${esc(T.f_up)}</button>` : '') +
      `<button type="button" role="menuitem" class="danger" data-act="delete">${esc(T.f_delete)}</button>`,
    (a) => {
      const act = a.dataset.act;
      if (act === 'sub') editName('new', null, id);
      else if (act === 'rename') editName(id);
      else if (act === 'up') nestFolder(id, up.parent || null);
      else deleteFolder(id);
    }, at);
  }
  // the folders as a tree, each with how deep it is: [folder, depth], in the order the sidebar shows them
  function tree(id = null, depth = 0) { return kidsOf(id).flatMap((f) => [[f, depth], ...tree(f.id, depth + 1)]); }
  // a document's, at a right click on it: into a folder (however far down the list it is, however deep), out of its
  // own, into a new one named there; its name in the library; or deleted
  function docMenu(btn, pid, at) {
    const T = t(), here = folders.of[pid] || null, p = papers.find((x) => x.id === pid);
    const fs = tree().map(([f, depth]) => `<button type="button" role="menuitemradio" aria-checked="${f.id === here}" data-to="${f.id}" style="--d:${depth}">` +
      `${FOLDER}<span class="sb-mname">${esc(f.name)}</span></button>`).join('');
    showMenu(btn, (fs ? `<div class="sb-mlabel">${esc(T.f_move_to)}</div>${fs}` : '') +
      (here ? `<button type="button" role="menuitem" data-to="">${esc(T.f_take_out)}</button>` : '') +
      `<button type="button" role="menuitem" data-act="new">${FOLDER_NEW}<span class="sb-mname">${esc(T.f_new)}…</span></button>` +
      `<div class="sb-msep" role="separator"></div>` +
      (chosen.has(pid) ? '' : `<button type="button" role="menuitem" data-act="pick" title="${esc(selTip(T))}">${esc(T.sel_pick)}</button>`) +
      `<button type="button" role="menuitem" data-act="rename">${esc(T.f_rename)}</button>` +
      (p && p.name ? `<button type="button" role="menuitem" data-act="unname">${esc(T.d_unname)}</button>` : '') +
      `<button type="button" role="menuitem" class="danger" data-act="delete">${esc(T.del_paper)}</button>`,
    (a) => {
      if (a.dataset.act === 'new') editName('new', pid);
      else if (a.dataset.act === 'pick') { chosen.add(pid); chosenAt = pid; renderSidebar(); }
      else if (a.dataset.act === 'rename') editDocName(pid);
      else if (a.dataset.act === 'unname') nameDoc(pid, '');
      else if (a.dataset.act === 'delete') deletePaper(pid);
      else if ((a.dataset.to || null) !== here) moveTo(pid, a.dataset.to || null);
    }, at);
  }
  // the documents picked: into a folder (ticked: the one they are all in), out of theirs, into a new one, or deleted
  function chosenMenu(btn, at) {
    const T = t(), ids = [...chosen], ins = new Set(ids.map((id) => folders.of[id] || null)), all = ins.size === 1 ? [...ins][0] : undefined;
    const fs = tree().map(([f, depth]) => `<button type="button" role="menuitemradio" aria-checked="${f.id === all}" data-to="${f.id}" style="--d:${depth}">` +
      `${FOLDER}<span class="sb-mname">${esc(f.name)}</span></button>`).join('');
    showMenu(btn, (fs ? `<div class="sb-mlabel">${esc(T.f_move_to)}</div>${fs}` : '') +
      ([...ins].some(Boolean) ? `<button type="button" role="menuitem" data-to="">${esc(T.f_take_out)}</button>` : '') +
      `<button type="button" role="menuitem" data-act="new">${FOLDER_NEW}<span class="sb-mname">${esc(T.f_new)}…</span></button>` +
      `<div class="sb-msep" role="separator"></div><button type="button" role="menuitem" class="danger" data-act="delete">${esc(T.sel_del_n.replace('{n}', ids.length))}</button>`,
    (a) => {
      if (a.dataset.act === 'new') editName('new', ids);
      else if (a.dataset.act === 'delete') deleteChosen(ids);
      else if ((a.dataset.to || null) !== all) moveTo(ids, a.dataset.to || null);
    }, at);
  }
  const selTip = (T) => T.sel_tip.replace('⌘', MAC ? '⌘' : 'Ctrl');
  function clearChosen() { chosen.clear(); chosenAt = null; renderSidebar(); }
  const shownRows = () => [...side.querySelectorAll('.sb-list .sb-row[data-pid]:not([hidden])')].filter((r) => r.offsetParent);
  // ⌘/Ctrl-click or Shift-click on a document picks it instead of opening it; while some are picked, a click picks or
  // unpicks, and Shift picks every row shown from the one picked last to this one
  side.addEventListener('click', (e) => {
    const a = e.target.closest('.sb-row[data-pid] > .sb-item');
    if (!a || e.button !== 0 || !(e.metaKey || e.ctrlKey || e.shiftKey || chosen.size)) return;
    e.preventDefault(); e.stopPropagation();
    const pid = a.parentElement.dataset.pid, rows = shownRows().map((r) => r.dataset.pid);
    const i = rows.indexOf(chosenAt), j = rows.indexOf(pid);
    if (e.shiftKey && i >= 0 && j >= 0) for (const id of rows.slice(Math.min(i, j), Math.max(i, j) + 1)) chosen.add(id);
    else if (chosen.has(pid)) chosen.delete(pid);
    else chosen.add(pid);
    chosenAt = pid;
    renderSidebar();
    if (e.detail === 0) { const again = side.querySelector(`.sb-row[data-pid="${CSS.escape(pid)}"] > .sb-item`); if (again) again.focus(); }   // (the keyboard's Enter)
  }, true);
  side.addEventListener('mousedown', (e) => { if (e.shiftKey && e.target.closest('.sb-row[data-pid] > .sb-item')) e.preventDefault(); });   // (no text selected by it)
  // Esc lets go of them; ⌘A (Ctrl+A) in the list picks every document it shows
  addEventListener('keydown', (e) => {
    if (side.classList.contains('collapsed') || menu || (e.target.closest && e.target.closest('input, textarea, select, [contenteditable]'))) return;
    if (e.key === 'Escape' && chosen.size && !document.querySelector('dialog[open]')) { e.preventDefault(); clearChosen(); }
    else if (e.key === 'a' && (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && document.activeElement && side.querySelector('.sb-list').contains(document.activeElement)) {
      e.preventDefault();
      for (const r of shownRows()) chosen.add(r.dataset.pid);
      renderSidebar();
    }
  });
  // under its button at the sidebar's right (above it, where there is no room below), or where the right click was
  // (kept inside the sidebar); a pick closes it
  function showMenu(btn, items, pick, at) {
    closeMenu();
    menu = document.createElement('div');
    menu.className = 'sb-menu';
    menu.setAttribute('role', 'menu');
    menu.innerHTML = items;
    side.append(menu);
    btn.classList.add('menu-on');   // (the row it is for stays marked while it is open)
    const r = btn.getBoundingClientRect(), s = side.getBoundingClientRect();
    if (at) {
      menu.style.left = `${Math.max(8, Math.min(at.x - s.left, s.width - menu.offsetWidth - 8))}px`;
      menu.style.top = `${Math.max(8, Math.min(at.y - s.top, s.height - menu.offsetHeight - 8))}px`;
    } else {
      const below = r.bottom - s.top + 4;
      menu.style.top = `${below + menu.offsetHeight + 8 > s.height ? Math.max(8, r.top - s.top - menu.offsetHeight - 4) : below}px`;
      menu.style.right = `${Math.max(8, s.right - r.right)}px`;
    }
    menu.onclick = (e) => {
      const a = e.target.closest('button');
      if (!a) return;
      closeMenu();
      pick(a);
    };
    menu.onkeydown = (e) => {
      const all = [...menu.querySelectorAll('button')], n = all.indexOf(document.activeElement);
      if (e.key === 'Escape') { e.stopPropagation(); closeMenu(); if (btn.isConnected) btn.focus(); }
      else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); all[(n + (e.key === 'ArrowDown' ? 1 : all.length - 1)) % all.length].focus(); }
    };
    (menu.querySelector('[aria-checked="true"]') || menu.querySelector('button')).focus();
  }
  document.addEventListener('click', (e) => { if (menu && !menu.contains(e.target) && !e.target.closest('.sb-fmenu')) closeMenu(); });
  side.addEventListener('contextmenu', (e) => {
    const row = e.target.closest('.sb-row[data-pid]'), head = !row && e.target.closest('.sb-folder[data-folder] > .sb-frow');
    if ((!row || !row.querySelector('.sb-item')) && !(head && head.querySelector('.sb-fmenu'))) { closeMenu(); return; }   // (a name being written: the field's own)
    e.preventDefault();
    const at = e.clientX || e.clientY ? { x: e.clientX, y: e.clientY } : null;   // (none from the keyboard: by the row)
    if (row && chosen.size > 1 && chosen.has(row.dataset.pid)) chosenMenu(row.querySelector('.sb-item'), at);
    else if (row) docMenu(row.querySelector('.sb-item'), row.dataset.pid, at);
    else folderMenu(head.querySelector('.sb-fmenu'), head.parentElement.dataset.folder, at);
  });
  document.addEventListener('contextmenu', (e) => { if (menu && !side.contains(e.target)) closeMenu(); });
  side.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.matches('.sb-newf')) editName('new');
    else if (b.matches('.sb-cmove')) { e.stopPropagation(); if (menu) closeMenu(); else chosenMenu(b); }
    else if (b.matches('.sb-cdel')) deleteChosen([...chosen]);
    else if (b.matches('.sb-cx')) clearChosen();
    else if (b.matches('.sb-order [data-sort]')) { store.set(SORT, b.dataset.sort); renderSidebar(); }
    else if (b.matches('.sb-fhead')) {   // shut, or open again
      const id = b.closest('.sb-folder').dataset.folder;
      if (folded.has(id)) folded.delete(id); else folded.add(id);
      store.set(FOLDED, [...folded].join(' '));
      renderSidebar();
    } else if (b.matches('.sb-fmenu')) {
      e.stopPropagation();
      if (menu) closeMenu(); else folderMenu(b, b.closest('.sb-folder').dataset.folder);
    }
  });
  // a document dragged into a folder (onto its name, or into it), or out of any (onto the rest of the list); a folder
  // dragged into another (not into itself, nor into one inside it), or to the top the same way
  const dropOn = (e) => e.target.closest && (e.target.closest('.sb-folder[data-folder]') || e.target.closest('.sb-out, .sb-loose'));
  const parentOf = (id) => ((folders.folders.find((f) => f.id === id) || {}).parent || null);
  // where the thing dragged may go: the folder (null: out of any, to the top); undefined where it is already, or cannot
  function dropTo(at) {
    const to = at.matches('.sb-folder') ? at.dataset.folder : null;
    if (draggingFolder) return parentOf(draggingFolder) === to || (to && inside(draggingFolder).has(to)) ? undefined : to;
    return dragging.every((id) => (folders.of[id] || null) === to) ? undefined : to;
  }
  // held near the list's top or bottom (or over the search above it), the list scrolls, the faster the nearer its edge:
  // a document far down reaches the folders at the top
  const EDGE = 48;
  let edgeStep = 0, edgeAt = 0, edgeRaf = 0;
  function edgeScroll(e) {
    const list = side.querySelector('.sb-list'), r = list.getBoundingClientRect();
    const up = r.top + EDGE - e.clientY, down = e.clientY - (r.bottom - EDGE);
    edgeStep = up > 0 ? -Math.ceil(14 * Math.min(1, up / EDGE)) : down > 0 ? Math.ceil(14 * Math.min(1, down / EDGE)) : 0;
    edgeAt = performance.now();
    if (!edgeStep || edgeRaf) return;
    const step = () => {   // (until the drag ends, leaves the sidebar or stops near the edge)
      if (!(dragging || draggingFolder) || !edgeStep || performance.now() - edgeAt > 700) { edgeRaf = 0; return; }
      list.scrollTop += edgeStep;
      edgeRaf = requestAnimationFrame(step);
    };
    edgeRaf = requestAnimationFrame(step);
  }
  side.addEventListener('dragstart', (e) => {
    const row = e.target.closest && e.target.closest('.sb-row[data-pid]');
    const head = !row && e.target.closest && e.target.closest('.sb-folder[data-folder] > .sb-frow');
    if (head) {
      draggingFolder = head.parentElement.dataset.folder;
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('application/x-textfold-folder', draggingFolder);
      side.classList.add('dragging');
      side.classList.toggle('from-folder', !!parentOf(draggingFolder));
      return;
    }
    if (!row) return;
    const pid = row.dataset.pid;
    dragging = chosen.size > 1 && chosen.has(pid) ? [...chosen] : [pid];   // (a picked one takes the others along)
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('application/x-textfold-paper', dragging.join(' '));
    if (dragging.length > 1) {   // what the pointer carries: how many
      const img = h('div', 'sb-dragimg', esc(t().sel_n.replace('{n}', dragging.length)));
      document.body.append(img);
      e.dataTransfer.setDragImage(img, 14, 14);
      setTimeout(() => img.remove());
    }
    side.classList.add('dragging');
    side.classList.toggle('from-folder', dragging.some((id) => folders.of[id]));
  });
  side.addEventListener('dragend', () => {
    dragging = null; draggingFolder = null; edgeStep = 0;
    side.classList.remove('dragging', 'from-folder');
    side.querySelectorAll('.drop').forEach((x) => x.classList.remove('drop'));
  });
  side.addEventListener('dragover', (e) => {
    if (dragging || draggingFolder) edgeScroll(e);
    const at = (dragging || draggingFolder) && dropOn(e);
    side.querySelectorAll('.drop').forEach((x) => { if (x !== at) x.classList.remove('drop'); });
    if (!at || dropTo(at) === undefined) return;   // (where it is already)
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    at.classList.add('drop');
  });
  side.addEventListener('dragleave', (e) => {
    if (!side.contains(e.relatedTarget)) edgeStep = 0;
    const at = dropOn(e);
    if (at && !at.contains(e.relatedTarget)) at.classList.remove('drop');
  });
  side.addEventListener('drop', (e) => {
    const at = (dragging || draggingFolder) && dropOn(e);
    if (!at) return;
    e.preventDefault();
    const pids = dragging, fid = draggingFolder, to = dropTo(at);
    dragging = null; draggingFolder = null;
    if (to === undefined) return;
    if (fid) nestFolder(fid, to); else moveTo(pids.length > 1 ? pids : pids[0], to);
  });

  // ── jobs ─────────────────────────────────────────────────────────────
  const watchers = new Map();   // job id -> callback(job)
  let polling = null;
  async function poll() {
    polling = null;
    try {
      const before = new Map(jobs.map((j) => [j.id, j.status]));
      jobs = await api('/api/jobs');
      let finished = false;
      for (const j of jobs) {
        const cb = watchers.get(j.id);
        if (cb) cb(j);
        if (before.get(j.id) !== j.status && (j.status === 'done' || j.status === 'error' || j.status === 'stopped')) finished = true;
      }
      if (finished) papers = await api('/api/papers');
      if (menu) stale = true; else renderSidebar();   // (a menu stays open while a job goes on)
    } catch { /* the server restarted; try again */ }
    if (jobs.some(live) || watchers.size) polling = setTimeout(poll, 1200);
  }
  function startPolling() { if (!polling) polling = setTimeout(poll, 400); }
  function watch(job, onUpdate) {
    watchers.set(job.id, (j) => { onUpdate(j); if (!live(j)) watchers.delete(j.id); });
    jobs = [...jobs.filter((j) => j.id !== job.id), job];
    renderSidebar(); onUpdate(job); startPolling();
    return job;
  }
  async function submit(paper, lang, force, onUpdate) {
    return watch(await api('/api/generate', { paper, lang, force: !!force }), onUpdate);
  }
  // what a PDF says it is, read in the browser: an arXiv id from its margin stamp, a DOI from its metadata or its
  // first page. Only the first page: the second is often the references, full of other papers' ids.
  async function refsOf(file) {
    const lib = await import('/static/vendor/pdfjs/pdf.min.mjs');
    lib.GlobalWorkerOptions.workerSrc = '/static/vendor/pdfjs/pdf.worker.min.mjs';
    const loading = lib.getDocument({ data: new Uint8Array(await file.arrayBuffer()), isEvalSupported: false });
    try {
      const doc = await loading.promise;
      const meta = await doc.getMetadata().catch(() => ({}));
      const info = meta.info || {}, xmp = meta.metadata;
      const said = [info.Subject, info.Keywords, info.doi, xmp && xmp.get && xmp.get('prism:doi'), xmp && xmp.get && xmp.get('dc:identifier')]
        .filter((x) => typeof x === 'string').join(' ');
      const page = (await (await doc.getPage(1)).getTextContent()).items.map((i) => i.str).join(' ');
      const refs = [];
      const add = (r) => { if (r && !refs.includes(r)) refs.push(r); };
      for (const m of `${said} ${page}`.matchAll(/arXiv:\s?(\d{4}\.\d{4,5})(?:v\d+)?|arxiv\.org\/(?:abs|pdf)\/(\d{4}\.\d{4,5})/gi)) add(m[1] || m[2]);
      for (const m of `${said} ${page}`.matchAll(/\b(10\.\d{4,9}\/[^\s"<>]+)/g)) add(m[1].replace(/[.,;:)\]]+$/, ''));
      return refs.slice(0, 4);
    } finally { loading.destroy(); }
  }
  // a file to the server: a PDF with what it says it is (the paper online, or a conversion, or a wait for the engine);
  // a Word file, an e-book, a picture, converted the same way
  async function uploadFile(file, lang) {
    let refs = [];
    if (isPdf(file)) try { refs = await refsOf(file); } catch { /* a PDF pdf.js cannot open: the engine may still read it */ }
    const r = await fetch('/api/files', { method: 'POST', body: file, headers: { 'Content-Type': file.type || 'application/octet-stream',
      'X-Name': encodeURIComponent(file.name), 'X-Lang': lang, 'X-Refs': encodeURIComponent(JSON.stringify(refs)) } });
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || r.statusText);
    return d;
  }
  const isPdf = (file) => /\.pdf$/i.test(file.name) || file.type === 'application/pdf';
  // what TextFold opens: what the PDF engine reads (engine.FILES), and Markdown and text, read as they are
  const ENGINE_EXT = ['pdf', 'docx', 'doc', 'rtf', 'pptx', 'ppt', 'xlsx', 'xls', 'odt', 'ods', 'odp', 'epub', 'html', 'htm', 'mhtml', 'mht',
    'csv', 'tsv', 'ofd', 'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'tif', 'tiff', 'jp2'];
  const TEXT_EXT = ['md', 'markdown', 'mdown', 'txt'];
  const extOf = (name) => ((/\.([a-z0-9]+)$/i.exec(name || '') || [])[1] || '').toLowerCase();
  const forEngine = (file) => isPdf(file) || ENGINE_EXT.includes(extOf(file.name));
  const isText = (file) => TEXT_EXT.includes(extOf(file.name));
  async function importFile(file, lang) {
    if (file.size > 100 * 1024 * 1024) throw new Error('too-large');
    if (forEngine(file)) return uploadFile(file, lang);
    if (!isText(file)) throw new Error('not-supported');
    const text = await file.text();
    if (!text.trim()) throw new Error('empty');
    return api('/api/import', { name: file.name, text });
  }
  // File selection, drop and browser tools share the upload dispatch and PDF reference discovery.
  window.TFUploads = { importFile, isText,
    language: () => document.getElementById('lang')?.value || window.__reader?.lang || readLang,
    hasModel: () => !!modelName,
    supportsLanguage: lang => !!(BOOT.langs || DATA?.langs || I18N.NATIVE)?.[lang] };
  // several files at once (dropped, or chosen together): each goes to the server in turn, one upload at a time, and its
  // job waits in the server's queues, which read the files and write their levels one after another (the sidebar shows
  // each, with its stop). Files given while others are still going up wait for them. Says what came of them: how many
  // were taken, the uploads that wait for the PDF engine, and each file not taken with why.
  let adding = Promise.resolve();
  function addFiles(files, lang, step) {
    const run = adding.then(async () => {
      const got = { taken: 0, engine: [], skipped: [] };
      for (const [k, file] of files.entries()) {
        step(k, file);
        const why = !forEngine(file) && !isText(file) ? t().md_only
          : file.size > 100 * 1024 * 1024 ? (isPdf(file) ? t().pdf_large : t().file_large)
          : isText(file) && !modelName ? t().fl_no_model : '';
        if (why) { got.skipped.push([file.name, why]); continue; }
        try {
          if (isText(file)) {
            const r = await importFile(file, lang);
            await submit(r.paper, lang, false, () => {});
          } else {
            const d = await importFile(file, lang);
            if (d.needs === 'engine') { got.engine.push(d.upload); continue; }
            watch(d.job, () => {});
          }
          got.taken++;
          // the library again, so that the sidebar names a stored file by its title while its job waits
          try { papers = await api('/api/papers'); if (menu) stale = true; else renderSidebar(); } catch { /* the next poll does */ }
        } catch (x) { got.skipped.push([file.name, errText(x.message)]); }
      }
      return got;
    });
    adding = run.catch(() => {});
    return run;
  }
  // what came of them, as lines: [heading, the lines under it, the files not taken (name: why), a few at most]
  function filesSaid(got, n, T) {
    const lines = [got.taken ? (modelName ? T.fl_queue : T.fl_read) : '', got.engine.length ? T.fl_engine.replace('{n}', got.engine.length) : ''].filter(Boolean);
    const not = got.skipped.slice(0, 6).map(([name, why]) => `${name}: ${why}`);
    if (got.skipped.length > 6) not.push('…');
    return [T.fl_added.replace('{n}', got.taken + got.engine.length).replace('{m}', n), lines, not];
  }
  // the PDF engine installed for the files waiting for it (the server converts each once it is in); the sidebar shows it
  async function installForFiles() {
    watch(await api('/api/engine/install', {}), () => {});
  }
  // OpenRouter's consent page. In the browser: this tab goes there and comes back with the key. Inside the app: the
  // system browser (where the reader is signed in) goes there, and the app waits for the key, then offers the models.
  async function connectOpenRouter(back) {
    const desktop = !!window.desktop;
    const r = await api('/api/openrouter/start', { back, desktop });
    if (!desktop) { location.href = r.url; return; }
    window.desktop.openExternal(r.url);
    for (let k = 0; k < 150; k++) {   // five minutes for a person in another window
      await new Promise((res) => setTimeout(res, 2000));
      if ((await api('/api/openrouter/minted')).minted) { openSettings({ minted: true }); return; }
    }
  }
  const errText = (msg) => (msg === 'no-html' ? t().no_html : msg === 'bad-ref' ? t().bad_ref : msg === 'no-model' ? t().no_model
    : msg === 'not-markdown' || msg === 'empty' || msg === 'not-supported' ? t().md_only : msg === 'no-fulltext' ? t().no_fulltext
    : msg === 'no-readme' ? t().no_readme : msg === 'github-limit' ? t().github_limit : msg === 'needs-engine' ? t().pdf_needs
    : msg === 'busy' ? t().engine_busy : msg === 'not-pdf' ? t().not_pdf : msg === 'too-large' ? t().pdf_large
    : msg === 'bad-file' ? t().file_bad : msg === 'no-file' ? t().no_file : msg === 'not-running' ? t().z_off : msg === 'api-off' ? t().z_api_off
    : msg === 'web-notext' ? t().web_notext : msg === 'web-pdf' ? t().web_pdf : msg === 'web-blocked' ? t().web_blocked : msg);

  // the corner where the page says what is under way, one above the other: a generation, the app's update. Each toast
  // is a small card: a sign at the left, what happens beside it, then its line, its progress or its buttons below.
  const toasts = h('div', 'toasts'); document.body.append(toasts);
  const SIGN = {
    done: '<path d="M5.5 10.5l3 3 6-6.5"/>',
    down: '<path d="M10 4.5v8.5M6.5 9.5l3.5 3.5 3.5-3.5M5.5 16h9"/>',
    restart: '<path d="M15.5 10a5.5 5.5 0 1 1-1.7-4M14.5 3.5v3h-3"/>',
    warn: '<path d="M10 6.5V11M10 13.8v.2"/><circle cx="10" cy="10" r="7"/>',
    out: '<path d="M8.5 5.5h-3v9h9v-3M11.5 4.5h4v4M15.5 4.5l-6 6"/>',
    x: '<path d="M6 6l8 8M14 6l-8 8"/>',
    bin: '<path d="M4.5 6h11M8.2 6V4.5h3.6V6M6 6l.8 9.5h6.4L14 6"/>',
  };
  const svg = (k) => `<svg viewBox="0 0 20 20" aria-hidden="true">${SIGN[k]}</svg>`;
  const sign = (k, tone) => `<span class="g-ico${tone ? ` ${tone}` : ''}">${k === 'mark' ? MARK : svg(k)}</span>`;
  const shut = (label) => `<button type="button" class="g-x" aria-label="${esc(label)}" title="${esc(label)}">${svg('x')}</button>`;

  // ── a delete's Undo: for ten seconds the corner says what went, with a button that brings it back as it was (in
  //    its folder, under its name); the paper being read, deleted, is offered back on the page it goes to ──
  const UNDO = 'textfold.undo', UNDO_MS = 10000;
  const undoBox = h('div', 'gen-toast undo-toast');
  undoBox.hidden = true; toasts.append(undoBox);
  let undoTimer = 0;
  function offerUndo(u) {
    const T = t(), close = () => { undoBox.querySelector('.g-x').onclick = () => { undoBox.hidden = true; }; };
    clearTimeout(undoTimer);
    undoBox.hidden = false;
    undoBox.innerHTML = `${sign('bin')}<b>${esc(u.said)}</b>${shut(T.close)}<span class="g-acts"><button type="button" class="g-go">${esc(T.undo)}</button></span>`;
    close();
    const b = undoBox.querySelector('.g-go');
    b.onclick = async () => {
      b.disabled = true;
      clearTimeout(undoTimer);
      try {
        const r = await api('/api/papers/restore', { papers: u.ids });
        papers = r.papers; folders = r.folders;
        renderSidebar();
        undoBox.innerHTML = `${sign('done', 'ok')}<b>${esc(T.undone)}</b>`;
        undoTimer = setTimeout(() => { undoBox.hidden = true; }, 2500);
      } catch (err) {
        undoBox.innerHTML = `${sign('warn', 'warn')}<b>${esc(err.message)}</b>${shut(T.close)}`;
        close();
      }
    };
    undoTimer = setTimeout(() => { undoBox.hidden = true; }, Math.max(0, u.until - Date.now()));
  }
  try {
    const u = JSON.parse(sessionStorage.getItem(UNDO) || 'null');
    sessionStorage.removeItem(UNDO);
    if (u && Array.isArray(u.ids) && u.until > Date.now()) offerUndo(u);
  } catch { /* private window */ }

  // ── the app's own updates (desktop/main.js): a newer release, downloaded and installed when the reader says ──
  if (window.desktop && window.desktop.onUpdate) {
    const D = window.desktop, box = h('div', 'gen-toast upd-toast');
    let last = null, fade = 0;
    box.hidden = true; toasts.append(box);
    const SAID = { latest: 'up_latest', 'check-failed': 'up_check_failed', dev: 'up_dev' };   // answers to a check by hand
    const show = (s) => {
      last = s;
      clearTimeout(fade);
      const T = t(), v = (x) => esc(x.replace('{v}', (s && s.version) || ''));
      box.hidden = !s || s.dismissed || !['updated', 'available', 'downloading', 'ready', 'failed', ...Object.keys(SAID)].includes(s.kind);
      if (box.hidden) return;
      const x = shut(T.close);
      const go = (label, primary) => `<span class="g-acts"><button type="button" class="g-go${primary ? ' primary' : ''}">${esc(label)}</button></span>`;
      const link = (href, label) => `<span class="g-acts"><a class="g-go" href="${esc(href)}" target="_blank" rel="noopener">${esc(label)}${svg('out')}</a></span>`;
      box.innerHTML = SAID[s.kind] ? `${sign(s.kind === 'latest' ? 'done' : 'warn', s.kind === 'latest' ? 'ok' : 'warn')}<b>${esc(T[SAID[s.kind]])}</b>${x}`
        : s.kind === 'updated' ? `${sign('done', 'ok')}<b>${v(T.up_done)}</b>${x}${link(s.notes, T.up_whatsnew)}`
        : s.kind === 'available' ? `${sign('down')}<b>${v(T.up_new)}</b>${x}${go(T.up_get, true)}`
        : s.kind === 'downloading' ? `${sign('down')}<b>${esc(T.up_getting.replace('{p}', s.percent || 0))}</b><span class="sb-prog"><i style="width:${Math.max(3, s.percent || 0)}%"></i></span>`
        : s.kind === 'ready' ? `${sign('restart')}<b>${v(T.up_ready)}</b>${x}${go(T.up_restart, true)}`
        : `${sign('warn', 'warn')}<b>${esc(T.up_failed)}</b>${x}${go(T.up_retry)}`;
      if (s.transient) fade = setTimeout(() => { box.hidden = true; }, 6000);
      const close = box.querySelector('.g-x');
      if (close) close.onclick = () => { box.hidden = true; if (!s.transient) D.dismissUpdate(); };
      const b = box.querySelector('button.g-go');
      if (b) b.onclick = () => { b.disabled = true; if (s.kind === 'ready') D.installUpdate(); else D.downloadUpdate(); };
    };
    D.onUpdate(show);
    D.updateState().then(show).catch(() => {});
    document.addEventListener('tf-lang', () => show(last));
  }

  // ── on a reader page: generate a language, or regenerate the current one ──
  if (PAPER) {
    const toast = h('div', 'gen-toast'); toast.hidden = true; toasts.append(toast);
    // a Markdown file dropped on a paper's page: stored, then generated from the landing (?start=)
    let over = 0;
    const hasFile = (e) => [...((e.dataTransfer && e.dataTransfer.types) || [])].includes('Files');
    const say = (msg, ms) => {   // (ms: a message that goes by itself, which says what went wrong)
      toast.hidden = false;
      toast.innerHTML = `${ms ? sign('warn', 'warn') : sign(msg === t().drop_md ? 'down' : 'mark')}<b>${esc(msg)}</b>`;
      if (ms) setTimeout(() => { toast.hidden = true; }, ms);
    };
    document.addEventListener('dragenter', (e) => { if (hasFile(e)) { over++; say(t().drop_md); } });
    document.addEventListener('dragleave', () => { if (--over <= 0) { over = 0; if (toast.textContent === t().drop_md) toast.hidden = true; } });
    document.addEventListener('dragover', (e) => { if (hasFile(e)) e.preventDefault(); });
    // several dropped here: they go in from this page, which stays open, and the toast says what came of them
    async function dropFiles(files) {
      const T = t();
      const got = await addFiles(files, readLang, (k, file) => {
        toast.hidden = false;
        toast.innerHTML = `${sign('down')}<b>${esc(T.fl_adding.replace('{k}', k + 1).replace('{n}', files.length))}</b>` +
          `<span class="g-stage">${esc(file.name)}</span><span class="sb-prog"><i style="width:${Math.round((100 * k) / files.length) + 4}%"></i></span>`;
      });
      const [head, lines, not] = filesSaid(got, files.length, T);
      toast.hidden = false;
      toast.innerHTML = `${got.skipped.length && !got.taken && !got.engine.length ? sign('warn', 'warn') : sign('mark')}<b>${esc(head)}</b>${shut(T.close)}` +
        lines.map((l) => `<span class="g-note">${esc(l)}</span>`).join('') +
        (not.length ? `<span class="g-note">${esc(T.fl_skipped)}<br>${not.map(esc).join('<br>')}</span>` : '') +
        (got.engine.length ? `<span class="g-acts"><button type="button" class="g-go primary">${esc(T.engine_install)}</button></span>` : '');
      toast.querySelector('.g-x').onclick = () => { toast.hidden = true; };
      const go = toast.querySelector('.g-go');
      if (go) go.onclick = async () => { go.disabled = true; try { await installForFiles(); toast.hidden = true; } catch (x) { go.disabled = false; say(errText(x.message), 4000); } };
    }
    document.addEventListener('drop', async (e) => {
      if (!e.dataTransfer.files.length) return;
      e.preventDefault(); over = 0;
      if (e.dataTransfer.files.length > 1) { dropFiles([...e.dataTransfer.files]); return; }
      const file = e.dataTransfer.files[0];
      if (forEngine(file)) {
        say(isPdf(file) ? t().pdf_reading : t().file_reading);
        try {
          const d = await importFile(file, readLang);
          if (d.paper) { location.href = `/p/${d.paper}?lang=${encodeURIComponent(d.job.lang)}`; return; }   // read already
          const q = new URLSearchParams({ t: file.name });
          if (d.needs === 'engine') q.set('engine', d.upload); else { q.set('follow', d.job.id); if (d.via === 'online') q.set('ref', d.ref); }
          location.href = `/?${q}`;
        } catch (x) { say(errText(x.message), 4000); }
        return;
      }
      if (!isText(file)) { say(t().md_only, 4000); return; }
      try {
        const r = await importFile(file, uiLang);
        location.href = `/?start=${encodeURIComponent(r.paper)}&t=${encodeURIComponent(r.title)}`;
      } catch (x) { say(errText(x.message), 4000); }
    });
    // a generation of this paper in the toast: its stage and a stop button; done, the page again, at the paragraph being
    // read (the reader picks it up from sessionStorage). `note` says why the page is here before its levels are.
    function toastFor(lang, note) {
      const T = t(), name = nameOf(lang);
      toast.hidden = false;
      toast.innerHTML = `${sign('mark')}<b>${esc(T.generating)} · ${esc(name)}</b><span class="g-stage">${esc(T.stages.queued)}</span>` +
        `<span class="sb-prog"><i style="width:4%"></i></span>${note ? `<span class="g-note">${esc(note)}</span>` : ''}`;
      const closable = (head, line, bad) => {
        toast.innerHTML = `${bad ? sign('warn', 'warn') : sign('mark')}<b>${esc(head)} · ${esc(name)}</b>${shut(t().close)}${line ? `<span class="g-stage">${esc(line)}</span>` : ''}`;
        toast.querySelector('.g-x').onclick = () => { toast.hidden = true; };
      };
      const update = (j) => {
        const T2 = t();
        if (j.status === 'error') { closable(T2.failed, errText(j.error), true); return; }
        if (j.status === 'stopped') { closable(T2.stopped, ''); return; }
        if (!toast.querySelector('.g-stop')) {
          toast.insertAdjacentHTML('beforeend', `<button type="button" class="g-stop">${esc(T2.stop)}</button>`);
          toast.querySelector('.g-stop').onclick = () => stopJob(j.id);
        }
        toast.querySelector('.g-stage').textContent = stageOf(j, T2);
        toast.querySelector('.g-stop').disabled = !!j.stopping;
        toast.querySelector('.sb-prog i').style.width = `${pct(j)}%`;
        if (j.status === 'done') reopen(j.lang);
      };
      update.fail = (e) => closable(t().failed, errText(e.message), true);
      return update;
    }
    function reopen(lang) {
      const r = document.getElementById('doc').getBoundingClientRect(), R = window.__reader;
      let u = null;
      for (const f of [0.3, 0.36, 0.42, 0.24]) {   // the reading line, or near it when it falls between paragraphs
        const at = document.elementFromPoint(r.left + Math.min(r.width / 2, 320), innerHeight * f);
        u = at && at.closest('.u[data-u]');
        if (u) break;
      }
      try { if (u) sessionStorage.setItem('pf-keep', JSON.stringify({ pid: PAPER.id, uid: u.dataset.u, top: u.getBoundingClientRect().top, level: R && R.level })); } catch { /* private window */ }
      if (R && lang !== R.lang) { store.set(UI_KEY, lang); tellLang(lang); }   // a language just made: the reader's goes with it
      location.href = `/p/${PAPER.id}?lang=${encodeURIComponent(lang)}`;
    }
    const src = PAPER.source || {};
    if ((src.kind === 'pdf' && src.tier !== 'accurate') || (src.kind === 'file' && src.tier === 'fast')) {
      const acc = h('button', 'cmpbtn accbtn');
      acc.type = 'button';
      acc.textContent = t().convert_acc;
      acc.title = `${t().conv_fast_note} ${t().tier_accurate}`;
      const bar = document.querySelector('.topbar .cmpbtn') || document.querySelector('.topbar .langpick');
      if (bar) bar.before(acc);
      acc.onclick = async () => {
        const T = t();
        if (!confirm(`${T.convert_acc}? ${T.tier_accurate}.`)) return;
        acc.disabled = true;
        const lang = (window.__reader && window.__reader.lang) || 'en', update = toastFor(lang);
        try { watch(await api('/api/convert', { paper: PAPER.id, tier: 'accurate', lang }), update); }
        catch (x) { update.fail(x); acc.disabled = false; }
      };
    }
    // A translation stored with paragraphs it has none for (PAPER.gaps: a call that failed, a paragraph the model left
    // out; or made before TextFold asked for them again): they are asked for as it is opened, with the model in use, the
    // rest kept. Once a visit for as many as there are, so one that will not translate is not asked for at every
    // opening; the language menu asks again (asked).
    function fillGaps(lang, asked) {
      const n = (PAPER.gaps || {})[lang], key = `tf-gaps:${PAPER.id}:${lang}`;
      if (!n || jobFor(PAPER.id) || !modelName) return;
      let tried = null;
      try { tried = sessionStorage.getItem(key); } catch { /* private window */ }
      if (!asked && tried === String(n)) return;
      try { sessionStorage.setItem(key, String(n)); } catch { /* private window */ }
      const update = toastFor(lang, t().gaps_filling.replace('{n}', n));
      submit(PAPER.id, lang, false, update).catch(update.fail);
    }
    window.DRApp = {
      onLang(lang, readerLang) {   // (readerLang: the reader's language, which a switch of the paper's moves)
        if (readerLang && readerLang !== readLang && READ[readerLang]) { readLang = readerLang; uiLang = I18N.uiFor(readerLang); tellLang(readerLang); }
        renderSidebar();
        fillGaps(lang);
      },
      generate(lang, force) {
        const T = t(), name = nameOf(lang);
        if (force && !confirm(`${T.regen} · ${name}?`)) return;
        const update = toastFor(lang);
        submit(PAPER.id, lang, force, update).catch(update.fail);
      },
      fill(lang) { fillGaps(lang, true); },
    };
    const modelsIn = api('/api/models').then((d) => { modelData = d; modelName = d.selectedName || ''; askName = d.askName || ''; renderSidebar(); }).catch(() => {});
    api('/api/papers').then((p) => { papers = p; renderSidebar(); }).catch(() => {});
    api('/api/folders').then((f) => { folders = f; renderSidebar(); }).catch(() => {});
    api('/api/jobs').then((j) => {
      jobs = j;
      renderSidebar();
      // a generation of this paper under way (the landing opens a paper as soon as it can be read): followed here
      const mine = jobFor(PAPER.id);
      if (mine) {
        const update = toastFor(mine.lang, t().read_first);
        watchers.set(mine.id, (x) => { update(x); if (!live(x)) watchers.delete(x.id); });
        update(mine);
      } else modelsIn.then(() => fillGaps((window.__reader && window.__reader.lang) || new URLSearchParams(location.search).get('lang') || 'en'));
      if (j.some(live)) startPolling();
    }).catch(() => {});
  }

  // ── the landing ──────────────────────────────────────────────────────
  const landing = document.getElementById('landing');
  function renderLanding() {
    const T = t();
    const storageError = document.getElementById('storage-error');
    if (storageError) { storageError.hidden = !BOOT.storageError; storageError.textContent = BOOT.storageError ? `${T.cloud_unavailable} ${BOOT.storageError}` : ''; }
    document.documentElement.lang = I18N.htmlLang(uiLang);
    document.getElementById('h-tagline').textContent = T.hero;
    document.getElementById('ref').placeholder = T.placeholder;
    document.getElementById('ref').setAttribute('aria-label', T.placeholder);
    document.getElementById('l-lang').textContent = T.read_in;
    document.getElementById('lang').value = readLang;
    const ui = document.getElementById('ui-lang');
    ui.value = uiLang;
    document.getElementById('ui-lang-box').title = T.language;
    ui.setAttribute('aria-label', T.language);
    document.getElementById('go-t').textContent = T.generate;
    document.getElementById('sources').textContent = T.sources;
    document.getElementById('sources').title = T.sources_tip;
    document.getElementById('dropzone-t').textContent = T.drop_zone;
    document.getElementById('dropzone-k').textContent = T.drop_types;
    document.getElementById('zlib-open-t').textContent = T.z_lib;
    document.getElementById('ui-help-t').textContent = T.tut_open;
    document.getElementById('ui-help').title = T.tut_open;
    document.getElementById('open-settings').textContent = T.settings;
    document.getElementById('model-label').textContent = modelLabel();
    renderConnect();
  }
  // No model yet (no agent on this computer, no provider): the ways in, on the landing itself. OpenRouter's one-click
  // authorisation (free models); the ChatGPT plan through the local bridge, started from here; or the settings.
  let bridgeStarting = false, bridgeState = null;
  function renderConnect() {
    const box = document.getElementById('connect');
    if (!box) return;
    box.hidden = !!modelName;
    box.classList.toggle('cn-wait', !modelName && modelsLooking);
    if (box.hidden) return;
    if (modelsLooking) {   // no call to connect a model before the ones on this computer are known
      box.innerHTML = `<p class="cn-looking">${esc(t().cn_looking)}</p>`;
      return;
    }
    const T = t(), br = BOOT.bridge || {};
    box.innerHTML = `<div class="cn-t">${esc(T.cn_title)}</div><p>${esc(T.cn_sub)}</p><div class="cn-ways">` +
      `<button type="button" class="primary cn-or">${esc(T.cn_or)}</button>` +
      (br.node || br.running ? `<button type="button" class="cn-gpt"${bridgeStarting ? ' disabled' : ''}>${esc(bridgeStarting ? T.bridge_starting : T.cn_gpt)}</button>` : '') +
      `</div>${bridgeNote(bridgeState, T)}<button type="button" class="linkbtn cn-more">${esc(T.cn_more)}</button>`;
    box.querySelector('.cn-or').onclick = async (e) => { e.target.disabled = true; await connectOpenRouter('/'); e.target.disabled = false; };
    box.querySelector('.cn-more').onclick = () => openSettings({ add: true });
    const g = box.querySelector('.cn-gpt');
    if (g) g.onclick = async () => {
      bridgeStarting = true; bridgeState = null; renderConnect();
      try { bridgeState = await startBridge((st) => { bridgeState = st; renderConnect(); }); } catch (x) { bridgeState = { error: x.message }; }
      bridgeStarting = false;
      const d = await refreshModels();
      modelName = d.selectedName || '';
      askName = d.askName || '';
      renderSidebar(); renderLanding();
    };
  }
  if (landing) {
    const sel = document.getElementById('lang');
    for (const [code, name] of Object.entries(READ)) sel.append(Object.assign(h('option'), { value: code, textContent: name }));
    sel.value = readLang;
    // the language, top right, and the one to read in, beside the bar: one language, so either moves both (one the
    // interface has no words for leaves it in English)
    const ui = document.getElementById('ui-lang');
    for (const code of I18N.CODES) ui.append(Object.assign(h('option'), { value: code, textContent: I18N.NATIVE[code] }));
    ui.value = uiLang;
    sel.onchange = () => setReaderLang(sel.value);
    if (modelData && modelData.lang == null) tellLang(readLang);   // (one chosen before the server kept it)
    ui.onchange = () => setReaderLang(ui.value);
    document.getElementById('open-settings').onclick = openSettings;
    const help = document.getElementById('ui-help');
    if (window.TFTutorial) help.onclick = () => window.TFTutorial.open(uiLang); else help.hidden = true;
    const form = document.getElementById('newform'), err = document.getElementById('err'), card = document.getElementById('jobcard');
    const fail = (msg) => { err.textContent = msg; err.hidden = false; };
    // a paper (an arXiv link, or a document just opened) into the job card, then into the reader
    let latest = null;   // the paper the card shows: started last. One started before keeps going in the sidebar.
    function onCard(label, mine, note) {
      const cb = (j) => {
        if (latest !== mine) return;
        const T = t(), name = esc(label || jobName(j, T)), in_ = j.lang ? ` · ${esc(nameOf(j.lang))}` : '';
        if (j.status === 'moved' && j.next) { watchers.set(j.next, cb); startPolling(); return; }   // (an arXiv paper without HTML: its PDF converts)
        if (j.status === 'error' && j.error === 'needs-engine') { engineCard(j.upload, label || j.title || j.pid); return; }
        if (j.status === 'error') { card.innerHTML = `<div class="jc-t">${esc(T.failed)} · ${name}</div><div class="jc-e">${esc(errText(j.error))}</div>`; return; }
        if (j.status === 'stopped') {
          card.innerHTML = `<div class="jc-head"><div class="jc-t">${name}${in_}</div>${j.kind === 'generate' ? `<button type="button" class="jc-go">${esc(T.resume)}</button>` : ''}</div>
            <div class="jc-s">${esc(T.stopped)}</div>`;
          if (j.kind === 'generate') card.querySelector('.jc-go').onclick = () => start(j.pid, label || j.title, j.lang);
          return;
        }
        card.innerHTML = `<div class="jc-head"><div class="jc-t">${name}${in_}</div>
            <button type="button" class="jc-stop"${j.stopping ? ' disabled' : ''}>${esc(T.stop)}</button></div>
          <div class="jc-s">${esc(stageOf(j, T))}</div>
          <span class="sb-prog"><i style="width:${pct(j)}%"></i></span>${note ? `<div class="jc-n">${esc(note)}</div>` : ''}`;
        card.querySelector('.jc-stop').onclick = () => stopJob(j.id);
        // readable (parsed, or converted): the paper opens in its own words now, and its levels come in while it is read
        if (j.pid && (j.status === 'done' || (j.readable && j.status === 'running'))) location.href = `/p/${j.pid}?lang=${encodeURIComponent(j.lang || sel.value)}`;
      };
      return cb;
    }
    async function start(ref, label, langOf) {
      const lang = langOf || sel.value, mine = {};
      latest = mine;
      card.hidden = false;
      try {
        await submit(ref, lang, false, onCard(label, mine));
      } catch (x) { card.hidden = true; fail(errText(x.message)); }
    }
    // a file that needs the engine: what it is and what it takes, and its install, in the card; once installed, the
    // file waiting for it converts (the server starts that), and the card follows the conversion
    function engineCard(upload, name) {
      const T = t(), other = /^doc-/.test(upload);   // not a PDF: a Word file, an e-book, a picture
      card.hidden = false;
      card.innerHTML = `<div class="jc-t">${esc(other ? T.file_needs : T.pdf_needs)}</div><div class="jc-s">${esc(name)}</div><p class="jc-why">${esc(other ? T.file_needs_why : T.pdf_needs_why)}</p>
        <div class="jc-acts"><button type="button" class="primary jc-install">${esc(T.engine_install)}</button><button type="button" class="jc-later">${esc(T.not_now)}</button></div>`;
      card.querySelector('.jc-later').onclick = () => { card.hidden = true; };
      card.querySelector('.jc-install').onclick = async () => {
        const mine = {};
        latest = mine;
        try {
          const show = onCard(t().engine, mine);
          watch(await api('/api/engine/install', {}), (j) => {
            if (j.status !== 'done') { show(j); return; }
            const conv = jobs.find((x) => x.kind === 'convert' && x.pid === upload);
            if (conv) watch(conv, onCard(name, mine)); else card.hidden = true;
          });
        } catch (x) { fail(errText(x.message)); }
      };
    }
    async function openUpload(file) {
      if (file.size > 100 * 1024 * 1024) { fail(isPdf(file) ? t().pdf_large : t().file_large); return; }
      const lang = sel.value, mine = {};
      latest = mine;
      card.hidden = false;
      card.innerHTML = `<div class="jc-t">${esc(file.name)}</div><div class="jc-s">${esc(isPdf(file) ? t().pdf_reading : t().file_reading)}</div><span class="sb-prog"><i style="width:4%"></i></span>`;
      try {
        const d = await importFile(file, lang);
        if (latest !== mine) return;
        took(d, file.name, mine, lang);
      } catch (x) { card.hidden = true; fail(errText(x.message)); }
    }
    // the server's reply to a file: the engine it waits for, the document read already, or the job that reads it
    function took(d, name, mine, lang) {
      if (d.needs === 'engine') { engineCard(d.upload, name); return; }
      if (d.paper) { location.href = `/p/${d.paper}?lang=${encodeURIComponent(lang)}`; return; }   // read already
      watch(d.job, onCard(d.via === 'online' ? null : name, mine, d.via === 'online' ? t().pdf_online.replace('{ref}', d.ref) : ''));
    }
    // an item of the Zotero library (zotero.py): its paper online, generated as a link is; else its file, read as a
    // file dropped here is
    async function readItem(item, title) {
      err.hidden = true;
      const lang = sel.value, mine = {};
      latest = mine;
      card.hidden = false;
      card.innerHTML = `<div class="jc-t">${esc(title)}</div><div class="jc-s">${esc(t().file_reading)}</div><span class="sb-prog"><i style="width:4%"></i></span>`;
      try {
        const d = await api('/api/zotero/read', { item, lang });
        if (latest !== mine) return;
        if (d.ref) {
          if (!modelName) { card.hidden = true; fail(t().no_model); return; }
          start(d.ref, title);
          return;
        }
        took(d, d.name || title, mine, lang);
      } catch (x) { card.hidden = true; fail(errText(x.message)); }
    }
    pickItem = readItem;
    document.getElementById('zlib-open').onclick = () => openLibrary();
    form.onsubmit = async (e) => {
      e.preventDefault();
      err.hidden = true;
      const ref = document.getElementById('ref').value.trim();
      if (!/\d{4}\.\d{4,5}|PMC\d{4,}|PPR\d{4,}|10\.\d{4,9}\/\S|^https?:\/\/[^\s/]+\.[^\s/]+/i.test(ref)) { fail(t().bad_ref); return; }   // an arXiv or Europe PMC id, a DOI, any web page's link
      if (!modelName) { fail(t().no_model); return; }   // connect a model first (the card below)
      start(ref);
    };
    // a file, from the drop zone or dropped anywhere on the page: one the engine reads is converted (no model is
    // needed to read it); a Markdown or text file is stored by the server, then generated
    async function openFile(file) {
      err.hidden = true;
      if (!file) return;
      if (forEngine(file)) { openUpload(file); return; }
      if (!isText(file)) { fail(t().md_only); return; }
      if (!modelName) { fail(t().no_model); return; }
      try {
        const r = await importFile(file, sel.value);
        start(r.paper, r.title);
      } catch (x) { fail(errText(x.message)); }
    }
    // several at once: they go in one after another, and the card says what came of them; the page stays here, the
    // sidebar showing each (one file opens as before)
    async function openFiles(list) {
      const files = [...list];
      if (files.length < 2) { openFile(files[0]); return; }
      err.hidden = true;
      const T = t(), mine = {};
      latest = mine;
      card.hidden = false;
      const got = await addFiles(files, sel.value, (k, file) => {
        if (latest !== mine) return;
        card.innerHTML = `<div class="jc-t">${esc(T.fl_adding.replace('{k}', k + 1).replace('{n}', files.length))}</div><div class="jc-s">${esc(file.name)}</div>` +
          `<span class="sb-prog"><i style="width:${Math.round((100 * k) / files.length) + 4}%"></i></span>`;
      });
      if (latest !== mine) return;   // (something started since has the card; the files are in the sidebar)
      const [head, lines, not] = filesSaid(got, files.length, T);
      card.innerHTML = `<div class="jc-head"><div class="jc-t">${esc(head)}</div><button type="button" class="jc-stop jc-close">${esc(T.close)}</button></div>` +
        lines.map((l) => `<div class="jc-s">${esc(l)}</div>`).join('') +
        (not.length ? `<div class="jc-n">${esc(T.fl_skipped)}</div>${not.map((l) => `<div class="jc-e">${esc(l)}</div>`).join('')}` : '') +
        (got.engine.length ? `<div class="jc-acts"><button type="button" class="primary jc-install">${esc(T.engine_install)}</button></div>` : '');
      card.querySelector('.jc-close').onclick = () => { card.hidden = true; };
      const inst = card.querySelector('.jc-install');
      if (inst) inst.onclick = async () => { inst.disabled = true; try { await installForFiles(); card.hidden = true; } catch (x) { inst.disabled = false; fail(errText(x.message)); } };
    }
    const fileIn = document.getElementById('file'), zone = document.getElementById('dropzone');
    fileIn.multiple = true;
    fileIn.accept = [...ENGINE_EXT, ...TEXT_EXT].map((x) => `.${x}`).join(',');
    zone.title = [...ENGINE_EXT, ...TEXT_EXT].filter((x) => !['htm', 'mht', 'jpeg', 'tif', 'markdown', 'mdown'].includes(x)).map((x) => `.${x}`).join(' ');
    zone.onclick = () => fileIn.click();
    fileIn.onchange = () => { openFiles(fileIn.files); fileIn.value = ''; };   // (openFiles copies the list first)
    let depth = 0;   // dragenter and dragleave fire for every child the file passes over
    const dropping = (on) => { zone.classList.toggle('dragging', on); document.getElementById('dropzone-t').textContent = on ? t().drop_md : t().drop_zone; };
    document.addEventListener('dragenter', (e) => { if ([...e.dataTransfer.types].includes('Files')) { depth++; dropping(true); } });
    document.addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; dropping(false); } });
    document.addEventListener('dragover', (e) => { if ([...e.dataTransfer.types].includes('Files')) e.preventDefault(); });
    document.addEventListener('drop', (e) => {
      if (!e.dataTransfer.files.length) return;
      e.preventDefault(); depth = 0; dropping(false);
      openFiles(e.dataTransfer.files);
    });
    renderLanding();
    // a first visit: how TextFold works, once (not when arriving mid-way: a file to generate, or back from OpenRouter)
    if (window.TFTutorial && !window.TFTutorial.seen() && !/[?&]start=/.test(location.search) && !location.hash.startsWith('#or-'))
      setTimeout(() => { if (!document.querySelector('dialog[open]')) window.TFTutorial.open(uiLang); }, 350);
    // a file dropped on a paper's page comes here, stored already: generate it (?start=<id>&t=<title>)
    const started = new URLSearchParams(location.search);
    if (started.get('start')) {
      history.replaceState(null, '', '/');
      if (modelName) start(started.get('start'), started.get('t')); else fail(t().no_model);
    }
    if (started.get('zotero')) { history.replaceState(null, '', '/'); readItem(started.get('zotero'), started.get('t') || ''); }
    // a PDF dropped on a paper's page, uploaded there: its job followed here, or the engine it waits for
    if (started.get('engine')) { history.replaceState(null, '', '/'); engineCard(started.get('engine'), started.get('t') || ''); }
    if (started.get('follow')) {
      history.replaceState(null, '', '/');
      const job = jobs.find((j) => j.id === started.get('follow')), mine = {};
      latest = mine;
      card.hidden = false;
      const ref = started.get('ref');
      if (job) watch(job, onCard(ref ? null : started.get('t'), mine, ref ? t().pdf_online.replace('{ref}', ref) : ''));
    }
    if (jobs.some(live)) startPolling();
  }

  // ── a Zotero library (zotero.py): its collections as Zotero shows them, each item read in a click ──
  async function openLibrary() {
    const dlg = h('dialog', 'settings zlib'); document.body.append(dlg);
    const COL = 'pf-zotero-col';
    let st = { ok: true, libraries: [] }, cols = [], items = null, col = store.get(COL) || '', query = '', timer = null, seq = 0;
    const opened = new Set();
    const q = (sel) => dlg.querySelector(sel);
    const lib = () => st.library || '';
    async function load() {
      st = await api('/api/zotero').catch((x) => ({ ok: false, error: x.message, libraries: [] }));
      cols = [];
      if (st.ok && lib()) {
        cols = await api(`/api/zotero/collections?lib=${encodeURIComponent(lib())}`).catch(() => []);
        if (col && !cols.some((c) => c.key === col)) col = '';
        // the chosen collection's parents open, so it shows in the tree
        for (let c = cols.find((x) => x.key === col); c && c.parent; c = cols.find((x) => x.key === c.parent)) opened.add(c.parent);
      }
      render(); list();
    }
    async function list() {
      items = null; renderItems();
      if (!st.ok || !lib()) return;
      const mine = ++seq;
      const qs = new URLSearchParams({ lib: lib(), collection: query ? '' : col, q: query });
      const got = await api(`/api/zotero/items?${qs}`).catch(() => []);
      if (mine === seq) { items = got; renderItems(); }
    }
    function tree(parent) {
      return cols.filter((c) => c.parent === parent).map((c) => {
        const kids = cols.some((x) => x.parent === c.key), open = opened.has(c.key);
        return `<li><div class="zl-col${c.key === col && !query ? ' on' : ''}" data-col="${esc(c.key)}">` +
          `<button type="button" class="zl-tw" data-tw="${esc(c.key)}"${kids ? '' : ' disabled'}>${kids ? (open ? '▾' : '▸') : ''}</button>` +
          `<span>${esc(c.name)}</span></div>${kids && open ? `<ul>${tree(c.key)}</ul>` : ''}</li>`;
      }).join('');
    }
    const treeHtml = () => `<div class="zl-col${!col && !query ? ' on' : ''}" data-col=""><span>${esc(t().z_all)}</span></div><ul>${tree('')}</ul>`;
    function render() {
      const T = t();
      const libs = (st.libraries || []).map((l) => `<option value="${esc(l.id)}"${l.id === lib() ? ' selected' : ''}>${esc(l.name || T.z_mine)}</option>`).join('');
      dlg.innerHTML = `<div class="st zlb"><div class="zl-head"><h2>${esc(T.z_lib)}</h2>
          ${st.ok && libs ? `<select class="zl-lib" aria-label="${esc(T.z_lib)}">${libs}</select>` : ''}
          <button type="button" class="zl-x" aria-label="${esc(T.st_close)}">×</button></div>
        ${!st.ok ? `<p class="st-note warn">${esc(st.error === 'api-off' ? T.z_api_off : T.z_off)}</p>` : ''}
        ${st.ok && lib() ? `<input class="st-search zl-q" type="search" placeholder="${esc(T.z_search)}" value="${esc(query)}">
          <div class="zl-body"><nav class="zl-tree">${treeHtml()}</nav><div class="zl-items"></div></div>` : ''}</div>`;
      q('.zl-x').onclick = () => dlg.close();
      const ls = q('.zl-lib');
      if (ls) ls.onchange = async () => { st = await api('/api/zotero/library', { library: ls.value }).catch(() => st); col = ''; store.set(COL, ''); load(); };
      const box = q('.zl-q');
      if (box) box.oninput = () => { clearTimeout(timer); timer = setTimeout(() => { query = box.value.trim(); renderTree(); list(); }, 300); };
      wireTree();
      renderItems();
    }
    function renderTree() { const n = q('.zl-tree'); if (!n) return; n.innerHTML = treeHtml(); wireTree(); }
    function wireTree() {
      dlg.querySelectorAll('.zl-col').forEach((n) => { n.onclick = () => {
        col = n.dataset.col; store.set(COL, col); query = ''; if (q('.zl-q')) q('.zl-q').value = ''; renderTree(); list();
      }; });
      dlg.querySelectorAll('.zl-tw').forEach((b) => { b.onclick = (e) => {
        e.stopPropagation(); if (opened.has(b.dataset.tw)) opened.delete(b.dataset.tw); else opened.add(b.dataset.tw); renderTree();
      }; });
    }
    function renderItems() {
      const box = q('.zl-items');
      if (!box) return;
      const T = t();
      if (items === null) { box.innerHTML = '<p class="st-note">…</p>'; return; }
      if (!items.length) { box.innerHTML = `<p class="st-note">${esc(T.z_none)}</p>`; return; }
      box.innerHTML = items.map((it, i) => {
        const who = it.authors.length > 2 ? `${it.authors[0]} et al.` : it.authors.join(', ');
        const p = it.paper && papers.find((x) => x.id === it.paper);
        const act = it.paper ? `<a class="zl-go" href="/p/${esc(it.paper)}${p ? `?lang=${pick(p)}` : ''}">${esc(T.z_open)}</a>`
          : it.arxiv || it.file || it.doi || it.children ? `<button type="button" class="zl-go primary" data-i="${i}">${esc(T.z_read)}</button>`
          : `<span class="zl-no">${esc(T.z_nofile)}</span>`;
        const tags = [it.arxiv ? 'arXiv' : '', it.file].filter(Boolean).map((x) => `<em>${esc(x)}</em>`).join('');
        return `<div class="zl-item"><div class="zl-tx"><div class="zl-t">${esc(it.title)}</div>` +
          `<div class="zl-m">${esc([who, it.year].filter(Boolean).join(' · '))}${it.citekey ? ` <code>${esc(it.citekey)}</code>` : ''}${tags}</div></div>${act}</div>`;
      }).join('');
      box.querySelectorAll('button.zl-go').forEach((b) => { b.onclick = () => {
        const it = items[+b.dataset.i];
        dlg.close();
        pickItem(`${lib()}/${it.key}`, it.title);
      }; });
    }
    dlg.addEventListener('close', () => dlg.remove());
    dlg.innerHTML = '<div class="st zlb"><p class="st-note">…</p></div>';
    dlg.showModal();
    load();
  }

  // ── settings: the model, the agents on this computer, the API providers (ThoughtDAG's model access) ──
  let settingsOpen = false;   // one Settings at a time: a click while it opens, or while it is open, opens no other (#12)
  async function openSettings(opts) {
    if (settingsOpen) return;
    settingsOpen = true;
    let data = modelData || { models: [], agents: [], providers: [], presets: [], bridge: {}, engine: {} };
    let loaded = !!modelData, refreshing = true, refreshError = '';
    let app = null;   // in the app: its version, loaded after the dialog is visible
    let form = null;          // the provider being added or edited: {key?, preset, name, base, api_key, found, picked}
    const tests = {};         // the last connection test of each model: the levels' (gen), the questions' (ask)
    let bridgeStarting = false, bridgeState = null;
    const dlg = h('dialog', 'settings prefs'); document.body.append(dlg);
    let storageDraft = null, storageBusy = false, storageError = '';
    let cloudDraft = null, cloudBusy = false, cloudError = '', cloudTested = false;
    let syncState = null, syncError = '', conflicts = [], conflictCount = -1;
    dlg.addEventListener('close', () => { settingsOpen = false; });
    const T = () => t();
    const host = (u) => { try { return new URL(u).host; } catch { return u; } };
    const groupName = (g) => (data.agents.find((a) => a.runtime === g) || {}).label || (data.providers.find((p) => p.key === g) || {}).name || g;

    function storageSection() {
      const t = T(), s = data.storage?.local || data.storage, c = data.cloudStorage;
      if (!s) return `<h3>${esc(t.storage_title)}</h3><p class="st-note" role="status">${esc(t.st_scanning)}</p>`;
      if (!cloudDraft && c) cloudDraft = { enabled: c.enabled, url: c.url, username: c.username, password: '', cache_path: c.cachePath || '' };
      const d = cloudDraft || { enabled: false }, disabled = storageBusy || cloudBusy ? ' disabled' : '';
      return `<h3>${esc(t.storage_title)}</h3>
        <div class="st-form st-storage-form">
          <label for="st-cloud-mode">${esc(t.cloud_mode)}<select id="st-cloud-mode"${disabled}><option value="local"${d.enabled ? '' : ' selected'}>${esc(t.cloud_local)}</option><option value="cloud"${d.enabled ? ' selected' : ''}>${esc(t.cloud_title)}</option></select></label>
          ${d.enabled ? `<label for="st-cloud-url">${esc(t.cloud_url)}<input id="st-cloud-url" type="url" placeholder="https://storage.example.com" autocomplete="url" spellcheck="false" value="${esc(d.url)}"${disabled}></label>
          <div class="st-cloud-credentials"><label for="st-cloud-user">${esc(t.cloud_username)}<input id="st-cloud-user" autocomplete="username" value="${esc(d.username)}"${disabled}></label>
          <label for="st-cloud-password">${esc(t.cloud_password)}<input id="st-cloud-password" type="password" autocomplete="new-password" placeholder="${esc(c?.hasPassword ? t.cloud_kept : '')}" value="${esc(d.password)}"${disabled}></label></div>
          <label for="st-cache-path">${esc(t.sync_cache)}<div class="st-storage-path"><input id="st-cache-path" type="text" spellcheck="false" autocomplete="off" value="${esc(d.cache_path)}" placeholder="${esc(t.sync_cache_default)}"${disabled}>
          ${window.desktop?.chooseLibraryFolder ? `<button type="button" class="st-btn st-cache-pick"${disabled}>${esc(t.storage_choose)}</button>` : ''}</div></label>
          <p class="st-note">${esc(t.sync_cloud_explain)}</p>` : `
          <div class="st-storage-current"><span class="st-meta">${esc(t.storage_current)}</span><code>${esc(s.path)}</code></div>
          <label for="st-storage-path">${esc(t.storage_destination)}<div class="st-storage-path"><input id="st-storage-path" class="st-storage-input" type="text" spellcheck="false" autocomplete="off" value="${esc(storageDraft ?? (s.pending || s.path))}"${disabled}>
          ${window.desktop?.chooseLibraryFolder ? `<button type="button" class="st-btn st-storage-pick"${disabled}>${esc(t.storage_choose)}</button>` : ''}</div></label>
          <p class="st-note">${esc(t.storage_explain)}</p>
          ${s.pending ? `<p class="st-note ok">${esc(t.storage_pending)} <code>${esc(s.pending)}</code></p>` : ''}`}
          ${c?.restartRequired ? `<p class="st-note ok" role="status">${esc(t.cloud_restart)}</p>` : ''}
          ${cloudTested ? `<p class="st-note ok" role="status">${esc(t.test_ok)}</p>` : ''}
          ${storageError || cloudError || s.error || c?.error ? `<p class="st-note warn" role="alert">${esc(storageErrorMessage(storageError || cloudError || s.error || c.error))}</p>` : ''}
          <div class="st-row end">${!d.enabled && s.pending ? `<button type="button" class="st-btn st-storage-cancel"${disabled}>${esc(t.storage_cancel)}</button>` : ''}
          ${d.enabled ? `<button type="button" class="st-btn st-cloud-test"${disabled}>${esc(t.cloud_test)}</button>` : ''}
          <button type="button" class="st-btn st-storage-save"${disabled}>${esc(storageBusy || cloudBusy ? t.storage_saving : t.storage_save)}</button></div>
        </div><div class="st-sync">${syncSection()}</div>`;
    }

    function syncSection() {
      if (data.cloudStorage?.active !== 'cloud') return conflicts.length ? `<h4>${esc(T().sync_conflicts.replace('{n}', conflicts.length))}</h4>${conflictSection()}` : '';
      const t = T(), s = syncState || data.cloudStorage.sync;
      if (!s) return '';
      const busy = s.syncing ? ' disabled' : '', local = data.storage?.local || data.storage;
      return `<div class="st-sync-status" role="status"><b>${esc(s.syncing ? t.sync_busy : s.online ? t.sync_online : t.sync_offline)}</b>
        <span>${esc(t.sync_pending.replace('{n}', s.pending))} · ${esc(t.sync_conflicts.replace('{n}', s.conflicts))}</span></div>
        <p class="st-note">${esc(t.sync_cache)}: <code>${esc(s.cachePath)}</code></p>
        ${s.lastSync ? `<p class="st-note">${esc(t.sync_last)} ${esc(new Date(s.lastSync * 1000).toLocaleString())}</p>` : ''}
        ${s.error || syncError ? `<p class="st-note warn" role="alert">${esc(syncError || s.error)}</p>` : ''}
        <div class="st-sync-actions"><button type="button" class="st-btn" data-sync="sync"${busy}>${esc(t.sync_now)}</button>
        <button type="button" class="st-btn" data-sync="import"${busy}>${esc(t.sync_import)}</button>
        <button type="button" class="st-btn" data-sync="download"${busy}>${esc(t.sync_download)}</button>
        <button type="button" class="st-btn" data-sync="clear"${busy}>${esc(t.sync_clear)}</button>
        ${s.pending || s.conflicts ? `<a class="st-btn" href="/api/storage/export" download="textfold-pending.zip">${esc(t.sync_export)}</a>` : ''}</div>
        <p class="st-note">${esc(t.sync_import_from)} <code>${esc(local?.path)}</code> · ${esc(t.sync_import_safe)}</p>
        ${s.job ? `<p class="st-note">${esc(t['sync_' + s.job.action] || s.job.action)}: ${s.job.done}/${s.job.total}${s.job.error ? ` · ${esc(s.job.error)}` : ''}</p>` : ''}
        ${s.import ? `<p class="st-note">${esc(t.sync_import_result.replace('{n}', s.import.imported).replace('{k}', s.import.conflicts.length))}${s.import.conflicts.length ? `<br>${s.import.conflicts.map(esc).join(', ')}` : ''}</p>` : ''}
        ${conflictSection()}`;
    }

    function conflictSection() {
      const t = T();
      const preview = (c, v) => {
        if (v === null) return `<p>${esc(t.sync_keep_deleted)}</p>`;
        if (c.key.startsWith('annotation/')) return `${v.anchor?.quote?.exact ? `<blockquote>${esc(v.anchor.quote.exact)}</blockquote>` : ''}<pre>${esc(v.text || (v.kind === 'ask' ? t.n_chat : t.sync_annotation))}</pre><p class="st-meta">${esc([v.color, v.updated].filter(Boolean).join(' · '))}</p>`;
        const full = JSON.stringify(v, null, 2);
        return `<pre>${esc(full.slice(0, 4000))}${full.length > 4000 ? '…' : ''}</pre>${full.length > 4000 ? `<p class="st-note">${esc(t.sync_preview)}</p>` : ''}`;
      };
      return conflicts.map((c, i) => `<details class="st-conflict" data-conflict="${i}"><summary>${esc(c.title || c.document)} · ${esc(c.key.startsWith('annotation/') ? t.sync_annotation : c.key)}</summary>
          ${c.key === 'document-state' ? `<p>${esc(t.sync_deleted)}</p><button type="button" class="st-btn" data-choice="restore">${esc(t.sync_restore)}</button> <button type="button" class="st-btn" data-choice="delete">${esc(t.sync_keep_deleted)}</button>` :
          `<p class="st-note">${esc(t.sync_revision_hint)}</p><div class="st-conflict-variants">${c.variants.map((v, k) => `<div>${preview(c, v)}<button type="button" class="st-btn" data-choice="${k}">${esc(t.sync_choose.replace('{n}', k + 1))}</button></div>`).join('')}</div>
          ${c.key.startsWith('annotation/') && c.variants.some((v) => v) ? `<label>${esc(t.sync_manual)}<textarea class="st-conflict-text" rows="4">${esc(c.variants.find((v) => v)?.text || '')}</textarea></label><button type="button" class="st-btn" data-manual="true" data-choice="${c.variants[0] ? 0 : 1}">${esc(t.sync_resolve)}</button>` : ''}`}</details>`).join('');
    }

    function storageErrorMessage(message) {
      const t = T();
      if (/absolute.*path/i.test(message)) return t.storage_absolute;
      if (/empty folder/i.test(message)) return t.storage_empty;
      if (/contain, or be inside|overlapping/i.test(message)) return t.storage_overlap;
      return message;
    }

    function modelSection() {
      const t = T();
      if (!data.models.length) {
        if (!refreshing && (refreshError || (data.discovery && data.discovery.error))) return '';
        return `<div class="st-list"><p class="st-empty">${esc(refreshing ? t.st_scanning : t.st_none)}</p></div>`;
      }
      const groups = [];
      for (const m of data.models) {
        let g = groups.find((x) => x.key === m.group);
        if (!g) groups.push(g = { key: m.group, agent: m.kind === 'agent', items: [] });
        g.items.push(m);
      }
      const opts = (chosen) => groups.map((g) => `<optgroup label="${esc(groupName(g.key))}${g.agent ? ' · agent' : ''}">${g.items.map((m) =>
        `<option value="${esc(m.id)}"${m.id === chosen ? ' selected' : ''}>${esc(m.name)}</option>`).join('')}</optgroup>`).join('');
      // questions: the levels' model unless another is chosen; its reasoning, where it has levels of it (Auto: the default)
      const asked = data.models.find((m) => m.id === (data.askModel || data.selected)), efforts = (asked && asked.efforts) || [];
      const effort = efforts.length ? `<select class="st-effort" aria-label="${esc(t.st_effort)}" title="${esc(t.st_effort)}"><option value="">${esc(t.eff_auto)}</option>${efforts.map((e) =>
        `<option value="${esc(e)}"${e === data.askEffort ? ' selected' : ''}>${esc(t[`eff_${e}`] || e)}</option>`).join('')}</select>` : '';
      const testBtn = (k) => {
        const r = tests[k];
        return `<button type="button" class="st-btn st-testbtn${r ? (r.ok ? ' ok' : ' bad') : ''}" data-test="${k}">${r ? (r.ok ? `✓ ${r.seconds} s` : `✕ ${esc(t.test_bad)}`) : esc(t.test)}</button>`;
      };
      const failed = ['gen', 'ask'].filter((k) => tests[k] && !tests[k].ok)
        .map((k) => `<p class="st-models-err">${esc(k === 'gen' ? t.st_gen : t.st_ask)}: ${esc(tests[k].error || '')}</p>`).join('');
      return `<div class="st-models">` +
        `<label for="st-gen">${esc(t.st_gen)}</label><select name="model" id="st-gen" class="st-model wide">${opts(data.selected)}</select>${testBtn('gen')}` +
        `<label for="st-ask">${esc(t.st_ask)}</label><select id="st-ask" class="st-askmodel${effort ? '' : ' wide'}"><option value="">${esc(t.st_same)}</option>${opts(data.askModel)}</select>${effort}${testBtn('ask')}` +
        `</div>${failed}`;
    }

    function agentSection() {
      const t = T();
      const found = data.agents.filter((a) => a.path);
      const missing = data.agents.filter((a) => !a.path && !a.error);
      const rows = found.map((a) => `<div class="st-item"><i class="st-dot${a.runnable ? '' : ' warn'}"></i><div class="st-main"><b>${esc(a.label)}</b>
        <span class="st-meta">${a.version ? `${esc(a.version)} · ` : ''}<code title="${esc(a.path)}">${esc(a.binary)}</code></span>${a.error ? `<p class="st-models-err">${esc(a.error)}</p>` : ''}</div>
        <span class="st-aside">${a.runnable ? esc(t.st_models.replace('{n}', a.models)) : esc(t.st_unsupported)}</span></div>`).join('');
      return `<div class="st-list">${rows}<div class="st-item st-foot-line"><span class="st-main st-meta">${missing.length ? `${esc(t.st_notfound)}: ${missing.map((a) => esc(a.label)).join(', ')}` : ''}</span>
        <button type="button" class="st-btn st-scan"${data.scanning ? ' disabled' : ''}>${esc(data.scanning ? t.st_scanning : t.st_rescan)}</button></div></div>`;
    }

    function providerSection() {
      const t = T();
      const src = (p) => p.source === 'thoughtdag' ? t.st_from_td : p.source === 'env' ? t.st_from_env : p.source === 'bridge' ? t.st_auto : t.st_added;
      const rows = data.providers.map((p) => `<div class="st-item"><div class="st-main"><b>${esc(p.name)}<span class="st-src ${p.source}">${esc(src(p))}</span></b>
        <span class="st-meta">${esc(host(p.base))}</span></div><span class="st-aside">${esc(t.st_models.replace('{n}', p.models.length))}</span>
        ${p.source === 'ui' ? `<span class="st-acts"><button type="button" class="st-btn st-edit" data-key="${esc(p.key)}">${esc(t.st_edit)}</button><button type="button" class="st-btn danger st-del" data-key="${esc(p.key)}">${esc(t.st_remove)}</button></span>` : ''}</div>`).join('');
      const td = '';   // (ThoughtDAG's .env is no longer read: its providers were copied into these settings once, v0.8.3)
      const br = data.bridge || {};
      const bridgeRow = br.running || data.providers.some((p) => p.preset === 'chatgpt-bridge') ? '' : `<div class="st-item"><i class="st-dot off"></i><div class="st-main"><b>${esc(t.bridge_name)}</b>
        <span class="st-meta">127.0.0.1:10531 · ${esc(bridgeStarting ? t.bridge_starting : t.bridge_off)}</span>${bridgeNote(bridgeState, t)}</div>
        ${br.node && !bridgeStarting ? `<button type="button" class="st-btn st-bridgego">${esc(t.bridge_start)}</button>` : ''}</div>`;
      return `<div class="st-list">${rows}${bridgeRow}${td}${form ? providerForm() : `<button type="button" class="st-addrow st-add"><span aria-hidden="true">＋</span>${esc(t.st_add)}</button>`}</div>`;
    }

    function providerForm() {
      const t = T();
      const preset = data.presets.find((p) => p.id === form.preset) || {};
      const opts = data.presets.map((p) => `<option value="${p.id}"${p.id === form.preset ? ' selected' : ''}>${esc(p.id === 'chatgpt-bridge' ? t.bridge_name : (p.name || 'Custom'))}${p.region ? ` (${p.region})` : ''}</option>`).join('');
      // the models found: the picked ones first (a long list, such as OpenRouter's, would bury them), then as listed;
      // a search box narrows the list as you type, without losing what is ticked
      const found = form.found ? [...form.found].sort((a, b) => form.picked.includes(b.id) - form.picked.includes(a.id)) : null;
      const list = found && !found.length ? `<p class="st-note warn">${esc(t.st_none)}</p>` : found ? `<div class="st-label">${esc(t.st_pick)} <span class="st-count"></span></div>` +
        (found.length > 12 ? `<input type="search" class="st-search" placeholder="${esc(t.st_search)}" autocomplete="off">` : '') +
        `<div class="st-pick">${found.map((m) =>
        `<label data-id="${esc(m.id.toLowerCase())}"><input type="checkbox" value="${esc(m.id)}"${form.picked.includes(m.id) ? ' checked' : ''}>${esc(m.id)}${m.free ? ` <em class="free">${esc(t.free)}</em>` : ''}${(preset.recommend || []).includes(m.id) ? ' <em>★</em>' : ''}</label>`).join('')}</div>` : '';
      // OpenRouter: a key made by one authorisation (OAuth PKCE) instead of pasting one
      const oauth = form.preset === 'openrouter' && !form.minted ? `<div class="st-oauth"><p>${esc(t.or_hint)}</p><button type="button" class="primary pf-oauth">${esc(t.or_button)}</button></div>` : '';
      const minted = form.minted ? `<p class="st-note ok">${esc(t.or_minted)}</p>` : '';
      // the ChatGPT plan through the local bridge: whether it runs, and starting it
      const b = form.bridge;
      const bridge = !preset.bridge ? '' : !b ? `<p class="st-note">…</p>` : b.running
        ? `<div class="st-bridge on"><i></i>${esc(t.bridge_on.replace('{n}', b.models.length))}</div><p class="st-note">${esc(t.bridge_hint)}</p>`
        : `<div class="st-bridge"><i></i><span>${esc(form.bridgeStarting ? t.bridge_starting : t.bridge_off)}</span>${b.node && !form.bridgeStarting ? `<button type="button" class="pf-bridge">${esc(t.bridge_start)}</button>` : ''}</div>
          ${bridgeNote(b, t)}<p class="st-note">${esc(t.bridge_hint)}</p>${b.node ? '' : `<p class="st-note warn">${esc(t.bridge_no_node)}</p>`}
          <div class="st-cmd"><code>npx openai-oauth</code><button type="button" class="linkbtn pf-copy">${esc(t.copy)}</button></div>`;
      return `<div class="st-form">
        <label>${esc(t.st_preset)}<select class="pf-preset">${opts}</select></label>${oauth}${minted}${bridge}
        ${form.preset === 'custom' ? `<label>${esc(t.st_name)}<input class="pf-name" value="${esc(form.name || '')}"></label>` : ''}
        <label>${esc(t.base_url)}<input class="pf-base" value="${esc(form.base || '')}" placeholder="https://…/v1"></label>
        ${preset.nokey || form.minted ? '' : `<label><span>${esc(t.api_key)}${preset.keyUrl ? ` · <a href="${preset.keyUrl}" target="_blank" rel="noopener">${esc(host(preset.keyUrl))}</a>` : ''}</span><input class="pf-key" type="password" placeholder="${esc(form.key ? t.st_key_kept : 'sk-…')}"></label>`}
        <div class="st-row"><button type="button" class="st-btn pf-fetch">${esc(t.st_fetch)}</button><span class="pf-err">${esc(form.err || '')}</span></div>
        ${list}
        <div class="st-row end"><button type="button" class="st-btn pf-cancel">${esc(t.cancel)}</button><button type="button" class="primary pf-save"${form.found && form.found.length ? '' : ' disabled'}>${esc(t.st_save_provider)}</button></div></div>`;
    }

    let formShown = false;
    function discoveryState() {
      const t = T(), error = refreshError || (data.discovery && data.discovery.error);
      return `<div class="st-discovery" role="status">${refreshing ? `<p class="st-note">${esc(t.st_scanning)}</p>` : error ?
        `<p class="st-models-err">${esc(t.st_discovery_failed)} ${esc(error)}</p><button type="button" class="st-btn st-retry">${esc(t.st_retry)}</button>` : ''}</div>`;
    }
    function render() {
      const t = T(), body = q('.stp-body'), y = body ? body.scrollTop : 0;   // (drawn again on each change: the place is kept)
      dlg.innerHTML = `<div class="stp" tabindex="-1" autofocus>
        <header class="stp-head"><h2>${esc(t.settings)}</h2><button type="button" class="stp-x st-close" aria-label="${esc(t.close)}" title="${esc(t.close)}"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6 6l8 8M14 6l-8 8"/></svg></button></header>
        <div class="stp-body">
        <section><div class="st-list">
          <div class="st-item"><label class="st-main" for="st-ui"><b>${esc(t.language)}</b></label><select class="st-ui" id="st-ui">${I18N.CODES.map((c) => `<option value="${c}"${c === uiLang ? ' selected' : ''}>${esc(I18N.NATIVE[c])}</option>`).join('')}</select></div>
          ${app ? `<div class="st-item st-app"><div class="st-main"><b>TextFold ${esc(app)}</b><span class="st-meta">${esc(t.up_note)} <a href="https://github.com/chenxiachan/textfold" target="_blank" rel="noopener">★ ${esc(t.up_star)}</a></span><span class="st-upd-msg" aria-live="polite"></span></div><button type="button" class="st-btn st-upd">${esc(t.up_check)}</button></div>` : ''}
        </div></section>
        <section><h3>${esc(t.st_models_h)}</h3><div class="st-model-area">${modelSection()}${discoveryState()}</div></section>
        <section><h3>${esc(t.st_agents)}</h3><div class="st-agent-area">${loaded ? agentSection() : ''}</div></section>
        <section><h3>${esc(t.st_providers)}</h3><div class="st-provider-area">${loaded ? providerSection() : ''}</div></section>
        <section class="st-storage">${storageSection()}</section>
        <section class="st-engine"></section>
        </div>
        <footer class="stp-foot"><p>${esc(t.key_note)}</p><button type="button" class="primary st-close">${esc(t.st_close)}</button></footer></div>`;
      wire();
      renderEngine();
      q('.stp-body').scrollTop = y;   // (after the engine's part, which makes the sections their full height)
      if (form && !formShown) q('.st-form').scrollIntoView({ block: 'nearest' });   // adding or editing a provider: the form in view
      formShown = !!form;
    }
    const q = (sel) => dlg.querySelector(sel);
    // the PDF engine's own part of the dialog, drawn again on its own while it installs
    const gb = (b) => (b ? `${(b / 1e9).toFixed(1)} GB` : '');
    function renderEngine() {
      const box = q('.st-engine');
      if (!box) return;
      const t = T(), e = data.engine || {}, job = jobs.find((j) => j.kind === 'install' && live(j));
      const act = job ? '' : e.installed ? `<button type="button" class="st-btn danger st-engine-rm">${esc(t.engine_remove)}</button>`
        : `<button type="button" class="st-btn st-engine-in">${esc(t.engine_install)}</button>`;
      const state = job ? `<div class="st-item"><i class="st-dot busy"></i><div class="st-main"><b>${esc(t.engine)}</b><span class="st-meta">${esc(stageOf(job, t))}</span></div></div>`
        : e.installed ? `<div class="st-item"><i class="st-dot"></i><div class="st-main"><b>MinerU ${esc(e.version)}</b><span class="st-meta">${esc(t.engine_on.replace('{size}', gb(e.bytes)))} · <code title="${esc(e.path)}">${esc(e.path.split('/').slice(-3).join('/'))}</code></span></div>${act}</div>`
        : `<div class="st-item"><div class="st-main st-meta st-desc">${esc(t.engine_off)}</div>${act}</div>`;
      const tier = `<div class="st-item"><label class="st-main" for="st-tier"><b>${esc(t.tier)}</b></label><select class="st-tiersel" id="st-tier">${['fast', 'accurate'].map((k) =>
        `<option value="${k}"${(data.pdfTier || 'fast') === k ? ' selected' : ''}>${esc(t['tier_' + k])}</option>`).join('')}</select></div>`;
      box.innerHTML = `<h3>${esc(t.engine)}</h3><div class="st-list">${state}${tier}</div>`;
      box.querySelector('.st-tiersel').onchange = async (ev) => { data = await api('/api/settings', { pdf_tier: ev.target.value }); renderEngine(); };
      const inst = box.querySelector('.st-engine-in');
      if (inst) inst.onclick = async () => { inst.disabled = true; watch(await api('/api/engine/install', {}), () => {}); follow(); };
      const rm = box.querySelector('.st-engine-rm');
      if (rm) rm.onclick = async () => {
        if (!confirm(t.engine_remove_q.replace('{size}', gb(e.bytes)))) return;
        try { data.engine = await api('/api/engine/remove', {}); } catch (x) { alert(errText(x.message)); }
        renderEngine();
      };
    }
    function follow() {   // while it installs: its stage here, then what it found
      const tick = setInterval(async () => {
        if (!dlg.open) { clearInterval(tick); return; }
        if (!jobs.some((j) => j.kind === 'install' && live(j))) { clearInterval(tick); data = await api('/api/models'); }
        renderEngine();
      }, 1500);
    }
    const update = (d, background = false) => {
      data = modelData = d; loaded = true;
      modelName = d.selectedName || ''; askName = d.askName || '';
      renderSidebar(); if (landing) renderLanding();
      if (!background) { render(); return; }
      // A discovery result must not rebuild a provider form the reader is editing.
      q('.st-model-area').innerHTML = modelSection() + discoveryState();
      q('.st-agent-area').innerHTML = agentSection();
      if (!form) q('.st-provider-area').innerHTML = providerSection();
      if (!cloudDraft) q('.st-storage').innerHTML = storageSection();
      renderEngine();
      wire();
    };
    function wireSync() {
      dlg.querySelectorAll('[data-sync]').forEach((b) => { b.onclick = async () => {
        b.disabled = true; syncError = '';
        try { syncState = await api('/api/storage/sync', { action: b.dataset.sync }); }
        catch (error) { syncError = error.message; }
        await refreshSync(true);
      }; });
      dlg.querySelectorAll('[data-choice]').forEach((b) => { b.onclick = async () => {
        const box = b.closest('[data-conflict]'), c = conflicts[+box.dataset.conflict];
        b.disabled = true; syncError = '';
        const value = { action: 'resolve', document: c.document, conflict: c.id,
          choice: c.key === 'document-state' ? b.dataset.choice : +b.dataset.choice };
        if (b.dataset.manual) value.text = box.querySelector('textarea').value;
        try { await api('/api/storage/sync', value); }
        catch (error) { syncError = error.message; }
        await refreshSync(true);
      }; });
    }
    async function refreshSync(force = false) {
      if (!dlg.isConnected) return;
      try {
        syncState = await api('/api/storage/sync');
        if (force || !syncState || syncState.conflicts !== conflictCount) {
          conflicts = await api('/api/storage/conflicts'); conflictCount = syncState?.conflicts ?? conflicts.length;
          document.dispatchEvent(new CustomEvent('tf-conflicts', { detail: conflicts.length }));
        }
        const box = q('.st-sync');
        // Don't replace a conflict form while its retained revisions are being reviewed.
        if (box && (force || !box.querySelector('details[open]'))) { box.innerHTML = syncSection(); wireSync(); }
      } catch (error) { syncError = error.message; }
    }
    async function discover() {
      refreshing = true; refreshError = '';
      q('.st-model-area').innerHTML = modelSection() + discoveryState(); wire();
      try {
        const d = await refreshModels();
        if (dlg.isConnected) { refreshing = false; update(d, true); }
      } catch (x) {
        if (!dlg.isConnected) return;
        refreshing = false; refreshError = x.message; data.scanning = false;
        q('.st-model-area').innerHTML = modelSection() + discoveryState();
        if (loaded) q('.st-agent-area').innerHTML = agentSection();
        wire();
      }
    }

    function wire() {
      for (const [id, key] of [['st-cloud-url', 'url'], ['st-cloud-user', 'username'], ['st-cloud-password', 'password'], ['st-cache-path', 'cache_path']]) {
        const input = q('#' + id);
        if (input) input.oninput = () => { cloudDraft[key] = input.value; cloudTested = false; };
      }
      const cloudMode = q('#st-cloud-mode');
      if (cloudMode) cloudMode.onchange = () => { cloudDraft.enabled = cloudMode.value === 'cloud'; render(); };
      const storageInput = q('.st-storage-input');
      if (storageInput) storageInput.oninput = () => { storageDraft = storageInput.value; };
      for (const [selector, key] of [['.st-storage-pick', 'local'], ['.st-cache-pick', 'cache']]) {
        const pick = q(selector);
        if (pick) pick.onclick = async () => {
          try {
            const path = await window.desktop.chooseLibraryFolder();
            if (path) { if (key === 'cache') cloudDraft.cache_path = path; else storageDraft = path; render(); }
          } catch (error) { storageError = error.message; render(); }
        };
      }
      const saveStorage = async (test = false, cancel = false) => {
        storageBusy = true; storageError = ''; cloudError = ''; cloudTested = false; render();
        try {
          if (test) { await api('/api/storage/cloud/test', cloudDraft); cloudTested = true; }
          else if (cloudDraft.enabled) {
            data.cloudStorage = await api('/api/storage/cloud', cloudDraft); cloudDraft.password = '';
          } else {
            // Validate switching first: an unsynced cloud workspace must remain selected.
            if (data.cloudStorage?.enabled) data.cloudStorage = await api('/api/storage/cloud', { enabled: false });
            const local = data.storage?.local || data.storage;
            if (cancel || (storageDraft && storageDraft !== (local.pending || local.path))) {
              data.storage = await api('/api/storage', cancel ? { cancel: true } : { path: storageDraft });
              const saved = data.storage.local || data.storage;
              storageDraft = saved.pending || saved.path;
            }
          }
        } catch (error) { storageError = error.message; }
        storageBusy = false;
        if (dlg.isConnected) render();
      };
      const storageSave = q('.st-storage-save'), storageCancel = q('.st-storage-cancel'), cloudTest = q('.st-cloud-test');
      if (storageSave) storageSave.onclick = () => saveStorage();
      if (storageCancel) storageCancel.onclick = () => saveStorage(false, true);
      if (cloudTest) cloudTest.onclick = () => saveStorage(true);
      wireSync();
      const retry = q('.st-retry');
      if (retry) retry.onclick = discover;
      dlg.querySelectorAll('.st-close').forEach((b) => { b.onclick = () => dlg.close(); });
      const up = q('.st-upd');
      if (up) up.onclick = async () => {   // an answer either way; one found shows as the corner's toast too
        const msg = q('.st-upd-msg'), t = T();
        up.disabled = true; msg.textContent = '…';
        const r = await window.desktop.checkForUpdates().catch(() => ({ kind: 'check-failed' }));
        up.disabled = false;
        msg.textContent = r.kind === 'latest' ? t.up_latest : r.kind === 'dev' ? t.up_dev : r.kind === 'check-failed' ? t.up_check_failed
          : r.kind === 'ready' ? t.up_ready.replace('{v}', r.version) : t.up_new.replace('{v}', r.version || '');
      };
      q('.st-ui').onchange = (e) => { setReaderLang(e.target.value); render(); };
      const sel = q('.st-model');
      if (sel) sel.onchange = async () => { delete tests.gen; if (!data.askModel) delete tests.ask; update(await api('/api/models/select', { model: sel.value })); };
      const askSel = q('.st-askmodel'), effSel = q('.st-effort');
      if (askSel) askSel.onchange = async () => { delete tests.ask; update(await api('/api/models/select', { task: 'ask', model: askSel.value || null, effort: null })); };
      if (effSel) effSel.onchange = async () => { delete tests.ask; update(await api('/api/models/select', { task: 'ask', model: data.askModel || null, effort: effSel.value || null })); };
      dlg.querySelectorAll('.st-testbtn').forEach((tb) => {
        tb.onclick = async () => {
          const k = tb.dataset.test;
          tb.disabled = true; tb.textContent = '…';
          tests[k] = await api('/api/settings/test', k === 'ask' ? { task: 'ask' } : { model: data.selected }).catch((e) => ({ ok: false, error: e.message }));
          render();
        };
      });
      const scan = q('.st-scan');
      if (scan) scan.onclick = async () => {
        data.scanning = true; q('.st-agent-area').innerHTML = agentSection(); wire();
        try { update(await api('/api/agents/scan', {}), true); }
        catch (x) { data.scanning = false; refreshError = x.message; update(data, true); }
      };
      const td = q('.st-td');
      if (td) td.onchange = async () => update(await api('/api/settings', { use_thoughtdag_env: td.checked }));
      dlg.querySelectorAll('.st-del').forEach((b) => { b.onclick = async () => update(await api('/api/providers/remove', { key: b.dataset.key })); });
      dlg.querySelectorAll('.st-edit').forEach((b) => {
        b.onclick = () => { const p = data.providers.find((x) => x.key === b.dataset.key); form = { key: p.key, preset: p.preset || 'custom', name: p.name, base: p.base }; render(); };
      });
      const bg = q('.st-bridgego');
      if (bg) bg.onclick = async () => {   // start the bridge; its models join the list once it answers
        bridgeStarting = true; bridgeState = null; render();
        try {
          bridgeState = await startBridge((st) => { if (!dlg.isConnected) return false; bridgeState = st; render(); });
        } catch (x) { bridgeState = { error: x.message }; }
        bridgeStarting = false;
        if (dlg.isConnected) update(await refreshModels(), true);
      };
      const add = q('.st-add');
      if (add) add.onclick = () => { const p = data.presets[0]; form = { preset: p.id, name: '', base: p.base }; render(); };
      if (!form) return;
      const ps = q('.pf-preset');
      ps.onchange = () => { const p = data.presets.find((x) => x.id === ps.value); form = { preset: p.id, name: '', base: p.base }; render(); };   // another kind: a new provider, the edited one stays
      const keep = () => { form.base = q('.pf-base').value.trim(); if (q('.pf-name')) form.name = q('.pf-name').value.trim(); if (q('.pf-key') && q('.pf-key').value) form.api_key = q('.pf-key').value.trim(); };
      q('.pf-fetch').onclick = () => fetchModels();
      const pick = q('.st-pick'), count = q('.st-count'), search = q('.st-search');
      const recount = () => { if (count && pick) count.textContent = `· ${pick.querySelectorAll('input:checked').length}`; };
      if (pick) { pick.onchange = recount; recount(); }
      if (search) search.oninput = () => {
        const words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
        pick.querySelectorAll('label').forEach((l) => { l.hidden = !words.every((w) => l.dataset.id.includes(w)); });
      };
      const oa = q('.pf-oauth');
      if (oa) oa.onclick = async () => { oa.disabled = true; if (window.desktop) dlg.close(); await connectOpenRouter(location.pathname + location.search); };
      const cp = q('.pf-copy');
      if (cp) cp.onclick = () => navigator.clipboard.writeText('npx openai-oauth').then(() => { cp.textContent = '✓'; }, () => {});
      const bs = q('.pf-bridge');
      if (bs) bs.onclick = async () => {
        form.bridgeStarting = true; render();
        let st;
        try {
          st = await startBridge((s) => { if (!dlg.isConnected || !form) return false; form.bridge = { ...form.bridge, ...s }; render(); });
        } catch (x) { st = { error: x.message }; }
        if (!form) return;
        form.bridge = { ...form.bridge, ...st }; form.bridgeStarting = false; render();
        if (st.running) fetchModels();
      };
      const pre = data.presets.find((x) => x.id === form.preset) || {};
      if (pre.bridge && !form.bridge) api('/api/bridge').then((st) => { if (!form) return; form.bridge = st; render(); if (st.running && !form.found) fetchModels(); });
      q('.pf-cancel').onclick = () => { form = null; render(); };
      q('.pf-save').onclick = async () => {
        keep();
        const picked = [...dlg.querySelectorAll('.st-pick input:checked')].map((i) => i.value);
        const d = await api('/api/providers/save', { key: form.key, preset: form.preset, name: form.name, base: form.base, api_key: form.api_key, models: picked, minted: !!form.minted });
        form = null; update(d);
      };
    }
    async function fetchModels() {
      if (!form) return;
      if (q('.pf-base')) form.base = q('.pf-base').value.trim();
      if (q('.pf-name')) form.name = q('.pf-name').value.trim();
      if (q('.pf-key') && q('.pf-key').value) form.api_key = q('.pf-key').value.trim();
      const err = q('.pf-err');
      form.err = '…';   // (kept in the form: a redraw while the startup scan lands no longer wipes it, #12)
      if (err) err.textContent = '…';
      try {
        const r = await api('/api/providers/probe', { base: form.base, api_key: form.api_key, key: form.key, preset: form.preset, minted: !!form.minted });
        form.found = r.models; form.picked = r.picked; form.err = ''; render();
      } catch (x) { if (!form) return; form.err = x.message; const e2 = q('.pf-err'); if (e2) e2.textContent = x.message; }
    }

    const syncTimer = setInterval(() => refreshSync(), 3000);
    dlg.addEventListener('close', () => { clearInterval(syncTimer); dlg.remove(); });
    render();
    dlg.showModal();
    if (window.desktop && window.desktop.checkForUpdates) window.desktop.version().then((v) => {
      app = v;
      if (dlg.isConnected && !form) render();
    }).catch(() => {});
    try {
      const snapshot = await api('/api/models');
      if (!dlg.isConnected) return;
      update(snapshot, true);
      if (opts && opts.add) { const p = data.presets[0]; form = { preset: p.id, name: '', base: p.base }; render(); }
      if (opts && opts.minted) {
        const p = data.presets.find((x) => x.id === 'openrouter');
        form = { preset: 'openrouter', name: '', base: p.base, minted: true }; render(); fetchModels();
      }
    } catch (x) { refreshError = x.message; }
    if (!dlg.isConnected) return;
    discover();
    if (jobs.some((j) => j.kind === 'install' && live(j))) follow();   // an install started on the landing
  }

  const syncBadge = h('button', 'storage-sync-badge');
  syncBadge.type = 'button'; syncBadge.hidden = true; syncBadge.onclick = () => openSettings();
  document.body.append(syncBadge);
  let offlineNotice = false, syncPolling = false, lastLibraryStamp = '', localConflictCount = 0;
  document.addEventListener('tf-conflicts', (event) => { localConflictCount = event.detail; pollStorage(); });
  document.addEventListener('tf-save-state', (event) => {
    if (event.detail.local && !event.detail.online && !offlineNotice) {
      offlineNotice = true; alert(t().sync_saved_local);
    }
    pollStorage();
  });
  async function pollStorage() {
    if (syncPolling || document.hidden) return;
    syncPolling = true;
    try {
      const s = await api('/api/storage/sync');
      syncBadge.hidden = !s && !localConflictCount;
      if (!s && localConflictCount) syncBadge.textContent = t().sync_conflicts.replace('{n}', localConflictCount);
      if (s) {
        if (s.online) offlineNotice = false;
        else if (s.pending && !offlineNotice) { offlineNotice = true; alert(t().sync_saved_local); }
        const T = t();
        syncBadge.textContent = `${s.syncing ? T.sync_busy : s.online ? T.sync_online : T.sync_offline} · ${T.sync_pending.replace('{n}', s.pending)}${s.conflicts ? ' · ' + T.sync_conflicts.replace('{n}', s.conflicts) : ''}`;
        syncBadge.classList.toggle('warn', !s.online || !!s.conflicts);
        const stamp = `${s.lastSync}:${s.pending}`;
        if (s.lastSync && stamp !== lastLibraryStamp) {
          lastLibraryStamp = stamp;
          document.dispatchEvent(new Event('tf-storage-synced'));
          const [items, groups] = await Promise.all([api('/api/papers'), api('/api/folders')]);
          papers = items; folders = groups; renderSidebar();
        }
      }
    } catch { /* the local app may be restarting */ }
    finally { syncPolling = false; }
  }
  const resumeStorage = (reason) => { api('/api/storage/sync', { action: 'sync', reason }).catch(() => {}); pollStorage(); };
  addEventListener('online', () => resumeStorage('online')); addEventListener('focus', () => resumeStorage('focus'));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) resumeStorage('focus'); });
  setInterval(pollStorage, 10000); pollStorage();

  renderSidebar();
  if (landing) refreshModels().then((d) => {
    modelName = d.selectedName || ''; askName = d.askName || ''; BOOT.bridge = d.bridge; modelsLooking = false;
    renderSidebar(); renderLanding();
  }).catch(() => { modelsLooking = false; renderLanding(); });
  // back from OpenRouter's consent page (#or-minted, or #or-failed=why)
  if (location.hash.startsWith('#or-')) {
    const hash = location.hash;
    history.replaceState(null, '', location.pathname + location.search);
    if (hash === '#or-minted') openSettings({ minted: true });
    else alert(t().or_failed.replace('{error}', decodeURIComponent(hash.split('=')[1] || '')));
  }
})();
