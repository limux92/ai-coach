import test from 'node:test';
import assert from 'node:assert/strict';
import { countWords, renderMarkdown } from '../src/views/chat.js';

test('countWords counts whitespace separated words accurately', () => {
  assert.equal(countWords(''), 0);
  assert.equal(countWords('   \n  \t  '), 0);
  assert.equal(countWords(null), 0);
  assert.equal(countWords(undefined), 0);
  assert.equal(countWords('One two three'), 3);
  assert.equal(countWords('   Leading,   multiple   spaces, and\ttrailing.  '), 5);
  const hundredWords = Array(100).fill('tempo').join(' ');
  assert.equal(countWords(hundredWords), 100);
});

test('renderMarkdown escapes raw HTML and formats basic markdown', () => {
  assert.equal(renderMarkdown(''), '');
  assert.equal(renderMarkdown(null), '');

  // HTML escaping
  const xss = renderMarkdown('<script>alert("xss")</script>');
  assert.match(xss, /&lt;script&gt;alert\(&quot;xss&quot;\)&lt;\/script&gt;/);
  assert.doesNotMatch(xss, /<script>/);

  // Inline formatting
  const inline = renderMarkdown('Here is `inline code`, **bold target**, and *italic pace*.');
  assert.match(inline, /<code>inline code<\/code>/);
  assert.match(inline, /<strong>bold target<\/strong>/);
  assert.match(inline, /<em>italic pace<\/em>/);

  // Fenced code block
  const codeBlock = renderMarkdown('```json\n{"watts": 280}\n```');
  assert.match(
    codeBlock,
    /<pre><code class="language-json">\{&quot;watts&quot;: 280\}<\/code><\/pre>/,
  );

  // Bullet list
  const list = renderMarkdown('- Zone 2 endurance (90 min)\n- 5x3 min VO2max intervals');
  assert.match(
    list,
    /<ul><li>Zone 2 endurance \(90 min\)<\/li><li>5x3 min VO2max intervals<\/li><\/ul>/,
  );
});
