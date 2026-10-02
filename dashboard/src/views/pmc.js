import { monthEnd, formatDate, number, escapeHTML as esc, pmcSeries, formStatus } from '../data.js';
import { selectedRows } from '../selectors.js';

export function pmcPanel(state) {
  const ending =
    state.month.slice(0, 7) === state.today.slice(0, 7) ? state.today : monthEnd(state.month);
  const rows = selectedRows(state.workouts, state);
  const series = pmcSeries(rows, ending, 84);
  const current = series.at(-1) || { ctl: 0, atl: 0, tsb: 0, load: 0 };
  const status = formStatus(current.tsb);

  const width = 640;
  const height = 220;
  const padLeft = 40;
  const padRight = 16;
  const padTop = 16;
  const padBottom = 28;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  const allValues = series.flatMap((s) => [s.ctl, s.atl, s.tsb]);
  const minVal = Math.min(-35, Math.floor(Math.min(...allValues) / 10) * 10);
  const maxVal = Math.max(50, Math.ceil(Math.max(...allValues) / 10) * 10);
  const valRange = maxVal - minVal || 1;

  const maxLoad = Math.max(50, ...series.map((s) => s.load));

  const x = (i) => (padLeft + (i / Math.max(series.length - 1, 1)) * plotW).toFixed(1);
  const y = (val) => (padTop + plotH - ((val - minVal) / valRange) * plotH).toFixed(1);
  const yLoad = (load) => (padTop + plotH - (load / maxLoad) * (plotH * 0.35)).toFixed(1);

  const zeroY = y(0);
  const optTop = y(-10);
  const optBottom = y(-30);
  const optH = Math.abs(Number(optBottom) - Number(optTop)).toFixed(1);

  const ctlPath = series.map((s, i) => `${i === 0 ? 'M' : 'L'}${x(i)},${y(s.ctl)}`).join(' ');
  const atlPath = series.map((s, i) => `${i === 0 ? 'M' : 'L'}${x(i)},${y(s.atl)}`).join(' ');
  const tsbPath = series.map((s, i) => `${i === 0 ? 'M' : 'L'}${x(i)},${y(s.tsb)}`).join(' ');

  const ticks = [maxVal, Math.round((maxVal + minVal) / 2), 0, minVal].filter(
    (v, i, a) => a.indexOf(v) === i,
  );

  const dateStep = 14;
  const dateLabels = [];
  for (let i = 0; i < series.length; i += dateStep) {
    dateLabels.push({
      x: x(i),
      label: formatDate(series[i].date, { month: 'numeric', day: 'numeric' }),
    });
  }
  dateLabels.push({
    x: x(series.length - 1),
    label: formatDate(series.at(-1).date, { month: 'numeric', day: 'numeric' }),
  });

  return /* HTML */ `<section class="panel pmc-panel">
    <div class="panel-heading">
      <div>
        <h2>Performance Management (PMC)</h2>
        <p>Fitness (CTL), Fatigue (ATL) & Form (TSB) · 12 Weeks</p>
      </div>
      <div class="pmc-badges">
        <span class="pmc-pill ctl" title="Chronic Training Load (42-day rolling fitness)">
          CTL <strong>${number(current.ctl)}</strong>
        </span>
        <span class="pmc-pill atl" title="Acute Training Load (7-day rolling fatigue)">
          ATL <strong>${number(current.atl)}</strong>
        </span>
        <span
          class="pmc-pill tsb ${status.tone}"
          title="Training Stress Balance (Form = Fitness - Fatigue)"
        >
          TSB <strong>${current.tsb > 0 ? '+' : ''}${number(current.tsb)}</strong>
        </span>
        <span class="form-tag ${status.tone}">${status.label}</span>
      </div>
    </div>
    <div class="pmc-chart-wrap">
      <svg
        viewBox="0 0 ${width} ${height}"
        class="pmc-svg"
        role="img"
        aria-label="Performance Management Chart showing CTL ${number(current.ctl)}, ATL ${number(current.atl)}, and TSB ${number(current.tsb)}"
      >
        <!-- Optimal training zone (-10 to -30 TSB) -->
        <rect
          x="${padLeft}"
          y="${Math.min(Number(optTop), Number(optBottom)).toFixed(1)}"
          width="${plotW}"
          height="${optH}"
          class="pmc-zone-optimal"
        />

        <!-- Zero line for TSB -->
        <line
          x1="${padLeft}"
          x2="${width - padRight}"
          y1="${zeroY}"
          y2="${zeroY}"
          class="pmc-zero-line"
        />

        <!-- Grid lines and Y-axis labels -->
        ${ticks
          .map(
            (t) => /* HTML */ `
              <line
                x1="${padLeft}"
                x2="${width - padRight}"
                y1="${y(t)}"
                y2="${y(t)}"
                class="pmc-grid-line"
              />
              <text
                x="${padLeft - 6}"
                y="${(Number(y(t)) + 4).toFixed(1)}"
                text-anchor="end"
                class="pmc-axis-label"
              >
                ${t}
              </text>
            `,
          )
          .join('')}

        <!-- Daily Training Load impulse bars -->
        ${series
          .map((s, i) =>
            s.load > 0
              ? /* HTML */ `<rect
                  x="${(Number(x(i)) - 2).toFixed(1)}"
                  y="${yLoad(s.load)}"
                  width="4"
                  height="${(padTop + plotH - Number(yLoad(s.load))).toFixed(1)}"
                  class="pmc-load-bar"
                >
                  <title>${esc(formatDate(s.date))}: Load ${s.load}</title>
                </rect>`
              : '',
          )
          .join('')}

        <!-- Curves: ATL (fatigue), CTL (fitness), TSB (form) -->
        <path d="${atlPath}" class="pmc-line atl" fill="none" />
        <path d="${ctlPath}" class="pmc-line ctl" fill="none" />
        <path d="${tsbPath}" class="pmc-line tsb" fill="none" />

        <!-- X-axis date labels -->
        ${dateLabels
          .map(
            (dl) => /* HTML */ `
              <text x="${dl.x}" y="${height - 8}" text-anchor="middle" class="pmc-axis-label">
                ${dl.label}
              </text>
            `,
          )
          .join('')}
      </svg>
    </div>
    <div class="pmc-legend">
      <span><i class="legend-line ctl"></i>Fitness (CTL)</span>
      <span><i class="legend-line atl"></i>Fatigue (ATL)</span>
      <span><i class="legend-line tsb"></i>Form (TSB)</span>
      <span><i class="legend-box load"></i>Daily Load</span>
      <span class="legend-optimal"><i class="legend-zone"></i>Optimal Zone (-10 to -30)</span>
    </div>
  </section>`;
}
