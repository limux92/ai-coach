import { escapeHTML as esc } from '../data.js';

export const CHAT_CACHE_KEY = 'ai_coach_chat_history';
export const CHAT_CACHE_TTL_MS = 30 * 24 * 60 * 60 * 1000;

export function countWords(text) {
  if (typeof text !== 'string' || text.trim() === '') return 0;
  return text.trim().split(/\s+/).length;
}

export function formatModelName(model) {
  if (!model) return 'Gemini 2.5 Flash';
  for (const m of ['2.5 Pro', '2.5 Flash', '1.5 Pro', '1.5 Flash']) {
    if (model.includes(m.toLowerCase().replace(' ', '-'))) return `Gemini ${m}`;
  }
  return model;
}

export function getLocalHistory(storage = globalThis?.localStorage) {
  try {
    const raw = storage?.getItem(CHAT_CACHE_KEY);
    if (!raw) return [];
    const { savedAt, messages } = JSON.parse(raw);
    return Date.now() - (savedAt || 0) <= CHAT_CACHE_TTL_MS && Array.isArray(messages)
      ? messages
      : [];
  } catch {
    return [];
  }
}

export function saveLocalHistory(messages, storage = globalThis?.localStorage) {
  try {
    if (storage && Array.isArray(messages)) {
      storage.setItem(
        CHAT_CACHE_KEY,
        JSON.stringify({ savedAt: Date.now(), messages: messages.slice(-50) }),
      );
    }
  } catch {}
}

export function appendLocalMessage(msg, storage = globalThis?.localStorage) {
  const list = getLocalHistory(storage);
  list.push(msg);
  saveLocalHistory(list, storage);
}

export function renderMarkdown(text) {
  if (!text) return '';
  const inline = (s) =>
    s
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*]+)\*/g, '<em>$1</em>');

  const lines = text.split('\n');
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const t = lines[i].trim();
    if (t.startsWith('```')) {
      const lang = t.slice(3).trim();
      const buf = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith('```')) buf.push(esc(lines[i++]));
      i++;
      const cls = lang ? ` class="language-${esc(lang)}"` : '';
      out.push(`<pre><code${cls}>${buf.join('\n')}</code></pre>`);
    } else if (/^[-*]\s/.test(t)) {
      const items = [];
      while (i < lines.length && /^[-*]\s/.test(lines[i].trim())) {
        items.push(`<li>${inline(esc(lines[i++].trim().replace(/^[-*]\s/, '')))}</li>`);
      }
      out.push(`<ul>${items.join('')}</ul>`);
    } else if (!t) {
      i++;
    } else {
      const buf = [];
      while (
        i < lines.length &&
        lines[i].trim() &&
        !lines[i].trim().startsWith('```') &&
        !/^[-*]\s/.test(lines[i].trim())
      ) {
        buf.push(lines[i++]);
      }
      out.push(`<p>${inline(esc(buf.join(' ')))}</p>`);
    }
  }
  return out.join('');
}

let activeApi = null;
let isOpen = false;
let isStreaming = false;
let prevFocus = null;

function getRoot() {
  let root = document.getElementById('chat-drawer-root');
  if (!root) {
    root = document.createElement('div');
    root.id = 'chat-drawer-root';
    document.body.appendChild(root);
  }
  return root;
}

export function closeChatDrawer() {
  if (!isOpen) return;
  isOpen = false;
  document.body.classList.remove('chat-drawer-open');
  getRoot().innerHTML = '';
  prevFocus?.focus?.();
}

