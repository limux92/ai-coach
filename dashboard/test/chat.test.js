import test from 'node:test';
import assert from 'node:assert/strict';
import {
  countWords,
  renderMarkdown,
  formatModelName,
  getLocalHistory,
  saveLocalHistory,
  appendLocalMessage,
  CHAT_CACHE_KEY,
  CHAT_CACHE_TTL_MS,
} from '../src/views/chat.js';

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

test('formatModelName formats gemini model ids to readable labels', () => {
  assert.equal(formatModelName(null), 'Gemini 2.5 Flash');
  assert.equal(formatModelName(''), 'Gemini 2.5 Flash');
  assert.equal(formatModelName('gemini-2.5-flash'), 'Gemini 2.5 Flash');
  assert.equal(formatModelName('publishers/google/models/gemini-2.5-flash'), 'Gemini 2.5 Flash');
  assert.equal(formatModelName('gemini-2.5-pro'), 'Gemini 2.5 Pro');
  assert.equal(formatModelName('custom-agent-v1'), 'custom-agent-v1');
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

function createMockStorage(initial = {}) {
  const store = { ...initial };
  return {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => {
      store[k] = String(v);
    },
    removeItem: (k) => {
      delete store[k];
    },
    _raw: store,
  };
}

test('getLocalHistory returns empty array for empty or missing storage', () => {
  assert.deepEqual(getLocalHistory(null), []);
  assert.deepEqual(getLocalHistory(createMockStorage()), []);
});

test('saveLocalHistory and getLocalHistory persist and retrieve chat messages within TTL', () => {
  const storage = createMockStorage();
  const sample = [
    { role: 'user', content: 'What is my CP?' },
    { role: 'coach', content: 'Your CP is 298W.' },
  ];

  saveLocalHistory(sample, storage);
  const retrieved = getLocalHistory(storage);
  assert.equal(retrieved.length, 2);
  assert.equal(retrieved[0].content, 'What is my CP?');
  assert.equal(retrieved[1].content, 'Your CP is 298W.');
});

test('saveLocalHistory limits stored messages to 50 items', () => {
  const storage = createMockStorage();
  const bulk = Array.from({ length: 65 }, (_, i) => ({ role: 'user', content: `Msg ${i}` }));
  saveLocalHistory(bulk, storage);

  const retrieved = getLocalHistory(storage);
  assert.equal(retrieved.length, 50);
  assert.equal(retrieved[0].content, 'Msg 15');
  assert.equal(retrieved[49].content, 'Msg 64');
});

test('getLocalHistory expires entries older than CHAT_CACHE_TTL_MS', () => {
  const storage = createMockStorage();
  const expiredTime = Date.now() - (CHAT_CACHE_TTL_MS + 1000);
  storage.setItem(
    CHAT_CACHE_KEY,
    JSON.stringify({
      savedAt: expiredTime,
      messages: [{ role: 'user', content: 'Old message' }],
    }),
  );

  assert.deepEqual(getLocalHistory(storage), []);
});

test('getLocalHistory handles corrupted JSON safely', () => {
  const storage = createMockStorage();
  storage.setItem(CHAT_CACHE_KEY, 'not-valid-json{{{');
  assert.deepEqual(getLocalHistory(storage), []);
});

test('appendLocalMessage appends to existing history and saves to storage', () => {
  const storage = createMockStorage();
  saveLocalHistory([{ role: 'user', content: 'Msg 1' }], storage);

  appendLocalMessage({ role: 'coach', content: 'Msg 2' }, storage);
  const retrieved = getLocalHistory(storage);
  assert.equal(retrieved.length, 2);
  assert.equal(retrieved[0].content, 'Msg 1');
  assert.equal(retrieved[1].content, 'Msg 2');
});
