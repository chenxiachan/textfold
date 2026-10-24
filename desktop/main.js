// TextFold as an app: the same local server and the same reader, in one window.
//
// The packaged app carries its own Python (payload/python, from python-build-standalone) and the code (payload/app:
// textfold/, its web/ inside). It starts the server on a free port of 127.0.0.1, with the reader's papers kept in the app's data
// folder (TEXTFOLD_DATA), and points its window at it. Settings and keys stay where the server always keeps them,
// ~/.textfold. Pages on other sites open in the system browser, where the reader is already signed in.
// Run from the repository without a payload (`npm start`), it uses the python3 on the PATH (python on Windows) and the
// repository itself. textfold:// links open a document of the library in the window (links.js).
const { app, BrowserWindow, Menu, dialog, ipcMain, nativeTheme, session, shell } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');
const links = require('./links');

const WIN = process.platform === 'win32';
ipcMain.handle('storage:choose-folder', async (e) => {
  const result = await dialog.showOpenDialog(BrowserWindow.fromWebContents(e.sender), {
    title: 'TextFold', properties: ['openDirectory', 'createDirectory'],
  });
  return result.canceled ? null : result.filePaths[0] || null;
});
const PAYLOAD = app.isPackaged ? path.join(process.resourcesPath, 'payload') : path.join(__dirname, 'payload');
const BUNDLED = WIN ? path.join(PAYLOAD, 'python', 'python.exe') : path.join(PAYLOAD, 'python', 'bin', 'python3');
const PACKED = fs.existsSync(BUNDLED);
const PYTHON = PACKED ? BUNDLED : WIN ? 'python' : 'python3';
const CODE = PACKED ? path.join(PAYLOAD, 'app') : path.join(__dirname, '..');

let server = null;
let win = null;
let base = null;      // the server's address, once it answers
let waiting = null;   // a link that came before that: the last one, opened then

// the data folder (papers/, out/, server.log), the one `python3 -m textfold serve` keeps too (store.app_data()): on a Mac
// ~/Library/Application Support/TextFold, Electron's own; on Windows %LOCALAPPDATA%\TextFold, where Electron's is Roaming
// (TEXTFOLD_DATA, as for the server: another folder, for a test run)
app.setName('TextFold');
if (process.env.TEXTFOLD_DATA) app.setPath('userData', path.resolve(process.env.TEXTFOLD_DATA));
else if (WIN) app.setPath('userData', path.join(process.env.LOCALAPPDATA || path.join(app.getPath('home'), 'AppData', 'Local'), 'TextFold'));
// The window's HTTP cache, at most 32 MB (and no large file in it): what it loads comes from the server on this computer,
// read from the same disk, and under Chromium's own limit, a share of the free space, it kept a second copy of each PDF
// and picture opened, 200 MB and more. A page still reuses what it loaded (Blink's memory cache).
app.commandLine.appendSwitch('disk-cache-size', String(32 * 2 ** 20));
if (!app.requestSingleInstanceLock()) app.quit();
app.on('second-instance', (_e, argv) => {
  if (win) { if (win.isMinimized()) win.restore(); win.focus(); }
  follow(links.fromArgv(argv));
});

// ── textfold:// links ────────────────────────────────────────────────
// Another program (an editor's plugin) opens a document of the library at a paragraph, a section, a note or a level. The packaged
// app is the scheme's handler: declared for macOS in package.json (build.protocols), and claimed here, which Windows
// needs, since its installer registers no scheme. A copy run from the code never claims it, so no build of the
// repository takes the installed app's links.
if (app.isPackaged) app.setAsDefaultProtocolClient('textfold');
// macOS hands a link to the app, before it is ready when the link starts it; Windows and Linux start the app with the
// link on its command line, and a second instance hands its own to this one (second-instance, above)
app.on('open-url', (e, url) => { e.preventDefault(); follow(url); });
follow(links.fromArgv(process.argv));

function follow(link) {
  const parts = link && links.parse(link);
  if (!parts) return;
  waiting = parts;
  if (base && win && !win.isDestroyed()) go();
}
// the reader's page of a document not in the library opens the landing without a word: the app says which it was
const MISSING = { en: 'This link names a document that is not in the library.', zh: '这个链接指向的文档不在列表中。',
  'zh-Hant': '這個連結指向的文件不在列表中。', ja: 'このリンクが指す文書は一覧にありません。', ko: '이 링크가 가리키는 문서는 목록에 없습니다.',
  es: 'Este enlace remite a un documento que no está en la biblioteca.', fr: 'Ce lien renvoie à un document qui n’est pas dans la bibliothèque.',
  de: 'Dieser Link führt zu einem Dokument, das nicht in der Bibliothek ist.', pt: 'Este link leva a um documento que não está na biblioteca.',
  it: 'Questo link rimanda a un documento che non è nella libreria.', ru: 'Эта ссылка ведёт к документу, которого нет в библиотеке.',
  hi: 'यह लिंक जिस दस्तावेज़ का है, वह सूची में नहीं है।' };
