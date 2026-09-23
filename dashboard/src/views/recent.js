import {
  calendarDays,
  category,
  dayOf,
  loadOf,
  duration,
  km,
  formatDate,
  escapeHTML as esc,
} from '../data.js';
import { names, icon, datum } from '../ui.js';

export function recentTable(rows) {
  const items = [...rows]
    .sort((a, b) =>
      String(b.start_date_local || dayOf(b)).localeCompare(String(a.start_date_local || dayOf(a))),
    )
    .slice(0, 6);
  if (!items.length)
    return /* HTML */ `<div class="empty-state">
      ${icon('calendar')}
      <h3>A little room on the calendar</h3>
      <p>
        No imported workouts match this month and sport.<br />Explore another month to see your
        training.
      </p>
    </div>`;
  return /* HTML */ `<div class="table-scroll">
    <table class="workout-table">
      <thead>
        <tr>
          <th>Session</th>
          <th>Date</th>
          <th>Time</th>
          <th>Distance</th>
          <th>Load</th>
          <th><span class="sr-only">Details</span></th>
        </tr>
      </thead>
      <tbody>
        ${items
          .map((row) => {
            const type = category(row);
            return /* HTML */ `<tr>
              <td>
                <button class="workout-name" data-workout="${esc(row.id)}">
                  <span class="sport-icon ${type}">${icon(type)}</span
                  ><span
                    ><strong>${esc(row.name || names[type])}</strong
                    ><small
                      >${esc(row.recording_platform === 'zwift' ? 'Zwift · virtual' : row.sport || names[type])}</small
                    ></span
                  >
                </button>
              </td>
              <td>${formatDate(dayOf(row))}</td>
              <td>${duration(row.metrics?.moving_time_s, true)}</td>
              <td>${km(row.metrics?.distance_m)} <small>km</small></td>
              <td>${datum(loadOf(row))}</td>
              <td>
                <button
                  class="icon-button small"
                  data-workout="${esc(row.id)}"
                  aria-label="View ${esc(row.name || 'workout')}"
                >
                  ${icon('arrow')}
                </button>
              </td>
            </tr>`;
          })
          .join('')}
      </tbody>
    </table>
  </div>`;
}

export function miniCalendar(rows, state) {
  const dates = new Map();
  for (const row of rows) {
    if (!dates.has(dayOf(row))) dates.set(dayOf(row), []);
    dates.get(dayOf(row)).push(row);
  }
  return /* HTML */ `<div class="mini-calendar">
    <div class="mini-weekdays">
      ${['M', 'T', 'W', 'T', 'F', 'S', 'S'].map((d) => `<span>${d}</span>`).join('')}
    </div>
    <div class="mini-days">
      ${calendarDays(state.month)
        .map(
          (day) =>
            /* HTML */ `<button
              class="mini-day ${day.slice(0, 7) !== state.month.slice(0, 7) ? 'outside' : ''} ${dates.has(day) ? 'has-session' : ''} ${day === state.today ? 'is-today' : ''}"
              data-day="${day}"
              aria-label="${esc(formatDate(day, { year: 'numeric' }))}, ${dates.get(day)?.length || 0} workouts"
            >
              <span>${Number(day.slice(8))}</span
              ><i class="mini-dot ${dates.has(day) ? category(dates.get(day)[0]) : ''}"></i>
            </button>`,
        )
        .join('')}
    </div>
  </div>`;
}