export async function openChatDrawer(api) {
  if (isOpen) return;
  isOpen = true;
  activeApi = api;
  prevFocus = document.activeElement;
  document.body.classList.add('chat-drawer-open');

  const root = getRoot();
  root.innerHTML = `<div class="drawer-backdrop chat-backdrop" data-action="close-chat"></div>
<aside class="drawer chat-drawer" role="dialog" aria-modal="true" aria-label="AI Coach">
<header class="drawer-header chat-header">
<div class="drawer-kind"><span aria-hidden="true">✦</span><strong>AI COACH</strong>
<span class="chat-model-badge" id="chat-model-badge">Gemini 2.5 Flash</span></div>
<button class="icon-button" data-action="close-chat" aria-label="Close">✕</button>
</header>
<div class="chat-goal-section">
<button class="chat-goal-toggle" id="chat-goal-toggle" aria-expanded="false">
<span>🎯 Season Goal & Focus</span><span id="chat-goal-arrow">▾</span></button>
<div class="chat-goal-body" id="chat-goal-body" style="display:none;">
<textarea id="chat-goal-input" class="chat-goal-textarea" placeholder="Describe your season goal (max 100 words)..." rows="3"></textarea>
<div class="chat-goal-footer"><span id="chat-goal-counter">0 / 100 words</span>
<button id="chat-goal-save" class="button primary small">Save Goal</button></div>
<p id="chat-goal-error" class="form-error" style="display:none;">Maximum 100 words allowed.</p>
</div>
</div>
<div class="chat-messages" id="chat-messages" role="log" aria-live="polite">
<div class="chat-loading"><span class="spinner" aria-hidden="true"></span> Loading conversation...</div>
</div>
<form class="chat-input-form" id="chat-input-form">
<textarea id="chat-message-input" class="chat-message-input" placeholder="Ask coach about fitness, CP, or next workout..." rows="1" required></textarea>
<button type="submit" id="chat-send-btn" class="button primary chat-send-btn" aria-label="Send">Send</button>
</form>
</aside>`;

  bindDrawerEvents(root);
  await Promise.all([loadGoal(), loadHistory(), loadModel()]);
}

export function toggleChatDrawer(api) {
  if (isOpen) closeChatDrawer();
  else openChatDrawer(api);
}

function bindDrawerEvents(root) {
  root
    .querySelectorAll('[data-action="close-chat"]')
    .forEach((b) => b.addEventListener('click', closeChatDrawer));

  const [toggle, body, arrow, input, counter, save, err, form, msgInput, sendBtn] = [
    '#chat-goal-toggle',
    '#chat-goal-body',
    '#chat-goal-arrow',
    '#chat-goal-input',
    '#chat-goal-counter',
    '#chat-goal-save',
    '#chat-goal-error',
    '#chat-input-form',
    '#chat-message-input',
    '#chat-send-btn',
  ].map((s) => root.querySelector(s));

  toggle?.addEventListener('click', () => {
    const exp = toggle.getAttribute('aria-expanded') === 'true';
    toggle.setAttribute('aria-expanded', String(!exp));
    body.style.display = exp ? 'none' : 'block';
    arrow.textContent = exp ? '▾' : '▴';
  });

  const checkGoal = () => {
    const words = countWords(input.value);
    counter.textContent = `${words} / 100 words`;
    const ok = words <= 100;
    counter.classList.toggle('error', !ok);
    save.disabled = !ok;
    err.style.display = ok ? 'none' : 'block';
    return ok;
  };

  input?.addEventListener('input', checkGoal);

  save?.addEventListener('click', async () => {
    if (!checkGoal()) return;
    save.disabled = true;
    save.textContent = 'Saving...';
    try {
      await activeApi('/user/goal', null, {
        method: 'POST',
        body: JSON.stringify({ goal: input.value.trim() }),
      });
      save.textContent = 'Saved ✓';
    } catch {
      save.textContent = 'Failed';
    } finally {
      setTimeout(() => {
        save.textContent = 'Save Goal';
        save.disabled = false;
      }, 1500);
    }
  });

  msgInput?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (isStreaming) return;
    const text = msgInput.value.trim();
    if (!text) return;

    msgInput.value = '';
    appendMessage('user', text);
    appendLocalMessage({ role: 'user', content: text, created_at: new Date().toISOString() });
    await streamChat(text, sendBtn);
  });

  const onKey = (e) => {
    if (e.key === 'Escape' && isOpen) {
      closeChatDrawer();
      document.removeEventListener('keydown', onKey);
    }
  };
  document.addEventListener('keydown', onKey);
}

