import test from 'node:test';
import assert from 'node:assert/strict';
import { shell } from '../src/views/shell.js';
import { calendar } from '../src/views/calendar.js';
import { loginScreen } from '../src/views/login.js';
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
