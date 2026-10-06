import { escapeHTML as esc } from '../data.js';

export function countWords(text) {
  if (typeof text !== 'string' || text.trim() === '') return 0;
  return text.trim().split(/\s+/).length;
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
    const trimmed = lines[i].trim();
    if (trimmed.startsWith('```')) {
      const lang = trimmed.slice(3).trim();
      const buf = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        buf.push(esc(lines[i++]));
      }
      i++;
      const cls = lang ? ` class="language-${esc(lang)}"` : '';
      out.push(`<pre><code${cls}>${buf.join('\n')}</code></pre>`);
    } else if (/^[-*]\s/.test(trimmed)) {
      const items = [];
      while (i < lines.length && /^[-*]\s/.test(lines[i].trim())) {
        items.push(`<li>${inline(esc(lines[i++].trim().replace(/^[-*]\s/, '')))}</li>`);
      }
      out.push(`<ul>${items.join('')}</ul>`);
    } else if (trimmed === '') {
      i++;
    } else {
      const buf = [];
      while (
        i < lines.length &&
        lines[i].trim() !== '' &&
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
  const root = getRoot();
  root.innerHTML = '';
  if (prevFocus && typeof prevFocus.focus === 'function') prevFocus.focus();
}

export async function openChatDrawer(api) {
  if (isOpen) return;
  isOpen = true;
  activeApi = api;
  prevFocus = document.activeElement;
  document.body.classList.add('chat-drawer-open');

  const root = getRoot();
  root.innerHTML = /* HTML */ `
    <div class="drawer-backdrop chat-backdrop" data-action="close-chat"></div>
    <aside class="drawer chat-drawer" role="dialog" aria-modal="true" aria-label="AI Coach">
      <header class="drawer-header chat-header">
        <div class="drawer-kind">
          <span class="chat-status-dot"></span>
          <strong>AI COACH</strong>
        </div>
        <button class="icon-button" data-action="close-chat" aria-label="Lukk" title="Lukk">
          ✕
        </button>
      </header>

      <div class="chat-goal-section">
        <button class="chat-goal-toggle" id="chat-goal-toggle" aria-expanded="false">
          <span>🎯 Sesongmål & fokus</span>
          <span id="chat-goal-arrow">▾</span>
        </button>
        <div class="chat-goal-body" id="chat-goal-body" style="display: none;">
          <textarea
            id="chat-goal-input"
            class="chat-goal-textarea"
            placeholder="Beskriv sesongmålet ditt (maks 100 ord)..."
            rows="3"
          ></textarea>
          <div class="chat-goal-footer">
            <span id="chat-goal-counter">0 / 100 ord</span>
            <button id="chat-goal-save" class="button primary small">Lagre mål</button>
          </div>
          <p id="chat-goal-error" class="form-error" style="display: none;">
            Maks 100 ord tillatt.
          </p>
        </div>
      </div>

      <div class="chat-messages" id="chat-messages" role="log" aria-live="polite">
        <div class="chat-loading">
          <span class="spinner" aria-hidden="true"></span> Laster samtale...
        </div>
      </div>

      <form class="chat-input-form" id="chat-input-form">
        <textarea
          id="chat-message-input"
          class="chat-message-input"
          placeholder="Spør coachen om form, CP eller neste økt..."
          rows="1"
          required
        ></textarea>
        <button
          type="submit"
          id="chat-send-btn"
          class="button primary chat-send-btn"
          aria-label="Send"
        >
          Send
        </button>
      </form>
    </aside>
  `;

  bindDrawerEvents(root);
  await Promise.all([loadGoal(), loadHistory()]);
}

export function toggleChatDrawer(api) {
  if (isOpen) closeChatDrawer();
  else openChatDrawer(api);
}

function bindDrawerEvents(root) {
  root
    .querySelectorAll('[data-action="close-chat"]')
    .forEach((b) => b.addEventListener('click', closeChatDrawer));

  const toggle = root.querySelector('#chat-goal-toggle');
  const body = root.querySelector('#chat-goal-body');
  const arrow = root.querySelector('#chat-goal-arrow');
  const input = root.querySelector('#chat-goal-input');
  const counter = root.querySelector('#chat-goal-counter');
  const save = root.querySelector('#chat-goal-save');
  const err = root.querySelector('#chat-goal-error');

  toggle?.addEventListener('click', () => {
    const exp = toggle.getAttribute('aria-expanded') === 'true';
    toggle.setAttribute('aria-expanded', String(!exp));
    body.style.display = exp ? 'none' : 'block';
    arrow.textContent = exp ? '▾' : '▴';
  });

  const checkGoal = () => {
    const words = countWords(input.value);
    counter.textContent = `${words} / 100 ord`;
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
    save.textContent = 'Lagrer...';
    try {
      await activeApi('/user/goal', null, {
        method: 'POST',
        body: JSON.stringify({ goal: input.value.trim() }),
      });
      save.textContent = 'Lagret ✓';
      setTimeout(() => {
        save.textContent = 'Lagre mål';
        save.disabled = false;
      }, 1500);
    } catch {
      save.textContent = 'Feilet';
      setTimeout(() => {
        save.textContent = 'Lagre mål';
        save.disabled = false;
      }, 1500);
    }
  });

  const form = root.querySelector('#chat-input-form');
  const msgInput = root.querySelector('#chat-message-input');
  const sendBtn = root.querySelector('#chat-send-btn');

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
  const input = document.getElementById('chat-goal-input');
  const counter = document.getElementById('chat-goal-counter');
  if (!input || !activeApi) return;
  try {
    const res = await activeApi('/user/goal');
    if (res?.goal) {
      input.value = res.goal;
      if (counter) counter.textContent = `${countWords(res.goal)} / 100 ord`;
    }
  } catch {}
}

