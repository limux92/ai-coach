import { createAuth0Client } from '@auth0/auth0-spa-js';
import './style.css';
import { isNumber, addDays, monthStart, monthEnd, shiftMonth, monday, calendarDays, localToday, category, dayOf, loadOf, totals, calendarPeriodRows, zoneGroups, weekSeries, duration, km, formatDate, number, escapeHTML as esc } from './data.js';

const root = document.querySelector('#app');
const drawerRoot = document.querySelector('#drawer-root');
const state = { view: 'overview', sport: 'all', calendarMode: 'month', month: '', week: '', today: '', workouts: [], plans: [], status: null, loading: true, error: null, drawer: null, zone: 0, request: 0 };
let auth, config, focusBeforeDrawer, activeController;
const names = { run: 'Running', cycle: 'Cycling', other: 'Other sports', all: 'All activities' };
const paths = {
  overview: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M16 3v4M8 3v4M3 11h18M8 15h2M14 15h2"/>',
  run: '<circle cx="14" cy="4" r="2"/><path d="m7 10 4-3 4 4 4 1M11 7l-2 7 4 3-2 5M9 14l-3 5H2"/>',
  cycle: '<circle cx="5" cy="16" r="4"/><circle cx="19" cy="16" r="4"/><path d="m5 16 5-9 5 9H5l-2-9h4M10 7h5l4 9M15 4h3"/>',
  other: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  arrow: '<path d="m9 5 7 7-7 7"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 11.5-2L20 8M4 16l2.5 3A7 7 0 0 0 18 17"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  distance: '<path d="M5 4h6v6H5zM13 14h6v6h-6zM8 10v5h5M11 7h5v7"/>',
  load: '<path d="m13 2-9 12h7l-1 8 10-12h-7l1-8Z"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  logout: '<path d="M9 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4M14 8l4 4-4 4M8 12h10"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/>',
  heart: '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
  mountain: '<path d="m2 20 8-16 5 10 3-6 4 12H2ZM7 10l3 2 3-2"/>',
};
const icon = (name, extra = '') => `<svg class="icon ${extra}" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">${paths[name] || paths.other}</svg>`;
const brand = `<span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 40 40" fill="none"><path d="M8 29V12l12 11 12-11v17" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg></span>`;
const selectedRows = rows => state.sport === 'all' ? rows : rows.filter(row => category(row) === state.sport);
const inWindow = (rows, start, end) => rows.filter(row => dayOf(row) >= start && dayOf(row) <= end);
const monthRows = () => inWindow(selectedRows(state.workouts), monthStart(state.month), monthEnd(state.month));
const prettyMonth = () => formatDate(state.month, { day: undefined, month: 'long', year: 'numeric' });
const datum = (value, suffix = '') => isNumber(value) ? `${number(value)}${suffix}` : '—';
const spinner = '<span class="spinner" aria-hidden="true"></span>';

