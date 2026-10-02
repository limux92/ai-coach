import { addDays, monthStart, shiftMonth, monday } from './data.js';

export function bindEvents(state, drawerRoot, actions) {
  const {
    signIn,
    clearSession,
    logout,
    closeDrawer,
    loadSamples,
    renderDrawer,
    openDrawer,
    shell,
    loadData,
  } = actions;
  document.addEventListener('click', (event) => {
    const target = event.target.closest('button, [data-action]');
    if (!target) return;
    const action = target.dataset.action;
    if (action === 'login') return void signIn();
    if (action === 'logout') {
      clearSession();
      return void logout();
    }
    if (action === 'close-drawer') return closeDrawer();
    if (action === 'more-samples' && state.drawer)
      return void loadSamples(state.drawer, state.drawer.nextOffset);
    if (target.dataset.sampleField && state.drawer) {
      state.drawer.sampleField = target.dataset.sampleField;
      renderDrawer();
      return;
    }
    if (target.dataset.workout) return void openDrawer(target.dataset.workout);
    if (target.dataset.plan) return void openDrawer(target.dataset.plan, true);
    if (target.dataset.sport) {
      state.sport = target.dataset.sport;
      state.zone = 0;
      shell();
      return;
    }
    if (target.dataset.view) {
      state.view = target.dataset.view;
      shell();
      return;
    }
    if (target.dataset.mode) {
      state.calendarMode = target.dataset.mode;
      shell();
      return;
    }
    if (target.dataset.day) {
      state.week = monday(target.dataset.day);
      state.month = monthStart(target.dataset.day);
      state.view = 'calendar';
      state.calendarMode = 'week';
      return void loadData();
    }
    if (action === 'refresh') return void loadData();
    if (action === 'today') {
      state.month = monthStart(state.today);
      state.week = monday(state.today);
      return void loadData();
    }
    if (action === 'previous' || action === 'next') {
      const delta = action === 'previous' ? -1 : 1;
      if (state.view === 'calendar' && state.calendarMode === 'week') {
        state.week = addDays(state.week, delta * 7);
        state.month = monthStart(state.week);
      } else {
        state.month = shiftMonth(state.month, delta);
        state.week = monday(state.month);
      }
      void loadData();
    }
  });
  document.addEventListener('change', (event) => {
    if (event.target.id === 'month-picker') {
      if (/^\d{4}-\d{2}$/.test(event.target.value) && event.target.validity.valid) {
        state.month = `${event.target.value}-01`;
        state.week = monday(state.month);
        void loadData();
      }
      return;
    }
    if (event.target.id === 'zone-select') {
      state.zone = Number(event.target.value);
      if (state.drawer) renderDrawer();
      else shell();
    }
  });
  document.addEventListener('keydown', (event) => {
    if (!state.drawer) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      closeDrawer();
    }
    if (event.key === 'Tab') {
      const focusable = [
        ...drawerRoot.querySelectorAll('button:not([disabled]), select, [tabindex="0"], a[href]'),
      ];
      const first = focusable[0],
        last = focusable.at(-1);
      if (
        event.shiftKey &&
        (document.activeElement === first || !drawerRoot.contains(document.activeElement))
      ) {
        event.preventDefault();
        last?.focus();
      } else if (
        !event.shiftKey &&
        (document.activeElement === last || !drawerRoot.contains(document.activeElement))
      ) {
        event.preventDefault();
        first?.focus();
      }
    }
  });
}
