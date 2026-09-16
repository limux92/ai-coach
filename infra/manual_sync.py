#!/usr/bin/env python3
"""Run an exact, checkpointed Intervals date range without moving scheduled cursors."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'infra')]

from google.auth.credentials import Credentials
from google.cloud import firestore, storage
from cloud import Cloud
from ai_coach.config import Settings
from ai_coach.intervals_client import IntervalsClient
from ai_coach.storage import Store
from ai_coach.summary_service import list_all, refresh_summaries
from ai_coach.sync import _Importer, _error_code, now, RUN_BUDGET_SECONDS


def windows(oldest, newest):
    end = newest
    while end >= oldest:
        start = max(oldest, end - timedelta(days=30))
        yield start, end
        if start == oldest:
            return
        end = start  # Match the production client's boundary-day overlap.


class GCloudCredentials(Credentials):
    def __init__(self, cloud):
        super().__init__()
        self.cloud = cloud

    def refresh(self, request):
        credential = self.cloud.json('config', 'config-helper')['credential']
        self.token = credential['access_token']
        self.expiry = datetime.fromisoformat(credential['token_expiry'].replace('Z', '+00:00')).astimezone(timezone.utc).replace(tzinfo=None)


class OperatorStore(Store):
    def __init__(self, settings, credentials):
        self.db = firestore.Client(project=settings.project, database=settings.database,
                                   credentials=credentials)
        self.bucket = storage.Client(project=settings.project, credentials=credentials).bucket(settings.bucket)


class WindowClient:
    """Keep historical plans inside the same window as workouts and wellness."""
    def __init__(self, client, oldest, newest):
        self.client, self.oldest, self.newest = client, oldest, newest
        self.seen = {}

    def __getattr__(self, name):
        return getattr(self.client, name)

    def list_activities(self, oldest, newest):
        rows = self.client.list_activities(oldest, newest)
        self.seen['activities'] = len(rows)
        return rows

    def list_wellness(self, oldest, newest):
        rows = self.client.list_wellness(oldest, newest)
        self.seen['wellness'] = len(rows)
        return rows

    def list_events(self, oldest, newest):
        # _Importer.plans(window_end) normally requests a wider calendar range.
        # Reuse its normalization/merge logic, while querying only this batch.
        if not oldest <= self.oldest <= self.newest <= newest:
            raise ValueError('Plan request does not cover the manual window')
        rows = self.client.list_events(self.oldest, self.newest)
        self.seen['calendar_events'] = len(rows)
        return rows


def inventory(store, oldest, newest):
    result = {}
    for collection in ('workouts', 'wellness', 'planned_workouts'):
        rows = list(list_all(store, collection, oldest.isoformat(), newest.isoformat(), lambda: None))
        if collection == 'workouts':
            rows = [r for r in rows if not r.get('source_deleted') and not r.get('source_excluded')]
        dates = sorted(r['local_date'] for r in rows)
        result[collection] = {'count': len(rows), 'oldest': dates[0] if dates else None,
                              'newest': dates[-1] if dates else None}
    return result


def run_window(store, settings, key, oldest, newest, job_id, index):
    run_id = f'{job_id}_{index}_{uuid.uuid4().hex[:8]}'
    if not store.acquire_lease(run_id, seconds=840):
        return {'status': 'already_running'}
    started = now()
    result = {'run_id': run_id, 'job_id': job_id, 'kind': 'manual_range',
              'oldest': oldest.isoformat(), 'newest': newest.isoformat(),
              'started_at': started.isoformat(), 'status': 'partial'}
    importer = None
    try:
        deadline = time.monotonic() + RUN_BUDGET_SECONDS
        with IntervalsClient(key, settings.athlete_id, deadline_monotonic=deadline) as upstream:
            client = WindowClient(upstream, oldest, newest)
            importer = _Importer(store, settings, client, deadline,
                                 store.get('sync_state', 'intervals') or {})
            importer.identify()
            complete = importer.range(oldest, newest)
            plans_complete = importer.plans(newest)
            importer.link_plans()
            while True:
                summaries = refresh_summaries(store, settings, datetime.now(ZoneInfo(settings.timezone)).date(), importer.check_budget)
                importer.counts['summaries_updated'] += summaries['updated']
                if not summaries['pending']:
                    break
            result['source_records'] = client.seen
            if complete and plans_complete and not importer.errors:
                result['status'] = 'ok_with_warnings' if importer.warnings or importer.counts['parse_terminal'] else 'ok'
    except Exception as exc:
        result['failure_code'] = _error_code(exc)
        result['retryable'] = bool(getattr(exc, 'retryable', False))
        result['retry_after_seconds'] = getattr(exc, 'retry_after_seconds', None)
    finally:
        if importer is not None:
            result.update(counts=importer.counts, errors=importer.errors, warnings=importer.warnings)
        result['finished_at'] = now().isoformat()
        try:
            store.put('sync_runs', run_id, result, merge=False)
        finally:
            store.release_lease(run_id, {})
    return result


def save_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as handle:
        os.chmod(temporary, 0o600)
        json.dump(report, handle, indent=2, default=str)
        handle.write('\n')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--oldest', type=date.fromisoformat, required=True)
    parser.add_argument('--newest', type=date.fromisoformat, required=True)
    args = parser.parse_args()
    if args.oldest > args.newest or args.newest > datetime.now(ZoneInfo('Europe/Oslo')).date():
        parser.error('Expected oldest <= newest <= today')
    os.environ['CLOUDSDK_CONFIG'] = str(ROOT / '.local/gcloud')
    os.environ['GCLOUD_BIN'] = str(ROOT / '.tools/google-cloud-sdk/bin/gcloud')
    config = json.loads((ROOT / 'deployment.json').read_text())
    settings = Settings(project=config['project_id'], bucket=config['bucket'],
                        database=config['database'], athlete_id=config['athlete_id'],
                        timezone=config['timezone'], history_start_date=config['history_start_date'])
    cloud = Cloud(settings.project)
    secret = cloud.rest('GET', f'https://secretmanager.googleapis.com/v1/projects/{settings.project}/secrets/intervals-api-key/versions/1:access')
    key = base64.b64decode(secret['payload']['data'], validate=True).decode().strip()
    store = OperatorStore(settings, GCloudCredentials(cloud))
    job_id = 'manual_' + now().strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:8]
    report_path = ROOT / '.local/verification' / (job_id + '.json')
    report = {'job_id': job_id, 'oldest': args.oldest.isoformat(), 'newest': args.newest.isoformat(),
              'started_at': now().isoformat(), 'status': 'running', 'windows': [],
              'before': inventory(store, args.oldest, args.newest)}
    batches = list(windows(args.oldest, args.newest))
    totals, observed = Counter(), Counter()
    print(json.dumps({'job_id': job_id, 'batches': len(batches), 'before': report['before']}), flush=True)
    save_report(report_path, report)
    for index, (oldest, newest) in enumerate(batches, 1):
        attempts, busy_started = 0, time.monotonic()
        while True:
            result = run_window(store, settings, key, oldest, newest, job_id, index)
            if result['status'] == 'already_running':
                if time.monotonic() - busy_started > 900:
                    raise RuntimeError('Manual sync could not acquire the importer lease')
                print(json.dumps({'batch': index, 'status': 'waiting_for_current_sync'}), flush=True)
                time.sleep(15)
                continue
            report['windows'].append(result)
            totals.update(result.get('counts', {}))
            save_report(report_path, report)
            attempts += 1
            if result['status'] in ('ok', 'ok_with_warnings'):
                observed.update(result.get('source_records', {}))
                store.put('sync_state', job_id, {'kind': 'manual_range', 'oldest': report['oldest'],
                    'newest': report['newest'], 'completed_windows': index,
                    'total_windows': len(batches), 'next_newest': oldest.isoformat(),
                    'updated_at': now(), 'status': 'running'})
                break
            if attempts >= 3 or result.get('failure_code') == 'authentication':
                report.update(status='partial', failed_window={'oldest': str(oldest), 'newest': str(newest)},
                              counts=dict(totals), finished_at=now().isoformat())
                save_report(report_path, report)
                print(json.dumps({'status': 'partial', 'report': str(report_path), 'result': result}), flush=True)
                return 1
            retry_until = time.monotonic() + max(10, result.get('retry_after_seconds') or 0)
            while time.monotonic() < retry_until:
                time.sleep(min(15, retry_until - time.monotonic()))
        print(json.dumps({'batch': index, 'of': len(batches), 'oldest': str(oldest), 'newest': str(newest),
                          'status': result['status'], 'counts': result['counts']}), flush=True)
    report.update(status='ok_with_warnings' if any(x.get('status') == 'ok_with_warnings' for x in report['windows']) else 'ok',
                  counts=dict(totals), source_record_observations_including_overlap=dict(observed),
                  after=inventory(store, args.oldest, args.newest), finished_at=now().isoformat())
    store.put('sync_state', job_id, {'status': report['status'], 'completed_at': now(),
                                    'counts': report['counts'], 'source_record_observations_including_overlap': dict(observed)})
    save_report(report_path, report)
    print(json.dumps({k: v for k, v in report.items() if k != 'windows'}), flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Never expose credential command output or upstream payloads in errors.
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}), flush=True)
        raise SystemExit(1)
