import test from 'node:test';
import assert from 'node:assert/strict';
import { shell } from '../src/views/shell.js';
import { calendar } from '../src/views/calendar.js';
import { loginScreen, pendingPaymentScreen } from '../src/views/login.js';
import { sampleChart } from '../src/views/samples.js';
import { drawerShell } from '../src/views/workout.js';

const fixture = (overrides = {}) => ({
  view: 'overview',
  sport: 'all',
  calendarMode: 'month',
  month: '2026-09-01',
  week: '2026-08-31',
  today: '2026-09-23',
  loading: false,
  zone: 0,
  workouts: [
    { id: 'old', local_date: '2026-08-31', sport: 'Ride', metrics: { moving_time_s: 3600 } },
    {
      id: 'current',
      local_date: '2026-09-01',
      sport: 'Run',
      name: '<img src=x onerror=alert(1)>',
      metrics: {},
    },
  ],
  plans: [
    {
      id: 'planned',
      local_date: '2026-09-02',
      sport: 'Ride',
      status: 'planned',
      metrics: { duration_s: 1800 },
    },
    { id: 'cancelled', local_date: '2026-09-03', sport: 'Ride', status: 'cancelled' },
  ],
  ...overrides,
});
const config = { timezone: 'Europe/Oslo' };

test('extracted overview renders scoped totals, unknown data, and escaped names', () => {
  const html = shell(fixture(), config);
  assert.match(html, /September 2026/);
  assert.match(html, /0 of 1 sessions with/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(html, /<img src=x/);
  assert.match(html, /data-workout="current"/);
  assert.doesNotMatch(html, /data-workout="old"/);
  assert.doesNotMatch(html, /quick-workout|Quick Workout|Quick Ride|Quick Run/);
  assert.doesNotMatch(
    shell(fixture({ view: 'calendar' }), config),
    /quick-workout|Quick Workout|Quick Ride|Quick Run/,
  );
});

test('recent sessions displays fallback workouts when selected month has 0 workouts', () => {
  const emptyMonth = fixture({ month: '2026-10-01' });
  const html = shell(emptyMonth, config);
  assert.match(html, /data-workout="current"/);
  assert.match(html, /Ingen økter denne måneden/);
});

test('calendar renders adjacent cards but counts the selected period, hiding cancelled plans', () => {
  const month = calendar(fixture());
  assert.match(month, /Month total[\s\S]*?<strong>1<\/strong>/);
  assert.match(month, /data-workout="old"/);
  assert.match(month, /data-plan="planned"/);
  assert.doesNotMatch(month, /data-plan="cancelled"/);
  assert.match(month, /missing metrics/);
  const week = calendar(fixture({ calendarMode: 'week' }));
  assert.match(week, /Week total[\s\S]*?<strong>2<\/strong>/);
  const cycling = calendar(fixture({ sport: 'cycle' }));
  assert.doesNotMatch(cycling, /data-workout="current"/);
});

test('loading and auth errors do not expose a stale training view', () => {
  for (const state of [
    fixture({ loading: true }),
    fixture({ error: '<expired>', authError: true }),
  ]) {
    assert.doesNotMatch(shell(state, config), /data-workout="current"/);
  }
  const html = shell(fixture({ error: '<expired>', authError: true }), config);
  assert.match(html, /&lt;expired&gt;/);
  assert.match(html, /data-action="login"/);
  assert.match(loginScreen('<sign-in failed>'), /&lt;sign-in failed&gt;/);
  assert.match(loginScreen('', 'register'), /Register with Google/);
  assert.match(loginScreen('', 'signin'), /Sign in with Google/);
  const pendingHtml = pendingPaymentScreen({
    display_name: '<script>alert(1)</script>',
    email: 'new@example.com',
  });
  assert.match(pendingHtml, /Payment Required/);
  assert.match(pendingHtml, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(pendingHtml, /<script>alert\(1\)<\/script>/);
  assert.match(drawerShell(fixture().workouts[1], '', false), /&lt;img src=x/);
});

test('sample preview stays bounded and missing readings break the line', () => {
  const samples = Array.from({ length: 10_000 }, (_, i) => ({
    heart_rate: i === 400 ? null : 120,
  }));
  const html = sampleChart({
    samples,
    sampleField: 'heart_rate',
    sampleTotal: 20_000,
    nextOffset: 10_000,
  });
  const path = html.match(/<path d="([^"]*)" class="sample-line/)[1];
  assert((path.match(/[ML]/g) || []).length <= 701);
  assert.equal((path.match(/M/g) || []).length, 2);
  assert.match(html, /downsampled preview/);
  assert.match(html, /partial session/);
  assert.match(html, /data-action="more-samples"/);
});