async function loadGoal() {
  if (!activeApi) return;
  try {
    const res = await activeApi('/user/goal');
    const input = document.getElementById('chat-goal-input');
    const counter = document.getElementById('chat-goal-counter');
    if (input && res?.goal) {
      input.value = res.goal;
      if (counter) counter.textContent = `${countWords(res.goal)} / 100 words`;
    }
  } catch {}
}

async function loadModel() {
  const badge = document.getElementById('chat-model-badge');
  if (!badge || !activeApi) return;
  try {
    const res = await activeApi('/chat/model');
    if (res?.model) badge.textContent = formatModelName(res.model);
  } catch {}
}

async function loadHistory() {
  const c = document.getElementById('chat-messages');
  if (!c || !activeApi) return;
  const cached = getLocalHistory();
  const roleOf = (m) => (m.role === 'model' || m.role === 'assistant' ? 'coach' : 'user');
  const render = (items) => {
    c.innerHTML = '';
    for (const m of items) appendMessage(roleOf(m), m.content, false);
    c.scrollTop = c.scrollHeight;
  };
  if (cached.length) render(cached);
  try {
    const res = await activeApi('/chat/history');
    if (Array.isArray(res) && res.length) {
      const mapped = res.map((m) => ({
        role: roleOf(m),
        content: m.content,
        created_at: m.created_at,
      }));
      render(mapped);
      saveLocalHistory(mapped);
      return;
    }
  } catch {}
  if (!cached.length) {
    c.innerHTML = '';
    renderWelcome();
  }
}

function renderWelcome() {
  appendMessage(
    'coach',
    "Hi Magne! I'm your AI Coach powered by Gemini. Ask me about your fitness curve (PMC), Critical Power, daily readiness, or workout recommendations!",
    false,
  );
}

function appendMessage(role, content, scroll = true) {
  const c = document.getElementById('chat-messages');
  if (!c) return null;
  const el = document.createElement('div');
  el.className = `chat-msg ${role}`;
  if (role === 'user') el.textContent = content;
  else el.innerHTML = renderMarkdown(content);
  c.appendChild(el);
  if (scroll) c.scrollTop = c.scrollHeight;
  return el;
}

async function streamChat(text, sendBtn) {
  const c = document.getElementById('chat-messages');
  if (!c || !activeApi) return;

  isStreaming = true;
  if (sendBtn) sendBtn.disabled = true;

  const coachEl = document.createElement('div');
  coachEl.className = 'chat-msg coach streaming';
  coachEl.innerHTML = '<span class="spinner" aria-hidden="true"></span>';
  c.appendChild(coachEl);
  c.scrollTop = c.scrollHeight;

  let acc = '';
  try {
    const res = await activeApi('/chat/stream', null, {
      method: 'POST',
      body: JSON.stringify({ message: text }),
      stream: true,
    });

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (!raw) continue;
        try {
          const payload = JSON.parse(raw);
          if (payload.model) {
            const badge = document.getElementById('chat-model-badge');
            if (badge) badge.textContent = formatModelName(payload.model);
          }
          if (payload.chunk) {
            acc += payload.chunk;
            coachEl.innerHTML = renderMarkdown(acc);
            c.scrollTop = c.scrollHeight;
          }
          if (payload.error) coachEl.innerHTML += `<p class="form-error">${esc(payload.error)}</p>`;
        } catch {}
      }
    }
    if (!acc) {
      coachEl.innerHTML = renderMarkdown('I could not generate a response. Please try again.');
    } else {
      appendLocalMessage({ role: 'coach', content: acc, created_at: new Date().toISOString() });
    }
  } catch (err) {
    coachEl.innerHTML = `<p class="form-error">Error: ${esc(err.message || 'Connection error')}</p>`;
  } finally {
    coachEl.classList.remove('streaming');
    isStreaming = false;
    if (sendBtn) sendBtn.disabled = false;
    c.scrollTop = c.scrollHeight;
  }
}
