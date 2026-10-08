import { isNumber, number, extractMmpEnvelope } from '../data.js';
import { selectedRows } from '../selectors.js';

const DENSE_DURATIONS = [15, 30, 60, 120, 300, 600, 1200, 2400, 3600];
const TICK_DURATIONS = [
  [1, '1s'],
  [5, '5s'],
  [15, '15s'],
  [60, '1m'],
  [300, '5m'],
  [1200, '20m'],
  [3600, '1h'],
];

export function cpCurvePanel(state) {
  const physiology = state?.context?.physiology?.current_models?.cycling || state?.physiology || {};
  let cp = isNumber(physiology.critical_power_watts)
    ? Math.round(physiology.critical_power_watts)
    : null;
  let wPrime = isNumber(physiology.w_prime_joules) ? Math.round(physiology.w_prime_joules) : null;

  const rows = selectedRows(state?.workouts || [], state);
  const cyclingRows = rows.filter((r) => {
    const s = (r.sport || '').toLowerCase();
    return (
      s.includes('ride') || s.includes('cycle') || s.includes('cycling') || s.includes('zwift')
    );
  });
  const mmp = extractMmpEnvelope(cyclingRows.length ? cyclingRows : rows);
  const mmpMap = new Map(mmp.map((item) => [item.duration, item.power]));

  if (cp === null) {
    const athlete = state?.context?.athlete;
    if (isNumber(athlete?.model_cp_w)) {
      cp = Math.round(athlete.model_cp_w);
      if (isNumber(athlete?.model_w_prime_j)) wPrime = Math.round(athlete.model_w_prime_j);
    } else if (isNumber(athlete?.ftp_w)) {
      cp = Math.round(athlete.ftp_w);
    }
  }

  if (cp === null) {
    for (const r of cyclingRows) {
      const a = r.analysis;
      if (isNumber(a?.model_cp_w)) {
        cp = Math.round(a.model_cp_w);
        if (isNumber(a?.model_w_prime_j)) wPrime = Math.round(a.model_w_prime_j);
        break;
      }
      if (isNumber(a?.ftp_w)) {
        cp = Math.round(a.ftp_w);
        break;
      }
    }
  }

  if (cp === null) {
    const p300 = mmpMap.get(300);
    const p1200 = mmpMap.get(1200);
    if (isNumber(p300) && isNumber(p1200) && p1200 * 1200 > p300 * 300) {
      const derivedCp = (p1200 * 1200 - p300 * 300) / 900;
      const derivedW = p300 * 300 - derivedCp * 300;
      if (derivedCp >= 100 && derivedCp <= 600 && derivedW >= 5000 && derivedW <= 50000) {
        cp = Math.round(derivedCp);
        wPrime = Math.round(derivedW);
      }
    }
    if (cp === null && isNumber(p1200)) {
      cp = Math.round(p1200 * 0.95);
      wPrime = 20000;
    } else if (cp === null && isNumber(p300)) {
      cp = Math.round(p300 * 0.82);
      wPrime = 18000;
    } else if (cp === null) {
      const powers = cyclingRows
        .filter((r) => (r.metrics?.moving_time_s || 0) >= 1800)
        .map((r) => r.analysis?.weighted_average_power_w || r.metrics?.average_power_w)
        .filter(isNumber);
      if (powers.length) {
        cp = Math.round(Math.max(...powers));
        wPrime = 20000;
      }
    }
  }

  if (cp === null) {
    return /* HTML */ `<section class="panel cp-panel empty">
      <div class="panel-heading">
        <div>
          <h2>Critical Power & MMP Profile</h2>
          <p>Mean Maximal Power (1s – 60m) · Theoretical CP / W' Hyperbolic Overlay</p>
        </div>
        <div class="cp-badges">
          <span class="cp-pill unconfigured" title="No Critical Power configured"
            >Unconfigured</span
          >
        </div>
      </div>
      <div class="cp-empty-notice">
        <p>
          No Critical Power or FTP configured in athlete profile, and insufficient cycling power
          data to estimate a power-duration curve.
        </p>
        <p class="muted">
          Sync your profile from Intervals.icu or upload cycling activities with power data.
        </p>
      </div>
    </section>`;
  }

  if (wPrime === null) wPrime = 20000;

  const width = 640;
  const height = 220;
  const padLeft = 46;
  const padRight = 20;
  const padTop = 18;
  const padBottom = 28;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  const validMmp = mmp.filter((p) => isNumber(p.power)).map((p) => p.power);
  const curveSamplePowers = DENSE_DURATIONS.map((d) => cp + wPrime / d);
  const peakPower = Math.max(cp * 1.5, ...validMmp, ...curveSamplePowers.slice(0, 5), 450);
  const maxVal = Math.ceil(peakPower / 50) * 50;
  const minVal = 0;

  const logMin = Math.log(1);
  const logMax = Math.log(3600);
  const x = (t) =>
    (padLeft + ((Math.log(Math.max(t, 1)) - logMin) / (logMax - logMin)) * plotW).toFixed(1);
  const y = (val) =>
    (padTop + plotH - ((Math.min(val, maxVal) - minVal) / (maxVal - minVal)) * plotH).toFixed(1);

  const cpY = y(cp);

  // Theoretical hyperbola path and shaded W' polygon
  const curvePoints = DENSE_DURATIONS.map((d) => ({
    t: d,
    power: Math.round(cp + wPrime / d),
  }));
  const hyperbolaPath = curvePoints
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${x(p.t)},${y(p.power)}`)
    .join(' ');

  const wPrimePolygon = [
    `M${x(DENSE_DURATIONS[0])},${cpY}`,
    ...curvePoints.map((p) => `L${x(p.t)},${y(p.power)}`),
    `L${x(DENSE_DURATIONS.at(-1))},${cpY}`,
    'Z',
  ].join(' ');

  // Athlete MMP envelope path
  const mmpPoints = mmp.filter((p) => isNumber(p.power));
  const mmpPath = mmpPoints
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${x(p.duration)},${y(p.power)}`)
    .join(' ');

  const yTicks = [maxVal, Math.round(maxVal * 0.66), Math.round(maxVal * 0.33), 0].filter(
    (v, i, a) => a.indexOf(v) === i,
  );

  const p15 = mmpMap.get(15);
  const p300 = mmpMap.get(300);
  const p1200 = mmpMap.get(1200);

  return /* HTML */ `<section class="panel cp-panel">
    <div class="panel-heading">
      <div>
        <h2>Critical Power & MMP Profile</h2>
        <p>Mean Maximal Power (1s – 60m) · Theoretical CP / W' Hyperbolic Overlay</p>
      </div>
      <div class="cp-badges">
        <span class="cp-pill cp" title="Critical Power (Aerobic threshold power)">
          CP <strong>${number(cp)} W</strong>
        </span>
        <span class="cp-pill w-prime" title="W' Anaerobic Work Capacity">
          W' <strong>${(wPrime / 1000).toFixed(1)} kJ</strong>
        </span>
        ${p15 ? `<span class="cp-pill mmp-stat" title="15s Sprint Power">15s <strong>${number(p15)} W</strong></span>` : ''}
        ${p300 ? `<span class="cp-pill mmp-stat" title="5m VO2max Power">5m <strong>${number(p300)} W</strong></span>` : ''}
        ${p1200 ? `<span class="cp-pill mmp-stat" title="20m Threshold Power">20m <strong>${number(p1200)} W</strong></span>` : ''}
      </div>
    </div>
    <div class="cp-chart-wrap">
      <svg
        viewBox="0 0 ${width} ${height}"
        class="cp-svg"
        role="img"
        aria-label="Power-duration curve showing Critical Power ${number(cp)} W and W' ${(wPrime / 1000).toFixed(1)} kJ"
      >
        <defs>
          <linearGradient id="wPrimeGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="var(--amber)" stop-opacity="0.32" />
            <stop offset="100%" stop-color="var(--amber)" stop-opacity="0.05" />
          </linearGradient>
        </defs>
        ${yTicks
          .map(
            (val) =>
              `<line x1="${padLeft}" x2="${width - padRight}" y1="${y(val)}" y2="${y(val)}" class="cp-grid-line" />` +
              `<text x="${padLeft - 6}" y="${Number(y(val)) + 4}" class="cp-axis-label" text-anchor="end">${val}W</text>`,
          )
          .join('')}
        ${TICK_DURATIONS.map(
          ([t, label]) =>
            `<line x1="${x(t)}" x2="${x(t)}" y1="${padTop}" y2="${padTop + plotH}" class="cp-grid-line duration-line" />` +
            `<text x="${x(t)}" y="${height - 8}" class="cp-axis-label" text-anchor="middle">${label}</text>`,
        ).join('')}
        <path d="${wPrimePolygon}" class="cp-w-prime-area" fill="url(#wPrimeGradient)" />
        <line
          x1="${padLeft}"
          x2="${width - padRight}"
          y1="${cpY}"
          y2="${cpY}"
          class="cp-line asymptote"
          stroke-dasharray="4 4"
        />
        <text
          x="${width - padRight}"
          y="${Number(cpY) - 5}"
          class="cp-asymptote-label"
          text-anchor="end"
        >
          CP ${cp} W
        </text>
        <path d="${hyperbolaPath}" class="cp-line hyperbola" fill="none" />
        ${mmpPath ? `<path d="${mmpPath}" class="cp-line mmp" fill="none" />` : ''}
        ${mmpPoints
          .map(
            (p) =>
              `<circle cx="${x(p.duration)}" cy="${y(p.power)}" r="3.5" class="cp-dot"><title>${p.duration}s: ${p.power} W</title></circle>`,
          )
          .join('')}
      </svg>
    </div>
    <div class="cp-legend">
      <span class="legend-item"><i class="legend-swatch mmp"></i> Best Power (MMP)</span>
      <span class="legend-item"><i class="legend-swatch hyperbola"></i> Model CP + W'/t</span>
      <span class="legend-item"><i class="legend-swatch asymptote"></i> CP (${cp} W)</span>
      <span class="legend-item"
        ><i class="legend-swatch w-prime"></i> W' (${(wPrime / 1000).toFixed(1)} kJ)</span
      >
    </div>
  </section>`;
}
