import {
  isNumber,
  addDays,
  calendarDays,
  category,
  dayOf,
  loadOf,
  totals,
  calendarPeriodRows,
  duration,
  km,
  formatDate,
  number,
  escapeHTML as esc,
} from '../data.js';
import { names, icon, datum } from '../ui.js';
import { selectedRows, inWindow } from '../selectors.js';

export function calendar(state) {
  const days =
    state.calendarMode === 'week'
      ? Array.from({ length: 7 }, (_, i) => addDays(state.week, i))
      : calendarDays(state.month);
  const rows = selectedRows(state.workouts, state),
    plans = selectedRows(state.plans, state);
  const weeks = Array.from({ length: days.length / 7 }, (_, i) => days.slice(i * 7, i * 7 + 7));
  const visibleRows = calendarPeriodRows(rows, {
    mode: state.calendarMode,
    month: state.month,
    week: state.week,
  });
  const total = totals(visibleRows);
  const virtualDistance = visibleRows.some(
    (r) => r.distance_type === 'virtual' || String(r.sport).includes('Virtual'),
  );
  const footnote = [
    state.calendarMode === 'month'
      ? 'Weekly totals include complete Monday–Sunday weeks, including adjacent months.'
      : '',
    virtualDistance ? 'Totals include virtual distance.' : '',
    total.durationCount < total.count || total.distanceCount < total.count
      ? 'Some sessions have missing metrics; totals include available values.'
      : '',
    'Blank days mean no imported or planned workouts in this view. They don’t confirm rest days.',
  ]
    .filter(Boolean)
    .join(' ');
  return /* HTML */ `<section class="panel calendar-panel">
    <div class="calendar-toolbar">
      <div class="calendar-summary">
        <b class="calendar-total-label"
          >${state.calendarMode === 'month' ? 'Month total' : 'Week total'}</b
        ><strong>${total.count}</strong> sessions <span>·</span>
        <strong>${duration(total.duration)}</strong> <span>·</span>
        <strong>${km(total.distance)}</strong> km
      </div>
      <div class="segmented" role="group" aria-label="Calendar layout">
        <button
          data-mode="month"
          class="${state.calendarMode === 'month' ? 'selected' : ''}"
          aria-pressed="${state.calendarMode === 'month'}"
        >
          Month</button
        ><button
          data-mode="week"
          class="${state.calendarMode === 'week' ? 'selected' : ''}"
          aria-pressed="${state.calendarMode === 'week'}"
        >
          Week
        </button>
      </div>
    </div>
    <div class="calendar-scroll">
      <div class="calendar-grid ${state.calendarMode}">
        <div class="calendar-weekdays">
          ${['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d) => `<div>${d}</div>`).join('')}
          <div class="week-total-heading">Weekly total</div>
        </div>
        ${weeks.map((week) => calendarWeek(week, rows, plans, state)).join('')}
      </div>
    </div>
    <div class="calendar-footnote">
      ${icon('info')}
      <span>${footnote}</span>
    </div>
  </section>`;
}

export function calendarWeek(days, rows, plans, state) {
  const weekRows = inWindow(rows, days[0], days[6]);
  const total = totals(weekRows);
  return /* HTML */ `<div class="calendar-week">
    ${days
      .map((day) => {
        const done = rows.filter((row) => dayOf(row) === day),
          upcoming = plans.filter(
            (row) =>
              dayOf(row) === day &&
              !['completed', 'cancelled', 'canceled', 'deleted', 'skipped'].includes(
                String(row.status).toLowerCase(),
              ),
          );
        return /* HTML */ `<section
          class="calendar-day ${day.slice(0, 7) !== state.month.slice(0, 7) ? 'outside' : ''} ${day === state.today ? 'today' : ''}"
          aria-label="${esc(formatDate(day, { weekday: 'long', year: 'numeric' }))}"
        >
          <div class="day-heading">
            <span class="day-number">${Number(day.slice(8))}</span
            >${day === state.today ? '<span class="today-label">TODAY</span>' : Number(day.slice(8)) === 1 ? `<span class="day-month">${formatDate(day, { day: undefined })}</span>` : ''}
          </div>
          <div class="day-sessions">
            ${done.map((r) => calendarCard(r, false)).join('')}${upcoming.map((r) => calendarCard(r, true)).join('')}${!done.length && !upcoming.length ? '<span class="no-session" aria-label="No workouts">—</span>' : ''}
          </div>
        </section>`;
      })
      .join('')}
    <aside class="week-total">
      <span class="week-label">${formatDate(days[0])}</span
      ><strong>${duration(total.duration, true)}</strong><span>${km(total.distance)} km</span
      ><span>${total.count} sessions</span>
      <div class="week-load">${icon('load')} ${datum(total.load)} <small>load</small></div>
    </aside>
  </div>`;
}

export function calendarCard(row, planned) {
  const type = category(row),
    m = row.metrics || {},
    seconds = planned ? m.duration_s : m.moving_time_s;
  return /* HTML */ `<button
    class="calendar-card ${type} ${planned ? 'planned' : ''}"
    ${planned ? `data-plan="${esc(row.id)}"` : `data-workout="${esc(row.id)}"`}
    aria-label="${planned ? 'Planned' : 'Completed'} ${esc(row.name || names[type])}, ${duration(seconds)}"
  >
    <span class="card-sport"
      >${icon(type)}<strong>${duration(seconds, true)}</strong>${planned ? '<span class="planned-tag">PLAN</span>' : icon('check', 'card-check')}</span
    ><span class="card-name">${esc(row.name || names[type])}</span
    ><span class="card-metrics"
      >${isNumber(m.distance_m) ? `${km(m.distance_m)} km` : names[type]}${!planned && isNumber(m.average_heart_rate_bpm) ? ` <i>·</i> ${number(m.average_heart_rate_bpm)} bpm` : ''}</span
    >${isNumber(planned ? m.training_load : loadOf(row)) ? `<span class="card-load">Load ${number(planned ? m.training_load : loadOf(row))}</span>` : ''}
  </button>`;
}
