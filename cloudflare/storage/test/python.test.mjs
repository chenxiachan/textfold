import { migrate } from './schema.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawn } from 'node:child_process';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';

test('Python document backend contract through HTTP and real local D1/R2', { timeout: 120000 }, async () => {
  const username = 'reader-读者', password = 'integration-fixture-only';
  const mf = new Miniflare(convertV4MiniflareOptions({ host: '127.0.0.1', modules: true,
    scriptPath: '.wrangler/build/index.js', compatibilityDate: '2026-10-23',
    compatibilityFlags: ['nodejs_compat'], d1Databases: ['DB'], r2Buckets: ['BLOBS'],
    bindings: { STORAGE_USERNAME: username, STORAGE_PASSWORD: password } }));
  try {
    const db = await mf.getD1Database('DB');
    await migrate(db);
    const root = resolve('../..'), temp = resolve('.wrangler/python-temp');
    await mkdir(temp, { recursive: true });
    const localPython = resolve(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
    const python = process.env.TEXTFOLD_TEST_PYTHON || (existsSync(localPython) ? localPython : 'python3');
    const child = spawn(python, ['-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_cloud_storage.py', '-v'], {
      cwd: root, env: { ...process.env, TEMP: temp, TMP: temp, PYTHONUTF8: '1',
        TEXTFOLD_TEST_STORAGE_URL: String(await mf.ready), TEXTFOLD_TEST_STORAGE_USER: username,
        TEXTFOLD_TEST_STORAGE_PASSWORD: password }, stdio: ['ignore', 'pipe', 'pipe'] });
    let output = '';
    child.stdout.on('data', chunk => { output += chunk; });
    child.stderr.on('data', chunk => { output += chunk; });
    const timer = setTimeout(() => child.kill(), 110000);
    try {
      const code = await new Promise((done, reject) => { child.once('exit', done); child.once('error', reject); });
      assert.equal(code, 0, output);
      console.log(output.trim());
    } finally { clearTimeout(timer); }
  } finally { await mf.dispose(); }
});
