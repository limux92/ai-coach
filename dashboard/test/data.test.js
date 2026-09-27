import test from 'node:test';
import assert from 'node:assert/strict';
import {
  calendarDays,
  monday,
  addDays,
  shiftMonth,
  totals,
  calendarPeriodRows,
  zoneGroups,
  weekSeries,
  category,
  escapeHTML,
} from '../src/data.js';

test('calendar starts on Monday, includes the full leap month, and crosses years safely', () => {
  const days = calendarDays('2024-02-01');
  assert.equal(days[0], '2024-01-29');
  assert.equal(days.at(-1), '2024-03-03');
  assert.ok(days.includes('2024-02-29'));
  assert.equal(monday('2026-01-01'), '2025-12-29');
  assert.equal(addDays('2024-03-01', -1), '2024-02-29');
  assert.equal(shiftMonth('2026-01-31', -1), '2025-12-01');
});
test('missing metrics remain unknown and coverage is measured', () => {
  assert.equal(totals([{ metrics: {} }]).duration, null);
  assert.equal(totals([]).duration, 0);
  const t = totals([
    {
      local_date: '2026-09-01',
      metrics: { moving_time_s: 100, distance_m: 1000 },
      analysis: { training_load: 25 },
    },
    { local_date: '2026-09-01', metrics: { distance_m: 2000 } },
  ]);
  assert.equal(t.durationCount, 1);
  assert.equal(t.duration, 100);
  assert.equal(t.distance, 3000);
  assert.equal(t.activeDays, 1);
  assert.equal(t.loadCount, 1);
});
test('zone time never combines incompatible historical boundaries or sports', () => {
  const row = (sport, boundaries, seconds) => ({
    sport,
    zone_summary: { heart_rate: { status: 'available', boundaries_bpm: boundaries, seconds } },
  });
  const groups = zoneGroups([
    row('Run', [120, 150], [60, 90]),
    row('Run', [120, 150], [10, 20]),
    row('Run', [125, 155], [10, 30]),
    row('VirtualRide', [120, 150], [50, 60]),
  ]);
  assert.equal(groups.length, 3);
  assert.equal(groups[0].total, 180);
  assert.equal(groups[0].count, 2);
});
test('weekly chart assigns sessions across month and year boundaries', () => {
  const series = weekSeries(
    [{ local_date: '2025-12-31', sport: 'VirtualRide', metrics: { moving_time_s: 3600 } }],
    '2026-01-01',
  );
  assert.equal(series.length, 12);
  assert.equal(series.at(-1).cycle, 3600);
  assert.equal(series.at(-1).start, '2025-12-29');
  assert.equal(category({ sport: 'TrailRun' }), 'run');
});
test('source text is escaped before insertion into HTML', () => {
  assert.equal(escapeHTML('<img src=x onerror="x">'), '&lt;img src=x onerror=&quot;x&quot;&gt;');
});

test('weekly chart reports missing duration coverage', () => {
  const week = weekSeries(
    [{ local_date: '2026-09-01', sport: 'Run', metrics: {} }],
    '2026-09-01',
  ).at(-1);
  assert.equal(week.count, 1);
  assert.equal(week.durationCount, 0);
});

test('month calendar totals exclude adjacent-month cards while week totals include the full week', () => {
  const rows = [
    { local_date: '2026-05-31', metrics: { distance_m: 10000 } },
    { local_date: '2026-06-01', metrics: { distance_m: 170600 } },
    { local_date: '2026-06-30', metrics: { distance_m: 4600 } },
    { local_date: '2026-07-02', metrics: { distance_m: 4600 } },
  ];
  const month = totals(
    calendarPeriodRows(rows, { mode: 'month', month: '2026-06-01', week: '2026-06-29' }),
  );
  assert.equal(month.count, 2);
  assert.equal(month.distance, 175200);
  const week = totals(
    calendarPeriodRows(rows, { mode: 'week', month: '2026-06-01', week: '2026-06-29' }),
  );
  assert.equal(week.count, 2);
  assert.equal(week.distance, 9200);
});
