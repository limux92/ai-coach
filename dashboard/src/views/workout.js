import {
  isNumber,
  category,
  dayOf,
  loadOf,
  duration,
  km,
  formatDate,
  number,
  escapeHTML as esc,
} from '../data.js';
import { names, icon, datum, spinner } from '../ui.js';
import { sampleChart } from './samples.js';
import { zones } from './charts.js';

export function drawerMetric(label, value, unit = '') {
  return /* HTML */ `<div class="detail-metric">
    <span>${label}</span><strong>${value}<small>${unit}</small></strong>
  </div>`;
}

export function drawerShell(row, content, planned) {
  const type = category(row);
  return /* HTML */ `<div class="drawer-backdrop" data-action="close-drawer"></div>
    <section
      class="drawer"
      role="dialog"
      aria-modal="true"
      aria-labelledby="drawer-title"
      tabindex="-1"
    >
      <header class="drawer-header">
        <span class="drawer-kind"
          ><i class="sport-icon ${type}">${icon(type)}</i
          >${planned ? 'PLANNED SESSION' : 'COMPLETED SESSION'}</span
        ><button class="icon-button" data-action="close-drawer" aria-label="Close workout details">
          ${icon('close')}
        </button>
      </header>
      <div class="drawer-content">
        <p class="eyebrow">${esc(formatDate(dayOf(row), { weekday: 'long', year: 'numeric' }))}</p>
        <h2 id="drawer-title">${esc(row.name || names[type])}</h2>
        <p class="drawer-subtitle">
          ${esc(row.sport || names[type])}${row.recording_platform === 'zwift' ? ' · Zwift virtual activity' : ''}
        </p>
        ${content}
      </div>
    </section>`;
}

export function renderDrawer(drawerRoot, state) {
  if (!state.drawer) {
    drawerRoot.innerHTML = '';
    document.body.classList.remove('drawer-open');
    return;
  }
  const focused = drawerRoot.contains(document.activeElement) ? document.activeElement : null;
  const focusedKey = focused?.dataset.action
    ? `[data-action="${focused.dataset.action}"]`
    : focused?.dataset.sampleField
      ? `[data-sample-field="${focused.dataset.sampleField}"]`
      : focused?.id === 'zone-select'
        ? '#zone-select'
        : null;
  const d = state.drawer,
    row = d.row,
    m = row.metrics || {},
    planned = d.planned;
  const metrics = [
    drawerMetric('Duration', duration(planned ? m.duration_s : m.moving_time_s)),
    drawerMetric('Distance', km(m.distance_m), 'km'),
    drawerMetric('Training load', datum(planned ? m.training_load : loadOf(row))),
    drawerMetric('Average heart rate', datum(m.average_heart_rate_bpm), 'bpm'),
    drawerMetric('Average power', datum(m.average_power_w), 'W'),
    drawerMetric('Elevation gain', datum(m.elevation_gain_m), 'm'),
  ].join('');
  let content = /* HTML */ `<div class="detail-metrics">${metrics}</div>`;
  if (row.distance_type === 'virtual')
    content += '<p class="detail-note">Distance and elevation describe a virtual route.</p>';
  if (planned) {
    content += /* HTML */ `<div class="detail-section">
      <h3>Session notes</h3>
      <p class="plan-description">
        ${esc(row.description || 'Session notes are not included in this calendar view.')}
      </p>
      <p class="detail-note">Status: ${esc(row.status || 'planned')}</p>
    </div>`;
  } else {
    if (d.loading)
      content += `<div class="detail-loading" role="status">${spinner} Loading workout details…</div>`;
    if (d.error)
      content += `<div class="inline-error" role="alert">${icon('info')}${esc(d.error)}</div>`;
    content += /* HTML */ `<div class="detail-section">
      <div class="section-heading">
        <h3>Session profile</h3>
        <div class="segmented">
          <button
            data-sample-field="heart_rate"
            class="${d.sampleField === 'heart_rate' ? 'selected' : ''}"
            aria-pressed="${d.sampleField === 'heart_rate'}"
          >
            Heart rate</button
          ><button
            data-sample-field="power"
            class="${d.sampleField === 'power' ? 'selected' : ''}"
            aria-pressed="${d.sampleField === 'power'}"
          >
            Power
          </button>
        </div>
      </div>
      ${sampleChart(d)}
    </div>`;
    const hr = row.zone_summary?.heart_rate || row.heart_rate_zones;
    if (hr?.status === 'available')
      content += `<div class="detail-section"><h3>Heart-rate zones</h3>${zones([row], state)}</div>`;
    const laps = row.laps_summary;
    if (Array.isArray(laps) && laps.length)
      content += /* HTML */ `<div class="detail-section">
        <h3>Laps</h3>
        <p class="detail-note">
          ${number(row.lap_count ?? laps.length)} laps
          recorded${row.laps_summary_truncated ? ` · ${number(row.laps_omitted)} omitted from the compact view` : ''}.
        </p>
      </div>`;
    if (row.description)
      content += /* HTML */ `<div class="detail-section">
        <h3>Session notes</h3>
        <p class="plan-description">${esc(row.description)}</p>
        ${row.description_truncated ? '<p class="detail-note">Description shortened in this view.</p>' : ''}
      </div>`;
    if (row.observations && (isNumber(row.observations.rpe) || isNumber(row.observations.feel)))
      content += /* HTML */ `<div class="detail-section">
        <h3>How it felt</h3>
        <div class="detail-metrics compact-metrics">
          ${drawerMetric('Perceived effort', datum(row.observations.rpe), '/ 10')}${drawerMetric('Feeling', datum(row.observations.feel))}
        </div>
      </div>`;
  }
  content += /* HTML */ `<div class="provenance">
    <span>${icon('lock')} YOUR TRAINING RECORD</span>
    <dl>
      <dt>Source</dt>
      <dd>
        ${esc(row.source_attribution || row.garmin_attribution || (row.provider_source === 'UPLOAD' ? 'FIT upload through Intervals.icu' : 'Intervals.icu'))}
      </dd>
      <dt>Activity ID</dt>
      <dd>${esc(row.source_id || row.id)}</dd>
      ${
        !planned
          ? /* HTML */ `<dt>Detail availability</dt>
              <dd>
                ${row.parse_status === 'summary_only' ? 'Summary available · original FIT archived' : esc(row.sample_availability || row.parse_status || 'Available fields shown above')}
              </dd>`
          : ''
      }
    </dl>
  </div>`;
  drawerRoot.innerHTML = drawerShell(row, content, planned);
  document.body.classList.add('drawer-open');
  if (focusedKey)
    drawerRoot
      .querySelector(`button${focusedKey}, select${focusedKey}`)
      ?.focus({ preventScroll: true });
}
