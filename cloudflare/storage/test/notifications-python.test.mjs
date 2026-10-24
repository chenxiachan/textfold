import { migrate } from './schema.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawn } from 'node:child_process';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';

const username = 'events-reader', password = 'events-fixture-only';
function service() {
  return new Miniflare(convertV4MiniflareOptions({ host: '127.0.0.1', modules: true,
    scriptPath: '.wrangler/build/index.js', compatibilityDate: '2026-10-23', compatibilityFlags: ['nodejs_compat'],
    d1Databases: ['DB'], r2Buckets: ['BLOBS'],
    durableObjects: { NOTIFICATIONS: { className: 'LibraryNotifications', useSQLite: true } },
    bindings: { STORAGE_USERNAME: username, STORAGE_PASSWORD: password } }));
}

async function runPython(mf, method) {
  const root = resolve('../..'), temp = resolve('.wrangler/events-temp');
  await mkdir(temp, { recursive: true });
  const local = resolve(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
  const python = process.env.TEXTFOLD_TEST_PYTHON || (existsSync(local) ? local : 'python3');
  const args = ['-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_storage_notifications.py', '-v'];
  if (method) args.push('-k', method);
  const child = spawn(python, args, {
    cwd: root, env: { ...process.env, TEMP: temp, TMP: temp, PYTHONUTF8: '1',
      TEXTFOLD_TEST_STORAGE_URL: method ? '' : String(await mf.ready),
      TEXTFOLD_TEST_LEGACY_STORAGE_URL: method ? String(await mf.ready) : '',
      TEXTFOLD_TEST_STORAGE_USER: username, TEXTFOLD_TEST_STORAGE_PASSWORD: password },
    stdio: ['ignore', 'pipe', 'pipe'] });
  let output = '';
  child.stdout.on('data', data => { output += data; }); child.stderr.on('data', data => { output += data; });
  const timer = setTimeout(() => child.kill(), 110000);
  try {
    const code = await new Promise((done, reject) => { child.once('exit', done); child.once('error', reject); });
    assert.equal(code, 0, output); console.log(output.trim());
  } finally { clearTimeout(timer); }
}

test('Python clients subscribe and reconcile over real hibernatable WebSockets', { timeout: 120000 }, async () => {
  const mf = service();
  try {
    await migrate(await mf.getD1Database('DB'));
    await runPython(mf);
  } finally { await mf.dispose(); }
});

for (const method of ['test_v1_library_changes_are_caught_at_v2_activation', 'test_v1_library_changes_are_caught_after_cache_reopen',
                      'test_transition_preserves_pending_library_changes']) {
  test(method, { timeout: 120000 }, async () => {
    // Each legacy case needs an un-fenced library; never reset a running DB.
    const mf = service();
    try {
      await migrate(await mf.getD1Database('DB'));
      await runPython(mf, method);
    } finally { await mf.dispose(); }
  });
}