async function inLibrary(id) {
  try {
    const r = await fetch(`${base}/api/papers`);
    return !r.ok || (await r.json()).some((p) => p.id === id);
  } catch {
    return true;   // (no answer: the page says so itself)
  }
}
async function go() {
  const parts = waiting;
  waiting = null;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
  if (!(await inLibrary(parts.paper))) {
    await dialog.showMessageBox(win, { type: 'info', message: inLocale(MISSING), detail: parts.paper });
    if (!win.isDestroyed()) win.loadURL(`${base}/`);
    return;
  }
  if (win.isDestroyed()) return;
  const url = links.readerUrl(base, parts);
  // the reader reads #u=, #s=, #n= and #level= as it opens, so a link into the page already open opens it again
  if (url.includes('#') && win.webContents.getURL().split('#')[0] === url.split('#')[0]) {
    win.webContents.executeJavaScript(`history.replaceState(null, '', ${JSON.stringify(url)}); location.reload();`).catch(() => win.loadURL(url));
  } else {
    win.loadURL(url);
  }
}

// a free port from a quiet base, so a server run by hand on 3017 is never in the way
function freePort(start = 31917) {
  return new Promise((resolve) => {
    const probe = net.createServer();
    probe.once('error', () => resolve(freePort(start + 1)));
    probe.once('listening', () => probe.close(() => resolve(start)));
    probe.listen(start, '127.0.0.1');
  });
}

async function waitReady(port, tries = 160) {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/api/models`);
      if (r.ok) return true;
    } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 250));
  }
  return false;
}

function startServer(port) {
  // A GUI app starts with a bare PATH: the usual install places are added so that the server finds node and npx
  // (the ChatGPT bridge) at once; on a Mac it also asks the login shell for the agents' places (textfold/agents.py).
  const home = app.getPath('home');
  const extra = WIN
    ? [path.join(process.env.APPDATA || path.join(home, 'AppData', 'Roaming'), 'npm'), path.join(process.env.ProgramFiles || 'C:\\Program Files', 'nodejs'), path.join(home, '.local', 'bin')]
    : ['/opt/homebrew/bin', '/usr/local/bin', path.join(home, '.local', 'bin')];
  const key = Object.keys(process.env).find((k) => k.toUpperCase() === 'PATH') || 'PATH';   // Windows spells it Path
  const env = {
    ...process.env,
    [key]: [...extra, process.env[key] || (WIN ? '' : '/usr/bin:/bin')].filter(Boolean).join(path.delimiter),
    TEXTFOLD_DATA: app.getPath('userData'),
    PYTHONNOUSERSITE: '1',
    PYTHONDONTWRITEBYTECODE: '1',
    // the locale's codepage (cp936 on a Chinese Windows) is otherwise the default for open() and the pipes, and the
    // packages the app ships read files of their own: UTF-8 mode makes the whole process, and the children it starts,
    // read and write UTF-8
    PYTHONUTF8: '1',
  };
  const log = fs.createWriteStream(path.join(app.getPath('userData'), 'server.log'), { flags: 'a' });
  // (windowsHide: no console window beside the app's own)
  server = spawn(PYTHON, ['-s', '-u', '-m', 'textfold', 'serve', '--port', String(port)], { cwd: CODE, env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
  server.stdout.pipe(log);
  server.stderr.pipe(log);
  server.on('exit', (code) => { server = null; if (code && win && !win.isDestroyed()) win.webContents.send?.('server-exit', code); });
}

async function open() {
  win = new BrowserWindow({
    width: 1360, height: 900, minWidth: 720, minHeight: 520, show: true, title: 'TextFold',
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#1B1915' : '#FAF9F7',   // the start page's ground, dark or light
    autoHideMenuBar: true,   // (Windows: the menu bar shows on Alt)
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, sandbox: true },
  });
  win.loadFile(path.join(__dirname, 'splash.html'));
  // full screen, from View › Toggle Full Screen or the reader's Aa: the page told when it changes (its button's name)
  for (const ev of ['enter-full-screen', 'leave-full-screen']) win.on(ev, () => win.webContents.send('fullscreen', win.isFullScreen()));
  const local = (url) => /^http:\/\/(127\.0\.0\.1|localhost):\d+\//.test(url);
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (e, url) => {
    if (local(url) || url.startsWith('file:')) return;
    e.preventDefault();
    if (/^https?:/.test(url)) shell.openExternal(url);
  });

  const port = await freePort();
  startServer(port);
  if (!(await waitReady(port))) {
    const where = path.join(app.getPath('userData'), 'server.log');
    win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(`<pre style="font:14px -apple-system,'Segoe UI',sans-serif;padding:24px;white-space:pre-wrap">TextFold could not start its server.\n\nThe log is in ${where}</pre>`)}`);
    return;
  }
  base = `http://127.0.0.1:${port}`;
  if (waiting) go();
  else win.loadURL(`${base}/`);
}

