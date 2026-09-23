import { icon } from '../ui.js';
import { escapeHTML as esc } from '../data.js';

export function loginScreen(message = '') {
  return /* HTML */ `
    <main id="main" class="login-page">
      <div class="login-layout">
        <section class="login-copy">
          <h1>AI Workout Builder</h1>
          <p>
            Liberate your training data to feed your AI agent with the correct insight to create
            your next session.
          </p>
          <button class="button primary login-button" data-action="login">
            Sign in with Google ${icon('arrow')}
          </button>
          ${message ? `<p class="login-error" role="alert">${esc(message)}</p>` : ''}
        </section>
      </div>
    </main>
  `;
}
