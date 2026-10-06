import { addDays, formatDate, escapeHTML as esc } from '../data.js';
import { names, icon, brand, spinner } from '../ui.js';
import { overview } from './overview.js';
import { calendar } from './calendar.js';

export function shell(state, config) {
  const syncDate = state.status?.last_success_at;
  const isAwaitingKey = state.status?.source_connection === 'awaiting_api_key';
  const syncLabel = state.status
    ? isAwaitingKey
      ? 'Connect Intervals.icu'
      : state.status.stale
        ? 'Sync needs attention'
        : 'Synced with Intervals.icu'
    : 'Checking connection';
  const syncTime = syncDate
    ? new Intl.DateTimeFormat('en-GB', {
        timeZone: config.timezone,
        day: 'numeric',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
      }).format(new Date(syncDate))
    : '';
  return /* HTML */ `<div class="app-shell">
    <aside class="sidebar">
      <a href="/dashboard/" class="wordmark" aria-label="Magne training home"
        >${brand}<span>magne<span class="wordmark-dot">.</span></span></a
      >
      <div class="sidebar-kicker">YOUR TRAINING SPACE</div>
      <nav aria-label="Main navigation">
        <button
          data-view="overview"
          class="nav-item ${state.view === 'overview' ? 'active' : ''}"
          ${state.view === 'overview' ? 'aria-current="page"' : ''}
        >
          ${icon('overview')}<span>Overview</span></button
        ><button
          data-view="calendar"
          class="nav-item ${state.view === 'calendar' ? 'active' : ''}"
          ${state.view === 'calendar' ? 'aria-current="page"' : ''}
        >
          ${icon('calendar')}<span>Calendar</span>
        </button>
      </nav>
      <div class="sidebar-bottom">
        <div class="connected-note">
          ${icon('other')}<span>Every effort.<br /><strong>All in one place.</strong></span>
        </div>
        <div class="sidebar-profile">
          <span class="avatar">M</span
          ><span><strong>Magne</strong><small>Personal training space</small></span
          ><button
            class="icon-button signout"
            data-action="logout"
            aria-label="Sign out"
            title="Sign out"
          >
            ${icon('logout')}
          </button>
        </div>
      </div>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <div class="breadcrumb">
          Your training <span>/</span>
          <strong>${state.view === 'overview' ? 'Overview' : 'Calendar'}</strong>
        </div>
        <div class="topbar-right">
          <span
            class="sync-status ${state.status?.stale || isAwaitingKey ? 'warn' : ''}"
            data-action="open-intervals"
            style="cursor: pointer;"
            title="${esc(syncTime ? `Last source sync: ${syncTime} · ${config.timezone}` : 'Configure Intervals.icu sync')}"
            ><span class="status-dot"></span>${syncLabel}</span
          ><button
            class="icon-button"
            data-action="refresh"
            aria-label="Refresh dashboard"
            title="Refresh dashboard"
            ${state.loading ? 'disabled' : ''}
          >
            ${icon('refresh', state.loading ? 'spinning' : '')}</button
          ><span class="avatar mobile-avatar">M</span>
        </div>
      </header>
      <main id="main" class="main-content">
        <div class="page-heading">
          <div>
            <p class="eyebrow">
              ${state.view === 'overview' ? 'THE BIG PICTURE' : 'TRAINING CALENDAR'}
            </p>
            <h1>${state.view === 'overview' ? 'Training overview' : 'Training calendar'}</h1>
            <p class="page-subtitle">
              ${state.view === 'overview' ? 'Perspective on your workouts and fitness.' : 'Review your past and upcoming sessions.'}
            </p>
          </div>
          <div class="period-nav">
            <button
              class="icon-button"
              data-action="previous"
              aria-label="Previous ${state.view === 'calendar' && state.calendarMode === 'week' ? 'week' : 'month'}"
            >
              ${icon('arrow', 'flipped')}</button
            >${state.view === 'calendar' && state.calendarMode === 'week' ? /* HTML */ `<span class="period-title week-period">${formatDate(state.week)} – ${formatDate(addDays(state.week, 6))}</span>` : ''}<label
              class="month-picker ${state.view === 'calendar' && state.calendarMode === 'week' ? 'week-picker' : ''}"
              ><span class="sr-only">Jump to month</span
              ><input
                id="month-picker"
                aria-label="Jump to month"
                type="month"
                min="2000-01"
                max="2099-12"
                value="${state.month.slice(0, 7)}" /></label
            ><button
              class="icon-button"
              data-action="next"
              aria-label="Next ${state.view === 'calendar' && state.calendarMode === 'week' ? 'week' : 'month'}"
            >
              ${icon('arrow')}</button
            ><button class="button today-button" data-action="today">Today</button>
          </div>
        </div>
        <div class="filter-row">
          <div class="sport-filters" role="group" aria-label="Filter by sport">
            ${Object.entries(names)
              .map(
                ([value, name]) =>
                  /* HTML */ `<button
                    class="sport-filter ${state.sport === value ? 'selected' : ''} ${value}"
                    data-sport="${value}"
                    aria-pressed="${state.sport === value}"
                  >
                    ${value !== 'all' ? icon(value) : ''}${name}
                  </button>`,
              )
              .join('')}
          </div>
          <span class="period-note"
            >${state.view === 'overview' ? 'Monthly snapshot' : `<span class="legend-dot run"></span> Completed <span class="legend-planned"></span> Planned`}</span
          >
        </div>
        ${
          state.error
            ? /* HTML */ `<div class="alert" role="alert">
                ${icon('info')}
                <div>
                  <strong>Couldn’t load your training data.</strong>
                  <p>${esc(state.error)}</p>
                </div>
                <button class="button" data-action="${state.authError ? 'login' : 'refresh'}">
                  ${state.authError ? 'Sign in again' : 'Try again'}
                </button>
              </div>`
            : ''
        }
        ${state.loading ? loadingView() : state.error ? '' : state.view === 'overview' ? overview(state) : calendar(state)}
        <footer class="workspace-footer">
          <span>YOUR EFFORT, IN PERSPECTIVE.</span
          ><span
            >Known imported workouts · ${esc(config.timezone)} ·
            ${syncTime ? `Last sync ${esc(syncTime)}` : 'Sync time unavailable'}</span
          >
        </footer>
      </main>
      <button class="coach-fab" data-action="open-chat" aria-label="Åpne AI Coach" title="AI Coach">
        💬
      </button>
    </div>
    ${state.showIntervalsModal ? intervalsModalHTML(state) : ''}
  </div>`;
}

