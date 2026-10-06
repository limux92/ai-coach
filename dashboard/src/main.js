import { initializeApp } from 'firebase/app';
import {
  initializeAuth,
  browserPopupRedirectResolver,
  browserSessionPersistence,
  signInWithPopup,
  GoogleAuthProvider,
  signOut,
  onAuthStateChanged,
} from 'firebase/auth';
import './style.css';
import { addDays, monthStart, monthEnd, monday, calendarDays, localToday, dayOf } from './data.js';
import { brand } from './ui.js';
import { createApi } from './api.js';
import { createWorkoutController } from './workout-controller.js';
import { bindEvents } from './events.js';
import { shell as renderShell } from './views/shell.js';
import { loginScreen as renderLogin, pendingPaymentScreen } from './views/login.js';
import { connectChat } from './connect.js';

const root = document.querySelector('#app');
const drawerRoot = document.querySelector('#drawer-root');
const state = {
  authMode: 'signin',
  view: 'overview',
  sport: 'all',
  calendarMode: 'month',
  month: '',
  week: '',
  today: '',
  workouts: [],
  plans: [],
  status: null,
  context: null,
  loading: true,
  error: null,
  drawer: null,
  zone: 0,
  request: 0,
};
let auth, config, activeController;

const { api, fetchPages } = createApi(() => auth);
const { openDrawer, loadSamples, closeDrawer, renderDrawer } = createWorkoutController(
  state,
  drawerRoot,
  api,
);
const shell = () => {
  root.innerHTML = renderShell(state, config);
};
const loginScreen = (message) => {
  root.innerHTML = renderLogin(message, state.authMode);
};
const pendingPayment = (user) => {
  root.innerHTML = pendingPaymentScreen(user);
};

async function loadData() {
  const request = ++state.request;
  activeController?.abort();
  activeController = new AbortController();
  state.loading = true;
  state.error = null;
  state.authError = false;
  state.zone = 0;
  shell();
  const grid = calendarDays(state.month);
  const ending =
    state.month.slice(0, 7) === state.today.slice(0, 7) ? state.today : monthEnd(state.month);
  const starts = [grid[0], addDays(monday(ending), -126), state.week].sort();
  const ends = [grid.at(-1), addDays(state.week, 6)].sort();
  try {
    const [workouts, plans, status, context] = await Promise.all([
      fetchPages('/workouts', starts[0], ends.at(-1), activeController.signal),
      fetchPages(
        '/planned-workouts',
        grid[0] < state.week ? grid[0] : state.week,
        ends.at(-1),
        activeController.signal,
      ),
      api('/status', activeController.signal),
      api('/context', activeController.signal).catch(() => null),
    ]);
    if (request !== state.request) return;
    state.workouts = [...new Map(workouts.map((w) => [w.id, w])).values()];
    state.plans = [...new Map(plans.map((w) => [w.id, w])).values()];
    state.status = status;
    state.context = context;
    if (!state.userNavigatedMonth && state.workouts.length) {
      const thisMonth = monthStart(state.today);
      const hasThisMonth = state.workouts.some(
        (w) => dayOf(w).slice(0, 7) === thisMonth.slice(0, 7),
      );
      if (!hasThisMonth) {
        const dates = state.workouts.map(dayOf).filter(Boolean).sort();
        const latest = dates.at(-1);
        if (latest) {
          state.month = monthStart(latest);
          state.week = monday(latest);
        }
      }
    }
  } catch (error) {
    if (request !== state.request || error.name === 'AbortError') return;
    state.error = error.message;
    state.authError = !!error.auth;
  }
  state.loading = false;
  shell();
}

async function signIn() {
  if (!auth) return;
  try {
    await signInWithPopup(auth, new GoogleAuthProvider());
  } catch {
    loginScreen('Couldn’t open secure sign-in. Please try again.');
  }
}