ipcMain.handle('open-external', (_e, url) => (/^https?:/.test(url) ? shell.openExternal(url) : null));
// a file the reader saves (a page, its notes): where it goes, they choose in the system's own dialog; the path, or null
const KINDS = { html: 'Web page', md: 'Markdown', json: 'JSON' };
ipcMain.handle('save-file', async (e, name, data) => {
  const ext = path.extname(String(name)).slice(1).toLowerCase();
  if (!KINDS[ext]) return null;
  const r = await dialog.showSaveDialog(BrowserWindow.fromWebContents(e.sender), {
    defaultPath: path.join(app.getPath('downloads'), path.basename(String(name))),
    filters: [{ name: KINDS[ext], extensions: [ext] }],
  });
  if (r.canceled || !r.filePath) return null;
  await fs.promises.writeFile(r.filePath, String(data), 'utf8');
  return r.filePath;
});

// ── updates ──────────────────────────────────────────────────────────
// A packaged app asks GitHub for the newest release (its public latest*.yml) a few seconds after it starts and every
// four hours; nothing of the reader's is sent. What it finds the page shows (app.js, in the reader's language). Nothing
// downloads until the reader asks, and what was downloaded installs when they restart from the page, or when they next
// quit. ThoughtDAG's flow. TEXTFOLD_UPDATES points it at another feed (a folder served over http), for a test run.
let update = null;   // what the page shows: { kind: updated | available | downloading | ready | failed, version, percent }
function tell(s) {
  update = s;
  if (win && !win.isDestroyed()) win.webContents.send('update', s);
}
// an answer to a check by hand (the menu's): shown for a moment, never kept
function answer(s) {
  if (win && !win.isDestroyed()) win.webContents.send('update', { ...s, transient: true });
}
const REPO = 'https://github.com/chenxiachan/textfold';
// The first launch after an update says so, with a link to what is new. A first install says nothing: the version it
// records is the next one's "was".
function updatedFrom() {
  const f = path.join(app.getPath('userData'), 'version');
  let was = null;
  try { was = fs.readFileSync(f, 'utf8').trim(); } catch { /* a first install */ }
  try { fs.writeFileSync(f, app.getVersion()); } catch { /* a read-only folder: nothing to say next time */ }
  return was && was !== app.getVersion() ? was : null;
}
// What an update was downloaded into stays after it is installed (electron-updater empties pending/ only for the next
// one): a second copy of the app, 190 MB, beside the copy the next update's download of what changed starts from
// (update.zip on a Mac, installer.exe on Windows). The first launch of the new version removes it, and once more a
// minute later on Windows, where the installer that just ran may still hold its file.
function dropDownloaded(again = true) {
  let dir = null;
  try {
    const name = (fs.readFileSync(path.join(process.resourcesPath, 'app-update.yml'), 'utf8').match(/^updaterCacheDirName:\s*([\w.-]+)\s*$/m) || [])[1];
    const caches = WIN ? process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local')
      : process.platform === 'darwin' ? path.join(os.homedir(), 'Library', 'Caches') : process.env.XDG_CACHE_HOME || path.join(os.homedir(), '.cache');
    if (name && name !== '.' && name !== '..') dir = path.join(caches, name, 'pending');   // (electron-updater's own place)
    if (dir) fs.rmSync(dir, { recursive: true, force: true });
  } catch {
    if (dir && again) setTimeout(() => dropDownloaded(false), 60 * 1000);
  }
}
ipcMain.handle('app:version', () => app.getVersion());
ipcMain.handle('window:fullscreen', (e, on) => {   // on: true or false; nothing: only whether it is
  const w = BrowserWindow.fromWebContents(e.sender);
  if (w && typeof on === 'boolean') w.setFullScreen(on);
  return Boolean(w && w.isFullScreen());
});
ipcMain.handle('update:state', () => (update && !update.dismissed ? update : null));
ipcMain.handle('update:dismiss', () => { if (update) update.dismissed = true; });
function setupUpdates() {
  let updater;
  try { ({ autoUpdater: updater } = require('electron-updater')); } catch { updater = null; }
  if (!app.isPackaged || !updater) {
    ipcMain.handle('update:check', () => ({ kind: 'dev' }));
    menu(async () => ({ kind: 'dev' }));
    return;
  }
  const was = updatedFrom();
  if (was) {
    update = { kind: 'updated', version: app.getVersion(), from: was, notes: `${REPO}/releases/tag/v${app.getVersion()}` };
    dropDownloaded();
    // and the window's HTTP cache, which an earlier version let grow (above); what a new version loads is new anyway
    session.defaultSession.clearCache().catch(() => null);
  }
  if (process.env.TEXTFOLD_UPDATES) updater.setFeedURL({ provider: 'generic', url: process.env.TEXTFOLD_UPDATES });
  updater.autoDownload = false;   // the reader starts the download, never the app
  updater.on('update-available', (info) => {
    if (update && (update.kind === 'downloading' || update.kind === 'ready' || update.kind === 'available' && update.version === info.version)) return;
    tell({ kind: 'available', version: info.version });
  });
  let shown = -1;
  updater.on('download-progress', (p) => {
    if (win && !win.isDestroyed()) win.setProgressBar(p.percent / 100);   // the Dock's or the taskbar's bar too
    const percent = Math.floor(p.percent);
    if (percent !== shown) { shown = percent; tell({ ...update, kind: 'downloading', percent }); }
  });
  updater.on('update-downloaded', (info) => {
    if (win && !win.isDestroyed()) win.setProgressBar(-1);
    tell({ kind: 'ready', version: info.version });
  });
  const look = () => updater.checkForUpdates().catch(() => null);
  setTimeout(look, 8000);
  setInterval(look, 4 * 60 * 60 * 1000);
  // a check by hand (Settings, the menu): always an answer, and a version dismissed earlier offered again
  const byHand = async () => {
    if (update && (update.kind === 'downloading' || update.kind === 'ready')) { tell({ ...update, dismissed: false }); return update; }
    try {
      const r = await updater.checkForUpdates();
      if (!(r && r.isUpdateAvailable)) return { kind: 'latest', version: app.getVersion() };
      tell({ kind: 'available', version: r.updateInfo.version });
      return update;
    } catch {
      return { kind: 'check-failed' };
    }
  };
  ipcMain.handle('update:check', byHand);
  menu(byHand);
  ipcMain.handle('update:download', () => {
    if (!update || !['available', 'failed'].includes(update.kind)) return;
    tell({ kind: 'downloading', version: update.version, percent: 0 });
    updater.downloadUpdate().catch(() => {
      if (win && !win.isDestroyed()) win.setProgressBar(-1);
      tell({ kind: 'failed', version: update.version });
    });
  });
  ipcMain.handle('update:install', () => {
    if (!update || update.kind !== 'ready') return;
    if (server) server.kill();   // (Windows: the installer replaces the bundled Python, which must not be running)
    updater.quitAndInstall(true, true);   // on Windows silently, and the new version opens
  });
}

