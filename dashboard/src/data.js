// Date-only arithmetic is UTC-based; the current local date is supplied in the athlete's timezone.
export const isNumber = (n) => typeof n === 'number' && Number.isFinite(n);
export const isoDate = (date) => date.toISOString().slice(0, 10);
export const dateOf = (str) => new Date(`${str.slice(0, 10)}T12:00:00Z`);
export const addDays = (str, days) => {
  const d = dateOf(str);
  d.setUTCDate(d.getUTCDate() + days);
  return isoDate(d);
};
export const monthStart = (str) => `${str.slice(0, 7)}-01`;
export const monthEnd = (str) => {
  const d = dateOf(monthStart(str));
  d.setUTCMonth(d.getUTCMonth() + 1);
  d.setUTCDate(0);
  return isoDate(d);
};
export const shiftMonth = (str, delta) => {
  const d = dateOf(monthStart(str));
  d.setUTCMonth(d.getUTCMonth() + delta);
  return isoDate(d);
};
export const monday = (str) => addDays(str, -((dateOf(str).getUTCDay() + 6) % 7));
export function calendarDays(str) {
  const first = monday(monthStart(str));
  const last = addDays(monday(monthEnd(str)), 6);
  return Array.from({ length: Math.round((dateOf(last) - dateOf(first)) / 86400000) + 1 }, (_, i) =>
    addDays(first, i),
  );
}
export function localToday(timezone = 'Europe/Oslo') {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const get = (key) => parts.find((p) => p.type === key).value;
  return `${get('year')}-${get('month')}-${get('day')}`;
}
export function category(row) {
  const sport = String(row.sport || '').toLowerCase();
  if (sport.includes('run')) return 'run';
  if (['ride', 'cycle', 'cycling', 'bike'].some((x) => sport.includes(x))) return 'cycle';
  return 'other';
}
export const dayOf = (row) => String(row.local_date || row.start_date_local || '').slice(0, 10);
export const loadOf = (row) =>
  row.analysis?.training_load ?? row.provider_estimates?.training_load ?? null;
export function totals(rows) {
  const fields = {
    duration: (row) => row.metrics?.moving_time_s,
    distance: (row) => row.metrics?.distance_m,
    load: loadOf,
  };
  const result = { count: rows.length, activeDays: new Set(rows.map(dayOf)).size };
  for (const [key, select] of Object.entries(fields)) {
    const values = rows.map(select).filter(isNumber);
    result[key] = values.length ? values.reduce((a, b) => a + b, 0) : rows.length ? null : 0;
    result[`${key}Count`] = values.length;
  }
  return result;
}
export function calendarPeriodRows(rows, { mode, month, week }) {
  const first = mode === 'week' ? week : monthStart(month);
  const last = mode === 'week' ? addDays(week, 6) : monthEnd(month);
  return rows.filter((row) => dayOf(row) >= first && dayOf(row) <= last);
}

export function zoneGroups(rows) {
  const groups = new Map();
  for (const row of rows) {
    const hr = row.zone_summary?.heart_rate || row.heart_rate_zones;
    if (
      hr?.status !== 'available' ||
      !Array.isArray(hr.seconds) ||
      !Array.isArray(hr.boundaries_bpm) ||
      hr.seconds.length !== hr.boundaries_bpm.length ||
      !hr.seconds.every(isNumber)
    )
      continue;
    // Explicit boundaries and sport keep historical zone definitions distinct, even when IDs are absent.
    const key = `${category(row)}:${hr.zone_definition_id || ''}:${hr.boundary_semantics || ''}:${JSON.stringify(hr.boundaries_bpm)}`;
    if (!groups.has(key))
      groups.set(key, {
        key,
        sport: category(row),
        boundaries: hr.boundaries_bpm,
        seconds: hr.seconds.map(() => 0),
        count: 0,
      });
    const group = groups.get(key);
    group.seconds = group.seconds.map((n, i) => n + hr.seconds[i]);
    group.count++;
  }
  return [...groups.values()]
    .sort((a, b) => b.count - a.count)
    .map((g) => ({ ...g, total: g.seconds.reduce((a, b) => a + b, 0) }));
}
export function weekSeries(rows, ending) {
  const last = monday(ending);
  return Array.from({ length: 12 }, (_, i) => {
    const start = addDays(last, (i - 11) * 7),
      end = addDays(start, 6);
    const items = rows.filter((r) => dayOf(r) >= start && dayOf(r) <= end);
    return {
      start,
      end,
      count: items.length,
      durationCount: items.filter((r) => isNumber(r.metrics?.moving_time_s)).length,
      ...Object.fromEntries(
        ['run', 'cycle', 'other'].map((type) => [
          type,
          items
            .filter((r) => category(r) === type)
            .reduce(
              (sum, r) => sum + (isNumber(r.metrics?.moving_time_s) ? r.metrics.moving_time_s : 0),
              0,
            ),
        ]),
      ),
    };
  });
}
export function duration(seconds, compact = false) {
  if (!isNumber(seconds)) return '—';
  const minutes = Math.round(seconds / 60),
    hours = Math.floor(minutes / 60),
    rest = minutes % 60;
  return hours ? `${hours}h${rest ? ` ${rest}m` : ''}` : `${minutes}${compact ? 'm' : ' min'}`;
}
export function km(meters, digits = 1) {
  return isNumber(meters)
    ? new Intl.NumberFormat('en-GB', {
        maximumFractionDigits: digits,
        minimumFractionDigits: digits,
      }).format(meters / 1000)
    : '—';
}
export function formatDate(str, options = {}) {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'UTC',
    day: 'numeric',
    month: 'short',
    ...options,
  }).format(dateOf(str));
}
export const number = (n) =>
  isNumber(n) ? new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 }).format(n) : '—';
export const escapeHTML = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
