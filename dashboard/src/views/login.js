import { icon } from '../ui.js';
import { escapeHTML as esc } from '../data.js';

function termsModalHTML() {
  return '<div id="terms-box" class="terms-box hidden"><h4>Terms</h4><p>199 NOK/mo. Direct debit / Vipps with no lock-in period.</p></div>';
}

export function loginScreen(message = '', mode = 'signin') {
  const isRegister = mode === 'register';
  return /* HTML */ `
    <main id="main" class="login-page">
      <div class="login-layout">
        <section class="login-copy">
          <h1>AI Workout Builder</h1>
          <p>
            Liberate your training data to feed your AI agent with the correct insight to create
            your next session.
          </p>
          <div class="auth-mode-toggle">
            <button
              class="auth-toggle-btn ${!isRegister ? 'active' : ''}"
              data-action="auth-mode"
              data-mode="signin"
            >
              Sign In
            </button>
            <button
              class="auth-toggle-btn ${isRegister ? 'active' : ''}"
              data-action="auth-mode"
              data-mode="register"
            >
              Register
            </button>
          </div>
          <button class="button primary login-button" data-action="login">
            ${isRegister ? 'Register with Google' : 'Sign in with Google'} ${icon('arrow')}
          </button>
          ${
            isRegister
              ? /* HTML */ `
                  <p class="terms-note">
                    By registering you accept our
                    <button type="button" class="terms-link" data-action="toggle-terms">
                      terms and conditions</button
                    >.
                  </p>
                  ${termsModalHTML()}
                `
              : ''
          }
          ${message ? `<p class="login-error" role="alert">${esc(message)}</p>` : ''}
        </section>
      </div>
    </main>
  `;
}

export function pendingPaymentScreen(user) {
  return /* HTML */ `
    <main id="main" class="login-page">
      <div class="login-layout">
        <section class="login-copy">
          <h1>Account Created</h1>
          <p>
            Welcome, <strong>${esc(user?.display_name || user?.email || 'Athlete')}</strong>! Your
            athlete profile is ready.
          </p>
          <div class="onboarding-box">
            <span class="status-badge warning">Payment Required</span>
            <p>Complete your subscription to activate automated syncing and AI Coach.</p>
            <div class="terms-section">
              <label class="terms-label">
                <input type="checkbox" id="terms-checkbox" checked />
                <span
                  >I accept the
                  <button type="button" class="terms-link" data-action="toggle-terms">
                    terms and conditions
                  </button>
                  (199 NOK/mo, no lock-in)</span
                >
              </label>
              ${termsModalHTML()}
              <p id="terms-error" class="login-error" style="display:none;"></p>
            </div>
            <button class="button primary checkout-button" data-action="checkout">
              Pay with Vipps ${icon('arrow')}
            </button>
          </div>
          <button class="button secondary" data-action="logout" style="margin-top: 20px;">
            Sign out
          </button>
        </section>
      </div>
    </main>
  `;
}
