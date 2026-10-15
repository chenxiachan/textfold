// The runtime the app ships, in desktop/payload:
//   python/   a standalone CPython (python-build-standalone, the "install_only_stripped" build for the app's system:
//             Apple silicon, an Intel Mac, 64-bit Windows) with TextFold's packages (requirements.txt) installed into it
//   app/      the code: textfold/ (its web/ inside)
// On a Mac electron-builder copies it into the app's Resources and signs every Mach-O file in it with the app (hardened
// runtime, build/entitlements.mac.plist), which notarization requires. Parts of CPython the server never uses
// (tkinter and its Tcl/Tk, the test suite, IDLE) are left out: less to sign, a smaller app.
//
//   node scripts/prepare-payload.mjs                  for this computer
//   node scripts/prepare-payload.mjs darwin x64       an Intel Mac's, on Apple silicon too (its Python runs under Rosetta)
//   PBS_URL=<tar.gz url> node scripts/...             a given build
// A Windows payload is made on Windows: the packages are installed by the Python it ships.
import { execFileSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, writeFileSync, createWriteStream } from 'node:fs';
import path from 'node:path';
import { pipeline } from 'node:stream/promises';
import { Readable } from 'node:stream';
import { fileURLToPath } from 'node:url';

const desktop = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const root = path.dirname(desktop);
const payload = path.join(desktop, 'payload');
const [platform = process.platform, arch = process.arch] = process.argv.slice(2);
const TARGETS = { 'darwin-arm64': 'aarch64-apple-darwin', 'darwin-x64': 'x86_64-apple-darwin', 'win32-x64': 'x86_64-pc-windows-msvc' };
const TARGET = TARGETS[`${platform}-${arch}`];
if (!TARGET) throw new Error(`no payload for ${platform}-${arch}: ${Object.keys(TARGETS).join(', ')}`);
if ((platform === 'win32') !== (process.platform === 'win32')) throw new Error(`a ${platform} payload is made on ${platform}`);
const WIN = platform === 'win32';

async function cpythonUrl() {
  if (process.env.PBS_URL) return process.env.PBS_URL;
  const r = await fetch('https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest', {
    headers: { 'User-Agent': 'textfold-desktop', ...(process.env.GITHUB_TOKEN ? { Authorization: `Bearer ${process.env.GITHUB_TOKEN}` } : {}) },
  });
  if (!r.ok) throw new Error(`python-build-standalone releases: HTTP ${r.status}`);
  const rel = await r.json();
  const want = new RegExp(`^cpython-3\\.12\\.\\d+\\+\\d+-${TARGET}-install_only_stripped\\.tar\\.gz$`);
  const asset = rel.assets.find((a) => want.test(a.name));
  if (!asset) throw new Error(`no CPython 3.12 ${TARGET} build in ${rel.tag_name}`);
  return asset.browser_download_url;
}

rmSync(payload, { recursive: true, force: true });
mkdirSync(payload, { recursive: true });

const url = await cpythonUrl();
console.log('CPython:', url);
const res = await fetch(url);
if (!res.ok) throw new Error(`download: HTTP ${res.status}`);
await pipeline(Readable.fromWeb(res.body), createWriteStream(path.join(payload, 'python.tar.gz')));
// a relative name, run from the folder: Windows' own tar and Git's GNU tar both read it (GNU tar takes C:\… for a host)
execFileSync('tar', ['-xzf', 'python.tar.gz'], { cwd: payload, stdio: 'inherit' });
rmSync(path.join(payload, 'python.tar.gz'));
const py = WIN ? path.join(payload, 'python', 'python.exe') : path.join(payload, 'python', 'bin', 'python3');
// an Intel Mac's Python on Apple silicon runs under Rosetta, and pip then picks the Intel wheels
const run = (args, opts) => execFileSync(platform === 'darwin' && arch !== process.arch ? 'arch' : py,
  platform === 'darwin' && arch !== process.arch ? [arch === 'x64' ? '-x86_64' : '-arm64', py, ...args] : args, opts);

console.log('installing TextFold\'s packages…');
run(['-m', 'pip', 'install', '--no-cache-dir', '--disable-pip-version-check', '--no-warn-script-location',
  '-r', path.join(root, 'requirements.txt')], { stdio: 'inherit', env: { ...process.env, PYTHONNOUSERSITE: '1' } });

// what the server never uses
const stdlib = WIN ? path.join(payload, 'python', 'Lib')
  : path.join(payload, 'python', 'lib', readdirSync(path.join(payload, 'python', 'lib')).find((d) => /^python3\.\d+$/.test(d)));
for (const p of ['test', 'idlelib', 'tkinter', 'turtledemo', 'ensurepip', 'lib2to3']) rmSync(path.join(stdlib, p), { recursive: true, force: true });
const dynload = WIN ? path.join(payload, 'python', 'DLLs') : path.join(stdlib, 'lib-dynload');
for (const f of readdirSync(dynload)) if (/^(_tkinter|tcl\d|tk\d)/.test(f)) rmSync(path.join(dynload, f));
const tcl = WIN ? path.join(payload, 'python') : path.join(payload, 'python', 'lib');
for (const d of readdirSync(tcl)) if (/^(tcl|tk|itcl|thread)\d/i.test(d) || /^lib(tcl|tk)/.test(d) || (WIN && d === 'tcl')) rmSync(path.join(tcl, d), { recursive: true, force: true });
(function prune(dir) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (!e.isDirectory()) continue;
    if (e.name === '__pycache__') rmSync(path.join(dir, e.name), { recursive: true, force: true });
    else prune(path.join(dir, e.name));
  }
})(path.join(payload, 'python'));

// the code
const app = path.join(payload, 'app');
mkdirSync(app, { recursive: true });
for (const d of ['textfold']) {
  cpSync(path.join(root, d), path.join(app, d), { recursive: true, filter: (src) => !src.includes('__pycache__') });
}
writeFileSync(path.join(app, 'requirements.txt'), '# installed into ../python\n');

// a quick check that the runtime imports what the server needs (trafilatura: web pages and HTML files)
run(['-s', '-c', 'import bs4, lxml, PIL, trafilatura, mammoth, openpyxl, pdf_inspector, textfold.server; print("payload ok")'], { cwd: app, stdio: 'inherit' });
if (!existsSync(path.join(app, 'textfold', 'web', 'reader.js'))) throw new Error('web/ missing from the payload');