// a table's words in the system's language (its own variant for Chinese in Taiwan, Hong Kong and Macao), else English
function inLocale(table) {
  const loc = app.getLocale();
  return table[/^zh-(TW|HK|MO)|Hant/i.test(loc) ? 'zh-Hant' : loc.split('-')[0]] || table.en;
}

// The menu: the system's own, with Check for Updates (in the app's menu on a Mac, in Help on Windows) and the project
// on GitHub. Its words follow the system's language, as the rest of the menu does.
const CHECK = { en: 'Check for Updates…', zh: '检查更新…', 'zh-Hant': '檢查更新…', ja: 'アップデートを確認…', ko: '업데이트 확인…',
  es: 'Buscar actualizaciones…', fr: 'Rechercher des mises à jour…', de: 'Nach Updates suchen …', pt: 'Procurar atualizações…',
  it: 'Cerca aggiornamenti…', ru: 'Проверить обновления…', hi: 'अपडेट देखें…' };
function menu(byHand) {
  const label = inLocale(CHECK);
  const check = { label, click: async () => { const r = await byHand(); if (r && r.kind !== 'available' && r.kind !== 'downloading' && r.kind !== 'ready') answer(r); } };
  const github = { label: 'TextFold on GitHub', click: () => shell.openExternal(REPO) };
  const mac = process.platform === 'darwin';
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(mac ? [{ role: 'appMenu', submenu: [{ role: 'about' }, check, { type: 'separator' }, { role: 'services' }, { type: 'separator' },
      { role: 'hide' }, { role: 'hideOthers' }, { role: 'unhide' }, { type: 'separator' }, { role: 'quit' }] }] : [{ role: 'fileMenu' }]),
    { role: 'editMenu' }, { role: 'viewMenu' }, { role: 'windowMenu' },
    { role: 'help', submenu: mac ? [github] : [check, github] },
  ]));
}

app.whenReady().then(() => { setupUpdates(); open(); });
app.on('window-all-closed', () => app.quit());
app.on('before-quit', () => { if (server) server.kill(); });
app.on('activate', () => { if (!BrowserWindow.getAllWindows().length) open(); });
