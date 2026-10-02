import {
  monthEnd,
  zoneGroups,
  weekSeries,
  duration,
  formatDate,
  number,
  escapeHTML as esc,
} from '../data.js';
import { names, icon } from '../ui.js';
import { selectedRows } from '../selectors.js';

export function trendChart(state) {
  const ending =
    state.month.slice(0, 7) === state.today.slice(0, 7) ? state.today : monthEnd(state.month);
  const weeks = weekSeries(selectedRows(state.workouts, state), ending);
  const max = Math.max(...weeks.map((w) => w.run + w.cycle + w.other), 3600);
  const ceiling = Math.ceil(max / 3600 / 4) * 4;
  return /* HTML */ `<div
      class="trend-chart"
      role="img"
      aria-label="Weekly training hours for the twelve weeks ending ${esc(formatDate(ending))}"
    >
      <div class="chart-scale">
        ${[1, 0.75, 0.5, 0.25, 0].map((n) => `<span>${number(ceiling * n)}<i></i></span>`).join('')}
      </div>
      <div class="chart-columns">
        ${weeks
          .map((week, i) => {
            const total = week.run + week.cycle + week.other;
            const timeLabel =
              week.count && !week.durationCount
                ? 'duration unknown'
                : `${duration(total)}${week.durationCount < week.count ? ' (partial time coverage)' : ''}`;
            return /* HTML */ `<div class="chart-column">
              <div class="bar-space">
                <div
                  class="stacked-bar ${i === 11 ? 'current' : ''}"
                  tabindex="0"
                  aria-label="Week of ${esc(formatDate(week.start))}: ${timeLabel}, ${week.count} sessions"
                  title="${esc(formatDate(week.start))}–${esc(formatDate(week.end))}: ${timeLabel} · ${week.count} sessions"
                >
                  ${['other', 'cycle', 'run'].map((type) => /* HTML */ `<span class="bar-part ${type}" style="height:${((week[type] / 3600 / ceiling) * 176).toFixed(2)}px"></span>`).join('')}
                </div>
              </div>
              <span class="bar-label"
                >${i % 2 === 0 || i === 11 ? formatDate(week.start) : ' '}</span
              >
            </div>`;
          })
          .join('')}
      </div>
    </div>
    ${weeks.some((w) => w.durationCount < w.count) ? '<p class="chart-coverage">Some sessions have no moving time. Bars show only recorded durations.</p>' : ''}`;
}

export function zones(rows, state) {
  const groups = zoneGroups(rows);
  if (!groups.length)
    return /* HTML */ `<div class="empty-state compact">
      ${icon('heart')}
      <h3>No zone data in this view</h3>
      <p>Heart-rate zones appear when supplied with an imported workout.</p>
    </div>`;
  state.zone = Math.min(state.zone, groups.length - 1);
  const group = groups[state.zone];
  const select =
    groups.length > 1
      ? /* HTML */ `<label class="zone-select-label"
          >Zone definition<select id="zone-select" aria-label="Heart-rate zone definition">
            ${groups.map((g, i) => /* HTML */ `<option value="${i}" ${state.zone === i ? 'selected' : ''}>${names[g.sport]} · ${g.boundaries.join(' / ')} bpm · ${g.count} sessions</option>`).join('')}
          </select></label
        >`
      : /* HTML */ `<div class="zone-context">
          <span class="sport-dot ${group.sport}"></span>${names[group.sport]} · ${group.count}
          ${group.count === 1 ? 'session' : 'sessions'}
        </div>`;
  return /* HTML */ `${select}
    <div class="zone-bars">
      ${group.seconds
        .map(
          (seconds, i) =>
            /* HTML */ `<div class="zone-row">
              <span class="zone-label">Z${i + 1}</span
              ><span class="zone-range"
                >${i === 0 ? '≤ ' : `${group.boundaries[i - 1] + 1}–`}${group.boundaries[i]}</span
              >
              <div class="zone-track">
                <span
                  class="zone-fill zone-${Math.min(i, 6)}"
                  style="width:${group.total ? ((seconds / group.total) * 100).toFixed(2) : 0}%"
                ></span>
              </div>
              <strong>${duration(seconds, true)}</strong>
            </div>`,
        )
        .join('')}
    </div>
    <p class="chart-caption">
      BPM boundaries · ${duration(group.total)} classified.<br />${groups.length > 1 ? 'Different zone definitions are shown separately.' : 'Percentages use classified time; HR coverage may be incomplete.'}
    </p>`;
}
