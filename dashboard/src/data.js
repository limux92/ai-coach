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

export function pmcSeries(rows, ending, lookbackDays = 84) {
  const sorted = [...rows].sort((a, b) => dayOf(a).localeCompare(dayOf(b)));
  const daily = new Map();
  for (const r of sorted) {
    const day = dayOf(r);
    const load = loadOf(r);
    if (!daily.has(day)) {
      daily.set(day, { load: 0, ctl: null, atl: null });
    }
    const d = daily.get(day);
    if (isNumber(load)) d.load += load;
    if (isNumber(r.analysis?.ctl)) d.ctl = r.analysis.ctl;
    if (isNumber(r.analysis?.atl)) d.atl = r.analysis.atl;
  }

  const start = addDays(ending, -lookbackDays + 1);
  const decayCtl = Math.exp(-1 / 42);
  const decayAtl = Math.exp(-1 / 7);

  let ctl = 0;
  let atl = 0;
  let seeded = false;

  for (const [day, data] of daily.entries()) {
    if (day <= start && data.ctl !== null && data.atl !== null) {
      ctl = data.ctl;
      atl = data.atl;
      seeded = true;
    }
  }

  if (!seeded) {
    for (const [, data] of daily.entries()) {
      if (data.ctl !== null && data.atl !== null) {
        ctl = data.ctl;
        atl = data.atl;
        seeded = true;
        break;
      }
    }
  }

  const series = [];
  for (let i = 0; i < lookbackDays; i++) {
    const date = addDays(start, i);
    const data = daily.get(date);
    const load = data ? data.load : 0;

    if (data?.ctl !== null && data?.ctl !== undefined && isNumber(data.ctl)) {
      ctl = data.ctl;
    } else {
      ctl = ctl * decayCtl + load * (1 - decayCtl);
    }

    if (data?.atl !== null && data?.atl !== undefined && isNumber(data.atl)) {
      atl = data.atl;
    } else {
      atl = atl * decayAtl + load * (1 - decayAtl);
    }

    const tsb = ctl - atl;
    series.push({
      date,
      load: Math.round(load * 10) / 10,
      ctl: Math.round(ctl * 10) / 10,
      atl: Math.round(atl * 10) / 10,
      tsb: Math.round(tsb * 10) / 10,
    });
  }

  return series;
}

export function formStatus(tsb) {
  if (!isNumber(tsb)) return { label: 'Unknown', tone: 'neutral' };
  if (tsb > 25) return { label: 'Transition / Rest', tone: 'warning' };
  if (tsb > 5) return { label: 'Fresh / Peaked', tone: 'fresh' };
  if (tsb >= -10) return { label: 'Maintenance', tone: 'neutral' };
  if (tsb >= -30) return { label: 'Optimal Training', tone: 'optimal' };
  return { label: 'High Fatigue', tone: 'fatigue' };
}

export const STANDARD_MMP_BUCKETS = [1, 5, 15, 30, 60, 120, 180, 300, 600, 1200, 3600];

export function bestRollingPower(samples, buckets = STANDARD_MMP_BUCKETS) {
  if (!Array.isArray(samples) || !samples.length) {
    return buckets.map((duration) => ({ duration, power: null }));
  }
  const values = samples.map((s) => {
    if (s == null) return 0;
    const v = typeof s === 'object' ? s.power : s;
    return typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : 0;
  });

  return buckets.map((duration) => {
    if (values.length < duration) {
      return { duration, power: null };
    }
    let sum = 0;
    for (let i = 0; i < duration; i++) sum += values[i];
    let maxAvg = sum / duration;

    for (let i = duration; i < values.length; i++) {
      sum += values[i] - values[i - duration];
      const avg = sum / duration;
      if (avg > maxAvg) maxAvg = avg;
    }

    return { duration, power: Math.round(maxAvg) };
  });
}

export function criticalPowerCurve(cp, wPrime, buckets = STANDARD_MMP_BUCKETS) {
  if (!isNumber(cp) || cp <= 0 || !isNumber(wPrime) || wPrime <= 0) {
    return [];
  }
  return buckets.map((duration) => ({
    duration,
    power: Math.round(cp + wPrime / duration),
  }));
}

export function extractMmpEnvelope(workouts, buckets = STANDARD_MMP_BUCKETS) {
  const result = new Map(buckets.map((d) => [d, null]));
  if (!Array.isArray(workouts))
    return Array.from(result, ([duration, power]) => ({ duration, power }));

  for (const w of workouts) {
    if (Array.isArray(w?.samples) && w.samples.length) {
      const workoutMmp = bestRollingPower(w.samples, buckets);
      for (const item of workoutMmp) {
        if (item.power !== null) {
          const cur = result.get(item.duration);
          if (cur === null || item.power > cur) result.set(item.duration, item.power);
        }
      }
    } else if (w?.metrics) {
      const avgP = isNumber(w.metrics.average_power_w) ? w.metrics.average_power_w : null;
      const maxP = isNumber(w.metrics.max_power_w) ? w.metrics.max_power_w : null;
      const duration = isNumber(w.metrics.moving_time_s) ? w.metrics.moving_time_s : 0;

      if (maxP !== null) {
        const cur1 = result.get(1);
        if (cur1 === null || maxP > cur1) result.set(1, maxP);
        const cur5 = result.get(5);
        if (cur5 === null || maxP > cur5) result.set(5, Math.round(maxP * 0.96));
      }
      if (avgP !== null && duration > 0) {
        for (const b of buckets) {
          if (b <= duration) {
            const cur = result.get(b);
            if (cur === null || avgP > cur) result.set(b, Math.round(avgP));
          }
        }
      }
    }
  }

  return Array.from(result, ([duration, power]) => ({ duration, power }));
}
