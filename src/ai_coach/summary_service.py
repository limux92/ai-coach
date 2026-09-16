"""Durable, bounded summary maintenance under the existing importer lease.

Invalidate before changing a workout. A crash may rebuild an unchanged bucket,
but cannot publish changed data while claiming that old totals are current.
Never increment totals on replay: rebuild affected buckets from current records.
"""
from datetime import date, timedelta

from .normalize import SCHEMA_VERSION
from .storage import fingerprint, json_bytes, utcnow
from .summaries import CALCULATION_VERSION, period_bounds, summarize_workouts
from .zones import CALCULATION_VERSION as ZONE_VERSION, build_zone_summary

PAGE_SIZE = 200
MAX_JOBS = 100


def projection(doc):
    return {key: doc.get(key) for key in (
        'id', 'local_date', 'sport', 'metrics', 'analysis', 'zone_summary',
        'source_deleted', 'source_excluded',
    )}


def save_workout(store, doc_id, data, *, merge=True):
    old = store.get('workouts', doc_id) or {}
    new = {**old, **data} if merge else dict(data)
    if fingerprint(projection(old)) != fingerprint(projection(new)):
        queue_workout(store, doc_id, old, new)
    store.put('workouts', doc_id, data, merge=merge)


def queue_workout(store, doc_id, old, new):
    store.put('sync_state', 'summaries', {
        'status': 'pending', 'pending_workouts': True,
        'history_complete': False, 'calculation_version': CALCULATION_VERSION,
    })
    previous = store.get('summary_jobs', doc_id) or {}
    dates = set(previous.get('dates', [])) if previous.get('pending') else set()
    for doc in (old, new):
        value = doc.get('local_date')
        if value:
            dates.add(date.fromisoformat(value).isoformat())
    store.put('summary_jobs', doc_id, {
        'id': doc_id, 'dates': sorted(dates), 'pending': True, 'updated_at': utcnow(),
    }, merge=False)


def list_all(store, collection, start, end, check_budget):
    cursor = None
    while True:
        check_budget()
        rows = store.list(collection, start, end, limit=PAGE_SIZE, after=cursor)
        yield from rows
        if len(rows) < PAGE_SIZE:
            return
        if cursor == rows[-1]['id']:
            raise RuntimeError('Pagination did not advance')
        cursor = rows[-1]['id']


def migrate_workouts(store, check_budget):
    """Reproject archived JSON once per calculation/schema version, without FIT IO."""
    migration = store.get('sync_state', 'summary_migration') or {}
    version = f'{CALCULATION_VERSION}.{SCHEMA_VERSION}.{ZONE_VERSION}'
    if migration.get('version') == version and migration.get('complete'):
        return True
    cursor = migration.get('cursor') if migration.get('version') == version else None
    rows = store.scan('workouts', limit=MAX_JOBS, after=cursor)
    for doc in rows:
        check_budget()
        updated = dict(doc)
        if ((doc.get('zone_summary') or {}).get('calculation_version') != ZONE_VERSION
                or doc.get('schema_version') == 1):
            raw = store.read_json(doc['raw_payload_artifact']) if doc.get('raw_payload_artifact') else {}
            payload = {**raw.get('summary', {}), **raw.get('detail', {})}
            updated['zone_summary'] = build_zone_summary(payload)
            # Schema 1->2 adds only the zone projection. Future schema migrations
            # must use their own explicit upgrader, not relabel old fields.
            if doc.get('schema_version') == 1 and SCHEMA_VERSION == 2:
                updated['schema_version'] = 2
        queue_workout(store, doc['id'], doc, updated)
        store.put('workouts', doc['id'], updated, merge=False)
        store.put('sync_state', 'summary_migration', {
            'version': version, 'cursor': doc['id'], 'complete': False,
        }, merge=False)
    complete = len(rows) < MAX_JOBS
    store.put('sync_state', 'summary_migration', {
        'version': version, 'cursor': rows[-1]['id'] if rows else cursor,
        'complete': complete,
    }, merge=False)
    return complete


def refresh_summaries(store, settings, today, check_budget=lambda: None):
    """Update affected totals; retained jobs make failed/partial work replayable."""
    try:
        migration_complete = migrate_workouts(store, check_budget)
        state = store.get('sync_state', 'summaries') or {}
        jobs = store.scan('summary_jobs', limit=MAX_JOBS + 1, pending=True)
        selected, more_jobs = jobs[:MAX_JOBS], len(jobs) > MAX_JOBS
        dates = {day for job in selected for day in job.get('dates', [])}
        targets = set()
        for day in dates:
            for period in ('day', 'week', 'month'):
                start, end = period_bounds(day, period)
                targets.add((period, start, end, start))
            # A saved rolling window is a view of current known history, not an
            # immutable past snapshot. Repair every existing window containing
            # an edited/moved/deleted day, including non-current windows.
            latest = (date.fromisoformat(day) + timedelta(days=27)).isoformat()
            for saved in list_all(store, 'training_summaries', day, latest, check_budget):
                if (saved.get('period') in ('rolling7', 'rolling28')
                        and saved.get('start_date', '') <= day <= saved.get('end_date', '')):
                    targets.add((saved['period'], saved['start_date'], saved['end_date'], saved['local_date']))
        daily_rollover = (state.get('as_of_day') != today.isoformat()
                          or state.get('calculation_version') != CALCULATION_VERSION)
        if daily_rollover and state.get('as_of_day'):
            for period in ('week', 'month'):
                start, end = period_bounds(state['as_of_day'], period)
                targets.add((period, start, end, start))
        if dates or daily_rollover or state.get('status') != 'ok':
            for period in ('week', 'month'):
                start, end = period_bounds(today.isoformat(), period)
                targets.add((period, start, end, start))
            for period, days in (('rolling7', 7), ('rolling28', 28)):
                for end_day in (today, today - timedelta(days=days)):
                    start, end = period_bounds(end_day.isoformat(), period)
                    targets.add((period, start, end, end))
        if targets:
            store.put('sync_state', 'summaries', {'status': 'pending', 'pending_workouts': True})
        changed = 0
        for period, start, end, key_date in sorted(targets):
            check_budget()
            rows = list(list_all(store, 'workouts', start, end, check_budget))
            result = summarize_workouts(rows, start=start, end=end,
                                        period=period, timezone=settings.timezone)
            result.update(local_date=key_date, date_basis='activity_local_start',
                          period_in_progress=start <= today.isoformat() <= end)
            digest = fingerprint(result)
            doc_id = f'{period}_{key_date}'
            previous = store.get('training_summaries', doc_id) or {}
            if previous.get('source_revision') != digest:
                result.update(id=doc_id, source_revision=digest, generated_at=utcnow())
                if len(json_bytes(result)) > 800_000:
                    raise ValueError('Summary exceeds document budget')
                store.put('training_summaries', doc_id, result, merge=False)
                changed += 1
        for job in selected:
            check_budget()
            store.put('summary_jobs', job['id'], {'pending': False, 'completed_at': utcnow()})
        pending = more_jobs or not migration_complete
        store.put('sync_state', 'summaries', {
            'status': 'pending' if pending else 'ok', 'pending_workouts': pending,
            'updated_at': utcnow(), 'as_of_day': today.isoformat(),
            'calculation_version': CALCULATION_VERSION, 'history_complete': False,
            'totals_scope': 'known_imported_workouts', 'last_error_code': None,
        })
        return {'updated': changed, 'pending': pending}
    except Exception as exc:
        store.put('sync_state', 'summaries', {
            'status': 'failed', 'pending_workouts': True,
            'last_error_code': type(exc).__name__, 'history_complete': False,
        })
        raise