function intervalsModalHTML(state) {
  const isOk = state?.status?.source_connection === 'configured';
  const id = state?.status?.source_athlete_id || state?.status?.athlete_id || 'i714323';
  return /* HTML */ `
    <div class="drawer-backdrop" data-action="close-intervals"></div>
    <div class="drawer intervals-modal">
      <div class="detail-header">
        <h3>Intervals.icu</h3>
        <button class="icon-button" data-action="close-intervals" aria-label="Close">✕</button>
      </div>
      ${
        isOk
          ? /* HTML */ `<p class="modal-hint">
                Tilkoblet <strong>${esc(id)}</strong>. Synkroniseres automatisk.
              </p>
              <div class="modal-fields">
                <button class="button primary" data-action="refresh">Oppdater nå</button>
              </div>`
          : /* HTML */ `<p class="modal-hint">
                Nøkkel og ID fra
                <a href="https://intervals.icu/settings" target="_blank" rel="noopener"
                  >innstillinger</a
                >.
              </p>
              <div class="modal-fields">
                <label
                  >API-nøkkel<input
                    type="password"
                    id="intervals-api-key"
                    class="sport-filter"
                    required
                /></label>
                <label
                  >Utøver-ID<input
                    type="text"
                    id="intervals-athlete-id"
                    placeholder="i12345"
                    class="sport-filter"
                    required
                /></label>
                <p id="intervals-error" class="form-error" style="display:none;"></p>
                <button class="button primary" data-action="save-intervals">
                  Lagre og synkroniser
                </button>
              </div>`
      }
    </div>
  `;
}

function loadingView() {
  return /* HTML */ `<div class="loading-state" role="status">
    ${spinner}
    <h2>Bringing your training together</h2>
    <p>Loading workouts and the weeks around them…</p>
  </div>`;
}
