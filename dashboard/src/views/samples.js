import { isNumber, duration, number, escapeHTML as esc } from '../data.js';
import { icon, spinner } from '../ui.js';

export function sampleChart(d) {
  if (d.samplesLoading && !d.samples.length)
    return `<div class="sample-empty" role="status">${spinner}<p>Loading the session profile…</p></div>`;
  if (d.sampleError)
    return `<div class="sample-empty">${icon('info')}<p>${esc(d.sampleError)}</p></div>`;
  const field = d.sampleField,
    points = d.samples.map((s, i) => ({ x: i, value: s[field], timestamp: s.timestamp }));
  const valid = points.filter((p) => isNumber(p.value));
  if (!valid.length)
    return /* HTML */ `<div class="sample-empty">
      ${icon(field === 'heart_rate' ? 'heart' : 'load')}
      <p>
        No ${field === 'heart_rate' ? 'heart-rate' : 'power'} samples in this portion of the
        workout.
      </p>
      ${samplePaging(d)}
    </div>`;
  const min = Math.max(
      0,
      Math.floor((valid.reduce((n, p) => Math.min(n, p.value), Infinity) - 10) / 10) * 10,
    ),
    max = Math.max(
      min + 20,
      Math.ceil((valid.reduce((n, p) => Math.max(n, p.value), -Infinity) + 5) / 10) * 10,
    );
  const t0 = Date.parse(points[0]?.timestamp),
    t1 = Date.parse(points.at(-1)?.timestamp);
  const useTime = Number.isFinite(t0) && Number.isFinite(t1) && t1 > t0;
  const x = (p) =>
    40 +
    (useTime && Number.isFinite(Date.parse(p.timestamp))
      ? (Date.parse(p.timestamp) - t0) / (t1 - t0)
      : p.x / Math.max(points.length - 1, 1)) *
      450;
  let previous = false;
  // Bound SVG size without modifying the stored samples. Missing measurements still break the line.
  const stride = Math.max(1, Math.ceil(points.length / 700));
  const path = points
    .map((p, i) => {
      if (!isNumber(p.value)) {
        previous = false;
        return '';
      }
      if (i % stride !== 0 && i !== points.length - 1) return '';
      const output = `${previous ? 'L' : 'M'}${x(p).toFixed(1)},${(160 - ((p.value - min) / (max - min)) * 140).toFixed(1)}`;
      previous = true;
      return output;
    })
    .join(' ');
  const unit = field === 'heart_rate' ? 'bpm' : 'W';
  return /* HTML */ `<div class="sample-chart">
      <svg
        viewBox="0 0 510 195"
        role="img"
        aria-label="${field === 'heart_rate' ? 'Heart rate' : 'Power'} for ${d.samples.length} loaded samples, ${min} to ${max} ${unit}"
      >
        ${[0, 0.5, 1].map((n) => /* HTML */ `<line x1="40" x2="490" y1="${160 - n * 140}" y2="${160 - n * 140}" class="sample-grid" /><text x="30" y="${164 - n * 140}" text-anchor="end">${number(min + n * (max - min))}</text>`).join('')}
        <path d="${path}" class="sample-line ${field}" />
        <text x="40" y="187">${useTime ? '0 min' : 'First sample'}</text>
        <text x="490" y="187" text-anchor="end">
          ${useTime ? duration((t1 - t0) / 1000) : `${d.samples.length} samples`}
        </text>
      </svg>
    </div>
    <p class="chart-caption">
      ${unit} · ${number(d.samples.length)} of ${number(d.sampleTotal)} samples
      loaded${stride > 1 ? ' · downsampled preview' : ''}${d.nextOffset != null ? ' · partial session' : ''}
    </p>
    ${samplePaging(d)}`;
}

export function samplePaging(d) {
  return d.nextOffset != null
    ? /* HTML */ `<button
        class="button load-samples"
        data-action="more-samples"
        ${d.samplesLoading ? 'disabled' : ''}
      >
        ${d.samplesLoading ? `${spinner} Loading…` : 'Load next 500 samples'}
      </button>`
    : '';
}
