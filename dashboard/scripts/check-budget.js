import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import { gzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';

// Source limits keep individual edits small enough for the local worker.
const source = new URL('../src/', import.meta.url);
let largest = { bytes: 0, name: '' };
for (const name of await readdir(source, { recursive: true })) {
  if (!/\.(js|css)$/.test(name)) continue;
  const bytes = await readFile(new URL(name, source));
  assert(bytes.length <= 12_000, `${name}: split this source file (over 12 KB)`);
  const lines = bytes.toString().split('\n');
  for (const [index, line] of lines.entries()) {
    assert(line.length <= 240, `${name}:${index + 1}: split this line (over 240 characters)`);
  }
  if (bytes.length > largest.bytes) largest = { bytes: bytes.length, name };
}

// Sum every emitted entry/chunk so moving code to another file cannot hide growth.
const output = new URL('../../adapters/mcp/static/dashboard/assets/', import.meta.url);
const sizes = { js: 0, css: 0 };
for (const name of await readdir(output)) {
  const type = name.split('.').at(-1);
  if (!(type in sizes)) continue;
  sizes[type] += gzipSync(await readFile(new URL(name, output))).length;
}
assert(sizes.js > 0 && sizes.css > 0, `Build assets first: ${fileURLToPath(output)}`);
assert(sizes.js <= 50_000, `JavaScript gzip budget exceeded: ${sizes.js} / 50000 bytes`);
assert(sizes.css <= 6_000, `CSS gzip budget exceeded: ${sizes.css} / 6000 bytes`);
console.log(`Largest source: ${largest.name}, ${largest.bytes} bytes`);
console.log(`Gzip totals: JavaScript ${sizes.js} / 50000; CSS ${sizes.css} / 6000 bytes`);
