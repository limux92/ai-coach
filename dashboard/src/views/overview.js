import { totals, duration, km, number, escapeHTML as esc } from '../data.js';
import { names, icon } from '../ui.js';
import { monthRows, prettyMonth, selectedRows } from '../selectors.js';
import { trendChart, zones } from './charts.js';
import { pmcPanel } from './pmc.js';
import { cpCurvePanel } from './cp_curve.js';
import { recentTable, miniCalendar } from './recent.js';

export function metricCard(label, value, unit, note, symbol, tone = '') {
  return /* HTML */ `<article class="metric-card ${tone}">
    <div class="metric-top"><span>${label}</span>${icon(symbol)}</div>
    <div class="metric-value">${value}<small>${unit}</small></div>
    <div class="metric-foot">${note}</div>
  </article>`;
}

export function overview(state) {
  const rows = monthRows(state),
    summary = totals(rows);
  const coverage = (key, noun) =>
    summary[`${key}Count`] < rows.length
      ? `${summary[`${key}Count`]} of ${rows.length} sessions with ${noun}`
      : noun;
  const mixedDistance = rows.some(
    (r) => r.distance_type === 'virtual' || String(r.sport).includes('Virtual'),
  );
  const metrics = [
    metricCard(
      'Training time',
      duration(summary.duration),
      '',
      coverage('duration', 'Moving time across your sessions'),
      'clock',
      'featured',
    ),
    metricCard(
      'Distance',
      km(summary.distance),
      'km',
      coverage(
        'distance',
        mixedDistance ? 'Includes virtual distance' : 'Distance covered this month',
      ),
      'distance',
    ),
    metricCard(
      'Completed sessions',
      number(summary.count),
      '',
      `${summary.activeDays} active ${summary.activeDays === 1 ? 'day' : 'days'} this month`,
      'check',
    ),
    metricCard(
      'Training load',
      number(summary.load),
      '',
      coverage('load', 'Provider-estimated load'),
      'load',
    ),
  ].join('');
  return /* HTML */ `<section class="metric-grid" aria-label="Monthly training totals">
      ${metrics}
    </section>
    ${pmcPanel(state)} ${cpCurvePanel(state)}
    <div class="charts-grid">
      <section class="panel trend-panel">
        <div class="panel-heading">
          <div>
            <h2>Consistency over time</h2>
            <p>Your training volume across 12 weeks</p>
          </div>
          <span class="quiet-badge">Hours / week</span>
        </div>
        ${trendChart(state)}
        <div class="chart-legend">
          ${['run', 'cycle', 'other']
            .filter((x) => state.sport === 'all' || state.sport === x)
            .map((x) => `<span><i class="legend-dot ${x}"></i>${names[x]}</span>`)
            .join('')}
        </div>
      </section>
      <section class="panel zones-panel">
        <div class="panel-heading">
          <div>
            <h2>Time in heart-rate zones</h2>
            <p>How your effort was distributed</p>
          </div>
          ${icon('heart')}
        </div>
        ${zones(rows, state)}
      </section>
    </div>
    <div class="bottom-grid">
      <section class="panel recent-panel">
        <div class="panel-heading">
          <div>
            <h2>Recent sessions</h2>
            <p>
              ${esc(prettyMonth(state.month))} · ${rows.length} completed
              ${rows.length === 1 ? 'workout' : 'workouts'}
            </p>
          </div>
          <button class="text-button" data-view="calendar">View calendar ${icon('arrow')}</button>
        </div>
        ${recentTable(rows, selectedRows(state.workouts, state))}
      </section>
      <section class="panel month-panel">
        <div class="panel-heading">
          <div>
            <h2>A month of movement</h2>
            <p>${esc(prettyMonth(state.month))}</p>
          </div>
          ${icon('calendar')}
        </div>
        ${miniCalendar(rows, state)}
        <div class="month-insight">
          <strong>${summary.activeDays}</strong
          ><span>days you showed up<br /><small>Every session is part of the story.</small></span>
        </div>
      </section>
    </div>`;
}