test('pmcSeries calculates rolling fitness, fatigue, and form accurately', async () => {
  const { pmcSeries, formStatus } = await import('../src/data.js');
  const rows = [
    { local_date: '2026-09-01', analysis: { training_load: 100, ctl: 50, atl: 60 } },
    { local_date: '2026-09-10', analysis: { training_load: 80 } },
  ];
  const series = pmcSeries(rows, '2026-09-23', 84);
  assert.equal(series.length, 84);
  assert.equal(series.at(-1).date, '2026-09-23');

  // Check form status classifications
  assert.equal(formStatus(30).tone, 'warning');
  assert.equal(formStatus(15).tone, 'fresh');
  assert.equal(formStatus(0).tone, 'neutral');
  assert.equal(formStatus(-20).tone, 'optimal');
  assert.equal(formStatus(-35).tone, 'fatigue');
});

test('overview view renders Performance Management Chart with curves and badges', () => {
  const html = shell(fixture(), config);
  assert.match(html, /Performance Management \(PMC\)/);
  assert.match(html, /pmc-panel/);
  assert.match(html, /pmc-line ctl/);
  assert.match(html, /pmc-line atl/);
  assert.match(html, /pmc-line tsb/);
  assert.match(html, /pmc-zone-optimal/);
});

test('overview view renders Critical Power and MMP profile panel with log-scale curve and W_prime overlay', () => {
  const f = fixture();
  f.workouts.push({
    id: 'cycling-mmp',
    local_date: '2026-09-02',
    sport: 'VirtualRide',
    metrics: { moving_time_s: 3600, average_power_w: 260, max_power_w: 750 },
  });
  f.context = {
    physiology: {
      current_models: {
        cycling: {
          critical_power_watts: 280,
          w_prime_joules: 22000,
        },
      },
    },
  };
  const html = shell(f, config);
  assert.match(html, /Critical Power & MMP Profile/);
  assert.match(html, /cp-panel/);
  assert.match(html, /cp-line asymptote/);
  assert.match(html, /cp-line hyperbola/);
  assert.match(html, /cp-w-prime-area/);
  assert.match(html, /CP <strong>280 W<\/strong>/);
  assert.match(html, /W' <strong>22\.0 kJ<\/strong>/);
});

test('overview view dynamically estimates CP from workout MMP data when physiology model is null', () => {
  const f = fixture();
  f.workouts.push({
    id: 'ride-20m-test',
    local_date: '2026-09-02',
    sport: 'Ride',
    metrics: { moving_time_s: 1800, average_power_w: 300, max_power_w: 450 },
  });
  f.context = null; // No context / no physiology model
  const html = shell(f, config);
  // With 1800s ride averaging 300W, 300s/1200s MMP is extracted and CP is derived
  assert.match(html, /Critical Power & MMP Profile/);
  assert.doesNotMatch(html, /CP <strong>250 W<\/strong>/); // Should NOT be fixed at 250W
});

test('overview view extracts CP and W prime from workout analysis when physiology model is null', () => {
  const f = fixture();
  f.workouts.push({
    id: 'ride-with-ftp',
    local_date: '2026-09-02',
    sport: 'Ride',
    analysis: { model_cp_w: 295, model_w_prime_j: 24000 },
  });
  f.context = null;
  const html = shell(f, config);
  assert.match(html, /CP <strong>295 W<\/strong>/);
  assert.match(html, /W' <strong>24\.0 kJ<\/strong>/);
});

test('overview view extracts CP and W prime from athlete profile in context when physiology model is null', () => {
  const f = fixture();
  f.workouts = [];
  f.context = {
    athlete: {
      ftp_w: 275,
      model_cp_w: 282,
      model_w_prime_j: 19000,
    },
  };
  const html = shell(f, config);
  assert.match(html, /CP <strong>282 W<\/strong>/);
  assert.match(html, /W' <strong>19\.0 kJ<\/strong>/);
});

test('overview view renders unconfigured panel when no CP, athlete profile, or cycling power data exist', () => {
  const f = fixture();
  f.workouts = [];
  f.context = null;
  const html = shell(f, config);
  assert.match(html, /cp-panel empty/);
  assert.match(html, /Unconfigured/);
  assert.match(html, /No Critical Power or FTP configured/);
  assert.doesNotMatch(html, /CP <strong>250 W<\/strong>/);
});

test('pmcSeries warms up CTL and ATL when historical workouts precede the chart window', async () => {
  const { pmcSeries } = await import('../src/data.js');
  // 30 daily workouts of load 60 from 2026-06-01 to 2026-06-30
  const rows = [];
  for (let d = 1; d <= 30; d++) {
    const day = d < 10 ? `0${d}` : `${d}`;
    rows.push({ local_date: `2026-06-${day}`, analysis: { training_load: 60 } });
  }
  // Chart window of 14 days starting 2026-07-01
  const series = pmcSeries(rows, '2026-07-14', 14);
  assert.equal(series.length, 14);
  // Day 0 of chart window should already have warm CTL > 0 due to 30 days of prior training
  assert.ok(series[0].ctl > 25, `Expected warmed-up CTL > 25, got ${series[0].ctl}`);
  assert.ok(series[0].atl > 25, `Expected warmed-up ATL > 25, got ${series[0].atl}`);
});
