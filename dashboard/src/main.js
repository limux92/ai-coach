import { initializeApp } from 'firebase/app';
import {
  getAuth,
  setPersistence,
  browserSessionPersistence,
  signInWithPopup,
  GoogleAuthProvider,
  signOut,
  onAuthStateChanged,
} from 'firebase/auth';
import './style.css';
import { addDays, monthStart, monthEnd, monday, calendarDays, localToday } from './data.js';
import { brand } from './ui.js';
import { createApi } from './api.js';
import { createWorkoutController } from './workout-controller.js';
import { bindEvents } from './events.js';
import { shell as renderShell } from './views/shell.js';
import { loginScreen as renderLogin } from './views/login.js';

const root = document.querySelector('#app');
const drawerRoot = document.querySelector('#drawer-root');
const state = {
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
  root.innerHTML = renderLogin(message);
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
    auth = getAuth(app);

    await setPersistence(auth, browserSessionPersistence);
    if (location.pathname === '/dashboard/connect') {
      const { connectChat } = await import('./connect.js');
      return await connectChat(root, auth);
    }
    onAuthStateChanged(auth, async (user) => {
      clearSession();
      if (user) await loadData();
      else loginScreen();
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
  signIn,
  clearSession,
  logout: () => signOut(auth),
  closeDrawer,
  loadSamples,
  renderDrawer,
  openDrawer,
  shell,
  loadData,
});
void boot();
