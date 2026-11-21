const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const reader = fs.readFileSync(path.join(__dirname, '../textfold/web/reader.js'), 'utf8');
const code = reader.slice(reader.indexOf('  const pdfNorm = '), reader.indexOf('  function cmpLabel()'))
  + reader.slice(reader.indexOf('  async function indexWords()'), reader.indexOf('  // a paragraph shown on its page:'));

async function locate(lines, entries) {
  const units = {}, blocks = [];
  for (const [id, label, text] of entries) {
    units[id] = { toks: text.split(' ').map(t => ({ t })) };
    if (label !== null) blocks.push({ k: 'fig', cap: id, label });
  }
  const items = lines.map((str, i) => ({ str, width: str.length * 5, transform: [10, 0, 0, 10, 50, 30 + i * 20] }));
  const page = { getTextContent: async () => ({ items }) };
  const cmp = { lib: { Util: { transform: (_, t) => t } }, pages: [{ n: 1, page, vp: {} }], spans: new Map() };
  return vm.runInNewContext(code + `
    (async () => {
      await indexWords();
      return Object.keys(U).map(id => {
        const span = locate(id);
        return span ? Math.floor((span.boxes[0].y - 20) / 20) : null;
      });
    })()
  `, { U: units, D: { blocks }, cmp });
}

test('figure and table numbers distinguish repeated captions, including short captions', async () => {
  const result = await locate([
    'Table 1: Single-Frame Depth Evaluation.',
    'Figure 15: Single-Frame Depth Evaluation.',
    'Table 15: Single-Frame Depth Evaluation.',
    'TABLE IV',
    'Video Depth Evaluation.',
  ], [
    ['table', 'Table 15', 'Single-Frame Depth Evaluation.'],
    ['fig', 'Fig. 15', 'Single-Frame Depth Evaluation.'],
    ['roman', 'Table IV', 'Video Depth Evaluation.'],
  ]);
  assert.deepEqual(Array.from(result), [2, 1, 3]);
});

test('verify a numbered caption despite citations, and distinguish it from a running reference', async () => {
  const result = await locate([
    'Table 7 reports peak GPU memory across the full evaluation suite.',
    'Table 7 Peak GPU memory across the full evaluation suite. ScanNet-50 [8] and NRGBD results.',
    'Table 10: An entirely different evaluation on a separate dataset.',
  ], [
    ['matched', 'Table 7', 'Peak GPU memory across the full evaluation suite. ScanNet-50 and NRGBD results.'],
    ['renumbered', 'Table 8', 'Peak GPU memory across the full evaluation suite. ScanNet-50 and NRGBD results.'],
    ['different', 'Table 10', 'Peak GPU memory across the full evaluation suite. ScanNet-50 and NRGBD results.'],
  ]);
  assert.deepEqual(Array.from(result), [1, null, null]);
});

test('use unique text without a label, reject ambiguous captions, and still locate ordinary paragraphs', async () => {
  const result = await locate([
    'Unique caption about an existing experiment.',
    'Repeated caption about a different experiment.',
    'Repeated caption about a different experiment.',
    'An ordinary paragraph with enough words to locate.',
  ], [
    ['unique', '', 'Unique caption about an existing experiment.'],
    ['ambiguous', '', 'Repeated caption about a different experiment.'],
    ['paragraph', null, 'An ordinary paragraph with enough words to locate.'],
  ]);
  assert.deepEqual(Array.from(result), [0, null, 3]);
});