function loginScreen(message = '') {
  root.innerHTML = `<main id="main" class="login-page"><div class="login-wordmark">${brand}<span>magne<span class="wordmark-dot">.</span></span><small>TRAINING SPACE</small></div>
    <div class="login-layout"><section class="login-copy"><div class="eyebrow"><span class="status-dot"></span> A CLEARER PICTURE OF YOUR TRAINING</div><h1>Every session.<br>One connected<br><em>training story.</em></h1><p>Your workouts, your progress, your next chapter. A private space to see the work you put in.</p>
      <button class="button primary login-button" data-action="login">Open my training space ${icon('arrow')}</button><div class="login-security">${icon('lock')} Private to you · Secure sign-in</div>${message ? `<p class="login-error" role="alert">${esc(message)}</p>` : ''}</section>
    <div class="login-art" aria-hidden="true"><div class="art-grid"></div><div class="art-orbit orbit-one"></div><div class="art-orbit orbit-two"></div><div class="art-orbit orbit-three"></div><div class="art-line"></div><span class="art-label">CONSISTENCY BUILDS POSSIBILITY.</span><div class="art-card"><span class="art-card-icon">${icon('run')}</span><span>Your next chapter<br><strong>Starts with showing up.</strong></span></div><div class="art-pill">${icon('check')} Every effort counts</div></div></div><footer class="login-footer">MAGNE TRAINING DATABASE<span>Garmin & Zwift · Connected through Intervals.icu</span></footer></main>`;
}
function shell() {
  const syncDate = state.status?.last_success_at;
  const syncLabel = state.status ? state.status.stale ? 'Sync needs attention' : 'Synced with Intervals.icu' : 'Checking connection';
  const syncTime = syncDate ? new Intl.DateTimeFormat('en-GB', { timeZone: config.timezone, day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(syncDate)) : '';
  root.innerHTML = `<div class="app-shell"><aside class="sidebar"><a href="/dashboard/" class="wordmark" aria-label="Magne training home">${brand}<span>magne<span class="wordmark-dot">.</span></span></a><div class="sidebar-kicker">YOUR TRAINING SPACE</div><nav aria-label="Main navigation"><button data-view="overview" class="nav-item ${state.view === 'overview' ? 'active' : ''}" ${state.view === 'overview' ? 'aria-current="page"' : ''}>${icon('overview')}<span>Overview</span></button><button data-view="calendar" class="nav-item ${state.view === 'calendar' ? 'active' : ''}" ${state.view === 'calendar' ? 'aria-current="page"' : ''}>${icon('calendar')}<span>Calendar</span></button></nav><div class="sidebar-bottom"><div class="connected-note">${icon('other')}<span>Every effort.<br><strong>All in one place.</strong></span></div><div class="sidebar-profile"><span class="avatar">M</span><span><strong>Magne</strong><small>Personal training space</small></span><button class="icon-button signout" data-action="logout" aria-label="Sign out" title="Sign out">${icon('logout')}</button></div></div></aside>
    <div class="workspace"><header class="topbar"><div class="breadcrumb">Your training <span>/</span> <strong>${state.view === 'overview' ? 'Overview' : 'Calendar'}</strong></div><div class="topbar-right"><span class="sync-status ${state.status?.stale ? 'warn' : ''}" title="${esc(syncTime ? `Last source sync: ${syncTime} · ${config.timezone}` : 'Checking source sync status')}"><span class="status-dot"></span>${syncLabel}</span><button class="icon-button" data-action="refresh" aria-label="Refresh dashboard" title="Refresh dashboard" ${state.loading ? 'disabled' : ''}>${icon('refresh', state.loading ? 'spinning' : '')}</button><span class="avatar mobile-avatar">M</span></div></header>
    <main id="main" class="main-content"><div class="page-heading"><div><p class="eyebrow">${state.view === 'overview' ? 'THE BIG PICTURE' : 'MAKE EVERY WEEK COUNT'}</p><h1>${state.view === 'overview' ? 'Your training, in focus.' : 'Your training calendar.'}</h1><p class="page-subtitle">${state.view === 'overview' ? 'A little perspective on all the work you’ve put in.' : 'The sessions behind you. The possibilities ahead.'}</p></div><div class="period-nav"><button class="icon-button" data-action="previous" aria-label="Previous ${state.view === 'calendar' && state.calendarMode === 'week' ? 'week' : 'month'}">${icon('arrow', 'flipped')}</button>${state.view === 'calendar' && state.calendarMode === 'week' ? `<span class="period-title week-period">${formatDate(state.week)} – ${formatDate(addDays(state.week, 6))}</span>` : ''}<label class="month-picker ${state.view === 'calendar' && state.calendarMode === 'week' ? 'week-picker' : ''}"><span class="sr-only">Jump to month</span><input id="month-picker" aria-label="Jump to month" type="month" min="2000-01" max="2099-12" value="${state.month.slice(0, 7)}"></label><button class="icon-button" data-action="next" aria-label="Next ${state.view === 'calendar' && state.calendarMode === 'week' ? 'week' : 'month'}">${icon('arrow')}</button><button class="button today-button" data-action="today">Today</button></div></div>
    <div class="filter-row"><div class="sport-filters" role="group" aria-label="Filter by sport">${Object.entries(names).map(([value, name]) => `<button class="sport-filter ${state.sport === value ? 'selected' : ''} ${value}" data-sport="${value}" aria-pressed="${state.sport === value}">${value !== 'all' ? icon(value) : ''}${name}</button>`).join('')}</div><span class="period-note">${state.view === 'overview' ? 'Monthly snapshot' : `<span class="legend-dot run"></span> Completed <span class="legend-planned"></span> Planned`}</span></div>
    ${state.error ? `<div class="alert" role="alert">${icon('info')}<div><strong>Couldn’t load your training data.</strong><p>${esc(state.error)}</p></div><button class="button" data-action="${state.authError ? 'login' : 'refresh'}">${state.authError ? 'Sign in again' : 'Try again'}</button></div>` : ''}
    ${state.loading ? loadingView() : state.error ? '' : state.view === 'overview' ? overview() : calendar()}
    <footer class="workspace-footer"><span>YOUR EFFORT, IN PERSPECTIVE.</span><span>Known imported workouts · ${esc(config.timezone)} · ${syncTime ? `Last sync ${esc(syncTime)}` : 'Sync time unavailable'}</span></footer></main></div></div>`;
}
function loadingView() { return `<div class="loading-state" role="status">${spinner}<h2>Bringing your training together</h2><p>Loading workouts and the weeks around them…</p></div>`; }
function metricCard(label, value, unit, note, symbol, tone = '') { return `<article class="metric-card ${tone}"><div class="metric-top"><span>${label}</span>${icon(symbol)}</div><div class="metric-value">${value}<small>${unit}</small></div><div class="metric-foot">${note}</div></article>`; }
function overview() {
  const rows = monthRows(), summary = totals(rows);
  const coverage = (key, noun) => summary[`${key}Count`] < rows.length ? `${summary[`${key}Count`]} of ${rows.length} sessions with ${noun}` : noun;
  const mixedDistance = rows.some(r => r.distance_type === 'virtual' || String(r.sport).includes('Virtual'));
  return `<section class="metric-grid" aria-label="Monthly training totals">${metricCard('Training time', duration(summary.duration), '', coverage('duration', 'Moving time across your sessions'), 'clock', 'featured')}${metricCard('Distance', km(summary.distance), 'km', coverage('distance', mixedDistance ? 'Includes virtual distance' : 'Distance covered this month'), 'distance')}${metricCard('Completed sessions', number(summary.count), '', `${summary.activeDays} active ${summary.activeDays === 1 ? 'day' : 'days'} this month`, 'check')}${metricCard('Training load', number(summary.load), '', coverage('load', 'Provider-estimated load'), 'load')}</section>
  <div class="charts-grid"><section class="panel trend-panel"><div class="panel-heading"><div><h2>Consistency over time</h2><p>Your training volume across 12 weeks</p></div><span class="quiet-badge">Hours / week</span></div>${trendChart()}<div class="chart-legend">${['run', 'cycle', 'other'].filter(x => state.sport === 'all' || state.sport === x).map(x => `<span><i class="legend-dot ${x}"></i>${names[x]}</span>`).join('')}</div></section>
    <section class="panel zones-panel"><div class="panel-heading"><div><h2>Time in heart-rate zones</h2><p>How your effort was distributed</p></div>${icon('heart')}</div>${zones(rows)}</section></div>
  <div class="bottom-grid"><section class="panel recent-panel"><div class="panel-heading"><div><h2>Recent sessions</h2><p>${esc(prettyMonth())} · ${rows.length} completed ${rows.length === 1 ? 'workout' : 'workouts'}</p></div><button class="text-button" data-view="calendar">View calendar ${icon('arrow')}</button></div>${recentTable(rows)}</section>
  <section class="panel month-panel"><div class="panel-heading"><div><h2>A month of movement</h2><p>${esc(prettyMonth())}</p></div>${icon('calendar')}</div>${miniCalendar(rows)}<div class="month-insight"><strong>${summary.activeDays}</strong><span>days you showed up<br><small>Every session is part of the story.</small></span></div></section></div>`;
}
function trendChart() {
  const ending = state.month.slice(0, 7) === state.today.slice(0, 7) ? state.today : monthEnd(state.month);
  const weeks = weekSeries(selectedRows(state.workouts), ending);
  const max = Math.max(...weeks.map(w => w.run + w.cycle + w.other), 3600);
  const ceiling = Math.ceil(max / 3600 / 4) * 4;
  return `<div class="trend-chart" role="img" aria-label="Weekly training hours for the twelve weeks ending ${esc(formatDate(ending))}"><div class="chart-scale">${[1, .75, .5, .25, 0].map(n => `<span>${number(ceiling * n)}<i></i></span>`).join('')}</div><div class="chart-columns">${weeks.map((week, i) => {
    const total = week.run + week.cycle + week.other;
    const timeLabel = week.count && !week.durationCount ? 'duration unknown' : `${duration(total)}${week.durationCount < week.count ? ' (partial time coverage)' : ''}`;
    return `<div class="chart-column"><div class="bar-space"><div class="stacked-bar ${i === 11 ? 'current' : ''}" tabindex="0" aria-label="Week of ${esc(formatDate(week.start))}: ${timeLabel}, ${week.count} sessions" title="${esc(formatDate(week.start))}–${esc(formatDate(week.end))}: ${timeLabel} · ${week.count} sessions">${['other', 'cycle', 'run'].map(type => `<span class="bar-part ${type}" style="height:${(week[type] / 3600 / ceiling * 176).toFixed(2)}px"></span>`).join('')}</div></div><span class="bar-label">${i % 2 === 0 || i === 11 ? formatDate(week.start) : ' '}</span></div>`;
  }).join('')}</div></div>${weeks.some(w => w.durationCount < w.count) ? '<p class="chart-coverage">Some sessions have no moving time. Bars show only recorded durations.</p>' : ''}`;
}
function zones(rows) {
  const groups = zoneGroups(rows);
  if (!groups.length) return `<div class="empty-state compact">${icon('heart')}<h3>No zone data in this view</h3><p>Heart-rate zones appear when supplied with an imported workout.</p></div>`;
  state.zone = Math.min(state.zone, groups.length - 1);
  const group = groups[state.zone];
  const select = groups.length > 1 ? `<label class="zone-select-label">Zone definition<select id="zone-select" aria-label="Heart-rate zone definition">${groups.map((g, i) => `<option value="${i}" ${state.zone === i ? 'selected' : ''}>${names[g.sport]} · ${g.boundaries.join(' / ')} bpm · ${g.count} sessions</option>`).join('')}</select></label>` : `<div class="zone-context"><span class="sport-dot ${group.sport}"></span>${names[group.sport]} · ${group.count} ${group.count === 1 ? 'session' : 'sessions'}</div>`;
  return `${select}<div class="zone-bars">${group.seconds.map((seconds, i) => `<div class="zone-row"><span class="zone-label">Z${i + 1}</span><span class="zone-range">${i === 0 ? '≤ ' : `${group.boundaries[i - 1] + 1}–`}${group.boundaries[i]}</span><div class="zone-track"><span class="zone-fill zone-${Math.min(i, 6)}" style="width:${group.total ? (seconds / group.total * 100).toFixed(2) : 0}%"></span></div><strong>${duration(seconds, true)}</strong></div>`).join('')}</div><p class="chart-caption">BPM boundaries · ${duration(group.total)} classified.<br>${groups.length > 1 ? 'Different zone definitions are shown separately.' : 'Percentages use classified time; HR coverage may be incomplete.'}</p>`;
}
function recentTable(rows) {
  const items = [...rows].sort((a, b) => String(b.start_date_local || dayOf(b)).localeCompare(String(a.start_date_local || dayOf(a)))).slice(0, 6);
  if (!items.length) return `<div class="empty-state">${icon('calendar')}<h3>A little room on the calendar</h3><p>No imported workouts match this month and sport.<br>Explore another month to see your training.</p></div>`;
  return `<div class="table-scroll"><table class="workout-table"><thead><tr><th>Session</th><th>Date</th><th>Time</th><th>Distance</th><th>Load</th><th><span class="sr-only">Details</span></th></tr></thead><tbody>${items.map(row => { const type = category(row); return `<tr><td><button class="workout-name" data-workout="${esc(row.id)}"><span class="sport-icon ${type}">${icon(type)}</span><span><strong>${esc(row.name || names[type])}</strong><small>${esc(row.recording_platform === 'zwift' ? 'Zwift · virtual' : row.sport || names[type])}</small></span></button></td><td>${formatDate(dayOf(row))}</td><td>${duration(row.metrics?.moving_time_s, true)}</td><td>${km(row.metrics?.distance_m)} <small>km</small></td><td>${datum(loadOf(row))}</td><td><button class="icon-button small" data-workout="${esc(row.id)}" aria-label="View ${esc(row.name || 'workout')}">${icon('arrow')}</button></td></tr>`; }).join('')}</tbody></table></div>`;
}
function miniCalendar(rows) {
  const dates = new Map();
  for (const row of rows) { if (!dates.has(dayOf(row))) dates.set(dayOf(row), []); dates.get(dayOf(row)).push(row); }
  return `<div class="mini-calendar"><div class="mini-weekdays">${['M', 'T', 'W', 'T', 'F', 'S', 'S'].map(d => `<span>${d}</span>`).join('')}</div><div class="mini-days">${calendarDays(state.month).map(day => `<button class="mini-day ${day.slice(0, 7) !== state.month.slice(0, 7) ? 'outside' : ''} ${dates.has(day) ? 'has-session' : ''} ${day === state.today ? 'is-today' : ''}" data-day="${day}" aria-label="${esc(formatDate(day, { year: 'numeric' }))}, ${dates.get(day)?.length || 0} workouts"><span>${Number(day.slice(8))}</span><i class="mini-dot ${dates.has(day) ? category(dates.get(day)[0]) : ''}"></i></button>`).join('')}</div></div>`;
}
function calendar() {
  const days = state.calendarMode === 'week' ? Array.from({ length: 7 }, (_, i) => addDays(state.week, i)) : calendarDays(state.month);
  const rows = selectedRows(state.workouts), plans = selectedRows(state.plans);
  const weeks = Array.from({ length: days.length / 7 }, (_, i) => days.slice(i * 7, i * 7 + 7));
  const visibleRows = calendarPeriodRows(rows, { mode: state.calendarMode, month: state.month, week: state.week });
  const total = totals(visibleRows);
  const virtualDistance = visibleRows.some(r => r.distance_type === 'virtual' || String(r.sport).includes('Virtual'));
  return `<section class="panel calendar-panel"><div class="calendar-toolbar"><div class="calendar-summary"><b class="calendar-total-label">${state.calendarMode === 'month' ? 'Month total' : 'Week total'}</b><strong>${total.count}</strong> sessions <span>·</span> <strong>${duration(total.duration)}</strong> <span>·</span> <strong>${km(total.distance)}</strong> km</div><div class="segmented" role="group" aria-label="Calendar layout"><button data-mode="month" class="${state.calendarMode === 'month' ? 'selected' : ''}" aria-pressed="${state.calendarMode === 'month'}">Month</button><button data-mode="week" class="${state.calendarMode === 'week' ? 'selected' : ''}" aria-pressed="${state.calendarMode === 'week'}">Week</button></div></div>
  <div class="calendar-scroll"><div class="calendar-grid ${state.calendarMode}"><div class="calendar-weekdays">${['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(d => `<div>${d}</div>`).join('')}<div class="week-total-heading">Weekly total</div></div>${weeks.map(week => calendarWeek(week, rows, plans)).join('')}</div></div><div class="calendar-footnote">${icon('info')} <span>${state.calendarMode === 'month' ? 'Weekly totals include complete Monday–Sunday weeks, including adjacent months. ' : ''}${virtualDistance ? 'Totals include virtual distance. ' : ''}${total.durationCount < total.count || total.distanceCount < total.count ? 'Some sessions have missing metrics; totals include available values. ' : ''}Blank days mean no imported or planned workouts in this view. They don’t confirm rest days.</span></div></section>`;
}
function calendarWeek(days, rows, plans) {
  const weekRows = inWindow(rows, days[0], days[6]);
  const total = totals(weekRows);
  return `<div class="calendar-week">${days.map(day => {
    const done = rows.filter(row => dayOf(row) === day), upcoming = plans.filter(row => dayOf(row) === day && !['completed', 'cancelled', 'canceled', 'deleted', 'skipped'].includes(String(row.status).toLowerCase()));
    return `<section class="calendar-day ${day.slice(0, 7) !== state.month.slice(0, 7) ? 'outside' : ''} ${day === state.today ? 'today' : ''}" aria-label="${esc(formatDate(day, { weekday: 'long', year: 'numeric' }))}"><div class="day-heading"><span class="day-number">${Number(day.slice(8))}</span>${day === state.today ? '<span class="today-label">TODAY</span>' : Number(day.slice(8)) === 1 ? `<span class="day-month">${formatDate(day, { day: undefined })}</span>` : ''}</div><div class="day-sessions">${done.map(r => calendarCard(r, false)).join('')}${upcoming.map(r => calendarCard(r, true)).join('')}${!done.length && !upcoming.length ? '<span class="no-session" aria-label="No workouts">—</span>' : ''}</div></section>`;
  }).join('')}<aside class="week-total"><span class="week-label">${formatDate(days[0])}</span><strong>${duration(total.duration, true)}</strong><span>${km(total.distance)} km</span><span>${total.count} sessions</span><div class="week-load">${icon('load')} ${datum(total.load)} <small>load</small></div></aside></div>`;
}
function calendarCard(row, planned) {
  const type = category(row), m = row.metrics || {}, seconds = planned ? m.duration_s : m.moving_time_s;
  return `<button class="calendar-card ${type} ${planned ? 'planned' : ''}" ${planned ? `data-plan="${esc(row.id)}"` : `data-workout="${esc(row.id)}"`} aria-label="${planned ? 'Planned' : 'Completed'} ${esc(row.name || names[type])}, ${duration(seconds)}"><span class="card-sport">${icon(type)}<strong>${duration(seconds, true)}</strong>${planned ? '<span class="planned-tag">PLAN</span>' : icon('check', 'card-check')}</span><span class="card-name">${esc(row.name || names[type])}</span><span class="card-metrics">${isNumber(m.distance_m) ? `${km(m.distance_m)} km` : names[type]}${!planned && isNumber(m.average_heart_rate_bpm) ? ` <i>·</i> ${number(m.average_heart_rate_bpm)} bpm` : ''}</span>${isNumber(planned ? m.training_load : loadOf(row)) ? `<span class="card-load">Load ${number(planned ? m.training_load : loadOf(row))}</span>` : ''}</button>`;
}

async function api(path, signal) {
  let token;
  try { token = await auth.getTokenSilently(); } catch { const e = new Error('Your sign-in has expired. Sign in again to reconnect.'); e.auth = true; throw e; }
  const response = await fetch(`/dashboard/api${path}`, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store', signal });
  if (!response.ok) {
    const messages = { 401: 'Your sign-in has expired. Sign in again to reconnect.', 403: 'This account does not have access to this private training space.', 409: 'This workout’s summary is available, but detailed samples are not available. Its original FIT file is preserved.', 429: 'The service is busy. Please try again shortly.', 502: 'The training database is temporarily unavailable. Your saved data is safe.' };
    const error = new Error(messages[response.status] || 'The training service could not complete this request. Please try again.');
    error.status = response.status; error.auth = response.status === 401; throw error;
  }
  return response.json();
}
async function fetchPages(path, oldest, newest, signal) {
  const rows = [], cursors = new Set(); let cursor;
  for (let page = 0; page < 100; page++) {
    const params = new URLSearchParams({ oldest, newest, limit: '50' });
    if (cursor) params.set('after', cursor);
    const result = await api(`${path}?${params}`, signal);
    if (!Array.isArray(result.items)) throw new Error('The training service returned an unexpected response. Please retry.');
    rows.push(...result.items);
    if (!result.next_cursor) return rows;
    if (cursors.has(result.next_cursor)) throw new Error('The workout list could not finish loading. Please retry.');
    cursor = result.next_cursor; cursors.add(cursor);
  }
  throw new Error('This date range contains too many workouts. Please choose a smaller view.');
}
async function loadData() {
  const request = ++state.request;
  activeController?.abort(); activeController = new AbortController();
  state.loading = true; state.error = null; state.authError = false; state.zone = 0; shell();
  const grid = calendarDays(state.month);
  const ending = state.month.slice(0, 7) === state.today.slice(0, 7) ? state.today : monthEnd(state.month);
  const starts = [grid[0], addDays(monday(ending), -77), state.week].sort();
  const ends = [grid.at(-1), addDays(state.week, 6)].sort();
  try {
    const [workouts, plans, status] = await Promise.all([
      fetchPages('/workouts', starts[0], ends.at(-1), activeController.signal),
      fetchPages('/planned-workouts', grid[0] < state.week ? grid[0] : state.week, ends.at(-1), activeController.signal),
      api('/status', activeController.signal),
    ]);
    if (request !== state.request) return;
    state.workouts = [...new Map(workouts.map(w => [w.id, w])).values()];
    state.plans = [...new Map(plans.map(w => [w.id, w])).values()]; state.status = status;
  } catch (error) { if (request !== state.request || error.name === 'AbortError') return; state.error = error.message; state.authError = !!error.auth; }
  state.loading = false; shell();
}

function drawerMetric(label, value, unit = '') { return `<div class="detail-metric"><span>${label}</span><strong>${value}<small>${unit}</small></strong></div>`; }
function drawerShell(row, content) {
  const type = category(row);
  return `<div class="drawer-backdrop" data-action="close-drawer"></div><section class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title" tabindex="-1"><header class="drawer-header"><span class="drawer-kind"><i class="sport-icon ${type}">${icon(type)}</i>${state.drawer.planned ? 'PLANNED SESSION' : 'COMPLETED SESSION'}</span><button class="icon-button" data-action="close-drawer" aria-label="Close workout details">${icon('close')}</button></header><div class="drawer-content"><p class="eyebrow">${esc(formatDate(dayOf(row), { weekday: 'long', year: 'numeric' }))}</p><h2 id="drawer-title">${esc(row.name || names[type])}</h2><p class="drawer-subtitle">${esc(row.sport || names[type])}${row.recording_platform === 'zwift' ? ' · Zwift virtual activity' : ''}</p>${content}</div></section>`;
}
function renderDrawer() {
  if (!state.drawer) { drawerRoot.innerHTML = ''; document.body.classList.remove('drawer-open'); return; }
  const focused = drawerRoot.contains(document.activeElement) ? document.activeElement : null;
  const focusedKey = focused?.dataset.action ? `[data-action="${focused.dataset.action}"]` : focused?.dataset.sampleField ? `[data-sample-field="${focused.dataset.sampleField}"]` : focused?.id === 'zone-select' ? '#zone-select' : null;
  const d = state.drawer, row = d.row, m = row.metrics || {}, planned = d.planned;
  let content = `<div class="detail-metrics">${drawerMetric('Duration', duration(planned ? m.duration_s : m.moving_time_s))}${drawerMetric('Distance', km(m.distance_m), 'km')}${drawerMetric('Training load', datum(planned ? m.training_load : loadOf(row)))}${drawerMetric('Average heart rate', datum(m.average_heart_rate_bpm), 'bpm')}${drawerMetric('Average power', datum(m.average_power_w), 'W')}${drawerMetric('Elevation gain', datum(m.elevation_gain_m), 'm')}</div>`;
  if (row.distance_type === 'virtual') content += '<p class="detail-note">Distance and elevation describe a virtual route.</p>';
  if (planned) {
    content += `<div class="detail-section"><h3>Session notes</h3><p class="plan-description">${esc(row.description || 'Session notes are not included in this calendar view.')}</p><p class="detail-note">Status: ${esc(row.status || 'planned')}</p></div>`;
  } else {
    if (d.loading) content += `<div class="detail-loading" role="status">${spinner} Loading workout details…</div>`;
    if (d.error) content += `<div class="inline-error" role="alert">${icon('info')}${esc(d.error)}</div>`;
    content += `<div class="detail-section"><div class="section-heading"><h3>Session profile</h3><div class="segmented"><button data-sample-field="heart_rate" class="${d.sampleField === 'heart_rate' ? 'selected' : ''}" aria-pressed="${d.sampleField === 'heart_rate'}">Heart rate</button><button data-sample-field="power" class="${d.sampleField === 'power' ? 'selected' : ''}" aria-pressed="${d.sampleField === 'power'}">Power</button></div></div>${sampleChart(d)}</div>`;
    const hr = row.zone_summary?.heart_rate || row.heart_rate_zones;
    if (hr?.status === 'available') content += `<div class="detail-section"><h3>Heart-rate zones</h3>${zones([row])}</div>`;
    const laps = row.laps_summary;
    if (Array.isArray(laps) && laps.length) content += `<div class="detail-section"><h3>Laps</h3><p class="detail-note">${number(row.lap_count ?? laps.length)} laps recorded${row.laps_summary_truncated ? ` · ${number(row.laps_omitted)} omitted from the compact view` : ''}.</p></div>`;
    if (row.description) content += `<div class="detail-section"><h3>Session notes</h3><p class="plan-description">${esc(row.description)}</p>${row.description_truncated ? '<p class="detail-note">Description shortened in this view.</p>' : ''}</div>`;
    if (row.observations && (isNumber(row.observations.rpe) || isNumber(row.observations.feel))) content += `<div class="detail-section"><h3>How it felt</h3><div class="detail-metrics compact-metrics">${drawerMetric('Perceived effort', datum(row.observations.rpe), '/ 10')}${drawerMetric('Feeling', datum(row.observations.feel))}</div></div>`;
  }
  content += `<div class="provenance"><span>${icon('lock')} YOUR TRAINING RECORD</span><dl><dt>Source</dt><dd>${esc(row.source_attribution || row.garmin_attribution || (row.provider_source === 'UPLOAD' ? 'FIT upload through Intervals.icu' : 'Intervals.icu'))}</dd><dt>Activity ID</dt><dd>${esc(row.source_id || row.id)}</dd>${!planned ? `<dt>Detail availability</dt><dd>${row.parse_status === 'summary_only' ? 'Summary available · original FIT archived' : esc(row.sample_availability || row.parse_status || 'Available fields shown above')}</dd>` : ''}</dl></div>`;
  drawerRoot.innerHTML = drawerShell(row, content); document.body.classList.add('drawer-open');
  if (focusedKey) drawerRoot.querySelector(`button${focusedKey}, select${focusedKey}`)?.focus({ preventScroll: true });
}
function sampleChart(d) {
  if (d.samplesLoading && !d.samples.length) return `<div class="sample-empty" role="status">${spinner}<p>Loading the session profile…</p></div>`;
  if (d.sampleError) return `<div class="sample-empty">${icon('info')}<p>${esc(d.sampleError)}</p></div>`;
  const field = d.sampleField, points = d.samples.map((s, i) => ({ x: i, value: s[field], timestamp: s.timestamp }));
  const valid = points.filter(p => isNumber(p.value));
  if (!valid.length) return `<div class="sample-empty">${icon(field === 'heart_rate' ? 'heart' : 'load')}<p>No ${field === 'heart_rate' ? 'heart-rate' : 'power'} samples in this portion of the workout.</p>${samplePaging(d)}</div>`;
  const min = Math.max(0, Math.floor((valid.reduce((n, p) => Math.min(n, p.value), Infinity) - 10) / 10) * 10), max = Math.max(min + 20, Math.ceil((valid.reduce((n, p) => Math.max(n, p.value), -Infinity) + 5) / 10) * 10);
  const t0 = Date.parse(points[0]?.timestamp), t1 = Date.parse(points.at(-1)?.timestamp);
  const useTime = Number.isFinite(t0) && Number.isFinite(t1) && t1 > t0;
  const x = p => 40 + (useTime && Number.isFinite(Date.parse(p.timestamp)) ? (Date.parse(p.timestamp) - t0) / (t1 - t0) : p.x / Math.max(points.length - 1, 1)) * 450;
  let previous = false;
  // Bound SVG size without modifying the stored samples. Missing measurements still break the line.
  const stride = Math.max(1, Math.ceil(points.length / 700));
  const path = points.map((p, i) => { if (!isNumber(p.value)) { previous = false; return ''; } if (i % stride !== 0 && i !== points.length - 1) return ''; const output = `${previous ? 'L' : 'M'}${x(p).toFixed(1)},${(160 - (p.value - min) / (max - min) * 140).toFixed(1)}`; previous = true; return output; }).join(' ');
  const unit = field === 'heart_rate' ? 'bpm' : 'W';
  return `<div class="sample-chart"><svg viewBox="0 0 510 195" role="img" aria-label="${field === 'heart_rate' ? 'Heart rate' : 'Power'} for ${d.samples.length} loaded samples, ${min} to ${max} ${unit}">${[0, .5, 1].map(n => `<line x1="40" x2="490" y1="${160 - n * 140}" y2="${160 - n * 140}" class="sample-grid"/><text x="30" y="${164 - n * 140}" text-anchor="end">${number(min + n * (max - min))}</text>`).join('')}<path d="${path}" class="sample-line ${field}"/><text x="40" y="187">${useTime ? '0 min' : 'First sample'}</text><text x="490" y="187" text-anchor="end">${useTime ? duration((t1 - t0) / 1000) : `${d.samples.length} samples`}</text></svg></div><p class="chart-caption">${unit} · ${number(d.samples.length)} of ${number(d.sampleTotal)} samples loaded${stride > 1 ? ' · downsampled preview' : ''}${d.nextOffset != null ? ' · partial session' : ''}</p>${samplePaging(d)}`;
}
function samplePaging(d) { return d.nextOffset != null ? `<button class="button load-samples" data-action="more-samples" ${d.samplesLoading ? 'disabled' : ''}>${d.samplesLoading ? `${spinner} Loading…` : 'Load next 500 samples'}</button>` : ''; }
async function openDrawer(id, planned = false) {
  const row = (planned ? state.plans : state.workouts).find(r => r.id === id); if (!row) return;
  focusBeforeDrawer = document.activeElement;
  const d = { id, row, planned, loading: !planned, samples: [], sampleTotal: 0, nextOffset: null, samplesLoading: !planned, sampleField: 'heart_rate' };
  state.drawer = d; renderDrawer(); drawerRoot.querySelector('[data-action="close-drawer"]:is(button)').focus();
  if (planned) return;
  await Promise.all([
    api(`/workouts/${encodeURIComponent(id)}`).then(detail => { if (state.drawer !== d) return; d.row = { ...row, ...detail }; }).catch(error => { d.error = error.message; }).finally(() => { if (state.drawer !== d) return; d.loading = false; renderDrawer(); }),
    loadSamples(d, 0),
  ]);
}
async function loadSamples(d, offset) {
  if (state.drawer !== d) return; d.samplesLoading = true; renderDrawer();
  try {
    const result = await api(`/workouts/${encodeURIComponent(d.id)}/samples?${new URLSearchParams({ fields: 'timestamp,heart_rate,power,distance', limit: '500', offset: String(offset) })}`);
    if (state.drawer !== d) return;
    d.samples.push(...result.items); d.sampleTotal = result.total; d.nextOffset = result.next_offset;
  } catch (error) { if (state.drawer === d) d.sampleError = error.message; }
  finally { if (state.drawer === d) { d.samplesLoading = false; renderDrawer(); } }
}
function closeDrawer() { state.drawer = null; renderDrawer(); focusBeforeDrawer?.focus(); }

async function signIn() {
  if (!auth) return;
  try { await auth.loginWithRedirect({ appState: { returnTo: '/dashboard/' } }); }
  catch { loginScreen('Couldn’t open secure sign-in. Please try again.'); }
}
document.addEventListener('click', event => {
  const target = event.target.closest('button, [data-action]'); if (!target) return;
  const action = target.dataset.action;
  if (action === 'login') return void signIn();
  if (action === 'logout') { state.workouts = []; state.plans = []; closeDrawer(); return void auth.logout({ logoutParams: { returnTo: config.logoutUri || `${location.origin}/dashboard/` } }); }
  if (action === 'close-drawer') return closeDrawer();
  if (action === 'more-samples' && state.drawer) return void loadSamples(state.drawer, state.drawer.nextOffset);
  if (target.dataset.sampleField && state.drawer) { state.drawer.sampleField = target.dataset.sampleField; renderDrawer(); return; }
  if (target.dataset.workout) return void openDrawer(target.dataset.workout);
  if (target.dataset.plan) return void openDrawer(target.dataset.plan, true);
  if (target.dataset.sport) { state.sport = target.dataset.sport; state.zone = 0; shell(); return; }
  if (target.dataset.view) { state.view = target.dataset.view; shell(); return; }
  if (target.dataset.mode) { state.calendarMode = target.dataset.mode; shell(); return; }
  if (target.dataset.day) { state.week = monday(target.dataset.day); state.month = monthStart(target.dataset.day); state.view = 'calendar'; state.calendarMode = 'week'; return void loadData(); }
  if (action === 'refresh') return void loadData();
  if (action === 'today') { state.month = monthStart(state.today); state.week = monday(state.today); return void loadData(); }
  if (action === 'previous' || action === 'next') {
    const delta = action === 'previous' ? -1 : 1;
    if (state.view === 'calendar' && state.calendarMode === 'week') { state.week = addDays(state.week, delta * 7); state.month = monthStart(state.week); }
    else { state.month = shiftMonth(state.month, delta); state.week = monday(state.month); }
    void loadData();
  }
});
document.addEventListener('change', event => { if (event.target.id === 'month-picker') { if (/^\d{4}-\d{2}$/.test(event.target.value) && event.target.validity.valid) { state.month = `${event.target.value}-01`; state.week = monday(state.month); void loadData(); } return; } if (event.target.id === 'zone-select') { state.zone = Number(event.target.value); if (state.drawer) renderDrawer(); else shell(); } });
document.addEventListener('keydown', event => {
  if (!state.drawer) return;
  if (event.key === 'Escape') { event.preventDefault(); closeDrawer(); }
  if (event.key === 'Tab') {
    const focusable = [...drawerRoot.querySelectorAll('button:not([disabled]), select, [tabindex="0"], a[href]')];
    const first = focusable[0], last = focusable.at(-1);
    if (event.shiftKey && (document.activeElement === first || !drawerRoot.contains(document.activeElement))) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && (document.activeElement === last || !drawerRoot.contains(document.activeElement))) { event.preventDefault(); first?.focus(); }
  }
});

async function boot() {
  try {
    const response = await fetch('/dashboard/config', { cache: 'no-store' }); if (!response.ok) throw new Error('config');
    config = await response.json();
    if (!config.domain || !config.clientId || !config.audience) throw new Error('config');
    config.timezone ||= 'Europe/Oslo'; state.today = localToday(config.timezone); state.month = monthStart(state.today); state.week = monday(state.today);
    auth = await createAuth0Client({ domain: config.domain, clientId: config.clientId, cacheLocation: 'memory', useRefreshTokens: true, useRefreshTokensFallback: true, authorizationParams: { audience: config.audience, scope: config.scope || 'openid profile email offline_access coach:read', redirect_uri: config.redirectUri || `${location.origin}/dashboard/` } });
    const params = new URLSearchParams(location.search);
    if (params.has('code') && params.has('state')) { await auth.handleRedirectCallback(); history.replaceState({}, '', '/dashboard/'); }
    if (params.has('error')) { history.replaceState({}, '', '/dashboard/'); loginScreen('Sign-in was not completed. You can try again below.'); return; }
    if (!(await auth.isAuthenticated())) { loginScreen(); return; }
    await loadData();
  } catch (error) {
    if (auth) { history.replaceState({}, '', '/dashboard/'); loginScreen('Your sign-in could not be completed. Please try signing in again.'); }
    else root.innerHTML = `<main id="main" class="setup-state">${brand}<h1>Your training space is almost ready.</h1><p>The dashboard connection is not available yet.<br>Please try reloading in a moment.</p><button class="button primary" id="reload-button">Reload dashboard</button></main>`;
    document.querySelector('#reload-button')?.addEventListener('click', () => location.reload());
  }
}
void boot();