async function loadHistory() {
  const container = document.getElementById('chat-messages');
  if (!container || !activeApi) return;
  try {
    const messages = await activeApi('/chat/history');
    container.innerHTML = '';
    if (!Array.isArray(messages) || messages.length === 0) {
      renderWelcome(container);
      return;
    }
    for (const m of messages) {
      appendMessage(
        m.role === 'model' || m.role === 'assistant' ? 'coach' : 'user',
        m.content,
        false,
      );
    }
    container.scrollTop = container.scrollHeight;
  } catch {
    container.innerHTML = '';
    renderWelcome(container);
  }
}

function renderWelcome(container) {
  const el = document.createElement('div');
  el.className = 'chat-msg coach';
  el.innerHTML = renderMarkdown(
    'Hei Magne! Jeg er din AI Coach. Spør meg om formkurven, Critical Power, dagsform eller forslag til neste økt!',
  );
  container.appendChild(el);
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
    const response = await activeApi('/chat/stream', null, {
      method: 'POST',
      body: JSON.stringify({ message: text }),
      stream: true,
    });

    const reader = response.body.getReader();
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
          if (payload.chunk) {
            acc += payload.chunk;
            coachEl.innerHTML = renderMarkdown(acc);
            c.scrollTop = c.scrollHeight;
          }
          if (payload.error) {
            coachEl.innerHTML += `<p class="form-error">${esc(payload.error)}</p>`;
          }
        } catch {}
      }
    }
    if (!acc) {
      coachEl.innerHTML = renderMarkdown('Jeg fikk ikke generert noe svar. Vennligst prøv igjen.');
    }
  } catch (err) {
    coachEl.innerHTML = `<p class="form-error">Feil: ${esc(err.message || 'Tilkoblingsfeil')}</p>`;
  } finally {
    coachEl.classList.remove('streaming');
    isStreaming = false;
    if (sendBtn) sendBtn.disabled = false;
    c.scrollTop = c.scrollHeight;
  }
}