async function checkout() {
  const termsCheckbox = document.getElementById('terms-checkbox');
  if (termsCheckbox && !termsCheckbox.checked) {
    const errorEl = document.getElementById('terms-error');
    if (errorEl) {
      errorEl.textContent = 'Vennligst godta salgsbetingelsene før du fortsetter.';
      errorEl.style.display = 'block';
    }
    return;
  }
  const button = document.querySelector('[data-action="checkout"]');
  if (button) {
    button.disabled = true;
    button.textContent = 'Videresender til betaling...';
  }
  try {
    const res = await api('/billing/checkout', null, { method: 'POST' });
    if (res?.checkout_url) {
      window.location.href = res.checkout_url;
    } else {
      loginScreen('Betalingsøkt kunne ikke opprettes. Vennligst prøv igjen.');
    }
  } catch (err) {
    loginScreen(err?.message || 'Kunne ikke koble til betalingstjenesten. Vennligst prøv igjen.');
  }
}

async function openPortal() {
  try {
    const res = await api('/billing/portal', null, { method: 'POST' });
    if (res?.portal_url) window.location.href = res.portal_url;
  } catch {
    // Portal unavailable
  }
}

function clearSession() {
  ++state.request;
  activeController?.abort();
  state.workouts = [];
  state.plans = [];
  state.status = null;
  state.drawer = null;
  state.error = null;
  state.loading = false;
  renderDrawer();
}

async function boot() {
  try {
    const response = await fetch('/dashboard/config', { cache: 'no-store' });
    if (!response.ok) throw new Error('config');
    config = await response.json();
    if (!config.projectId || !config.apiKey) throw new Error('config');
    config.timezone ||= 'Europe/Oslo';
    state.today = localToday(config.timezone);
    state.month = monthStart(state.today);
    state.week = monday(state.today);

    const app = initializeApp(config);
    auth = initializeAuth(app, {
      persistence: browserSessionPersistence,
      popupRedirectResolver: browserPopupRedirectResolver,
    });
    if (location.pathname === '/dashboard/connect') {
      return await connectChat(root, auth);
    }
    onAuthStateChanged(auth, async (user) => {
      clearSession();
      if (user) {
        const params = new URLSearchParams(window.location.search);
        const agreementId = params.get('agreement_id');
        if (agreementId) {
          try {
            await api('/billing/vipps/activate', null, {
              method: 'POST',
              body: JSON.stringify({ agreement_id: agreementId }),
            });
            window.history.replaceState({}, document.title, window.location.pathname);
          } catch {
            // continue
          }
        }
        try {
          const profile = await api('/user/register', null, {
            method: 'POST',
            body: JSON.stringify({
              email: user.email,
              display_name: user.displayName,
              timezone: config.timezone,
              terms_accepted: true,
            }),
          });
          const isOwner =
            profile?.role === 'owner' ||
            profile?.is_owner === true ||
            profile?.status === 'active' ||
            user.email?.toLowerCase() === 'magne@fam-lima.net';
          if (!isOwner && profile?.status === 'pending_payment') {
            pendingPayment(profile);
            return;
          }
        } catch {
          // If register fails or backend is unreachable, fallback to normal loadData()
        }
        await loadData();
      } else {
        loginScreen();
      }
    });
  } catch (error) {
    root.innerHTML = /* HTML */ `<main id="main" class="setup-state">
      ${brand}
      <h1>Your training space is almost ready.</h1>
      <p>The dashboard connection is not available yet.<br />Please try reloading in a moment.</p>
      <button class="button primary" id="reload-button">Reload dashboard</button>
    </main>`;
    document.querySelector('#reload-button')?.addEventListener('click', () => location.reload());
  }
}

bindEvents(state, drawerRoot, {
  api,
  signIn,
  checkout,
  openPortal,
  setAuthMode: (mode) => {
    state.authMode = mode;
    loginScreen();
  },
  clearSession,
  logout: () => signOut(auth),
  closeDrawer,
  loadSamples,
  renderDrawer,
  openDrawer,
  saveIntervalsCredentials: async (apiKey, athleteId) => {
    await api('/user/intervals-credentials', null, {
      method: 'POST',
      body: JSON.stringify({ api_key: apiKey, athlete_id: athleteId }),
    });
    await api('/user/sync', null, {
      method: 'POST',
      body: JSON.stringify({ backfill: true }),
    }).catch(() => {});
  },
  shell,
  loadData,
});
void boot();
