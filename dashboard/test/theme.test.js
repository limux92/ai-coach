import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(new URL('../src/styles/tokens.css', import.meta.url), 'utf8');
const colors = Object.fromEntries(
  [...css.matchAll(/--([\w-]+): (#\w{6});/g)].map((m) => [m[1], m[2]]),
);
const luminance = (hex) =>
  hex
    .slice(1)
    .match(/../g)
    .map((part) => {
      const n = parseInt(part, 16) / 255;
      return n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4;
    })
    .reduce((sum, n, i) => sum + n * [0.2126, 0.7152, 0.0722][i], 0);

test('dark theme text, muted labels and sport labels retain readable contrast', () => {
  const pairs = ['ink', 'muted', 'blue'].flatMap((text) =>
    ['bg', 'surface', 'surface-raised', 'navy', 'hover'].map((background) => [text, background]),
  );
  pairs.push(
    ['teal', 'run-bg'],
    ['amber', 'cycle-bg'],
    ['other', 'other-bg'],
    ['danger', 'surface'],
  );
  for (const [text, background] of pairs) {
    const [light, dark] = [luminance(colors[text]), luminance(colors[background])].sort(
      (a, b) => b - a,
    );
    assert((light + 0.05) / (dark + 0.05) >= 4.5, `${text} on ${background} needs more contrast`);
  }
});
