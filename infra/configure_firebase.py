#!/usr/bin/env python3
"""Prepare Firebase Google sign-in in the existing AI Coach project.

Uses the Firebase CLI's provisioning API; does not create a new Cloud project,
training database, service-account key or enable phone/password providers.
"""
import argparse
import json
import os
from pathlib import Path
import time

from cloud import Cloud

PROJECT = 'magne-ai-coach-20260915'
ORIGIN = 'https://ai-coach-chat-600465847441.europe-north1.run.app'
APP_NAME = 'AI Coach Firebase login'
ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as file:
        json.dump(value, file, indent=2)
        file.write('\n')


def wait_operation(cloud, operation):
    for _ in range(90):
        if operation.get('done'):
            if operation.get('error'):
                raise RuntimeError('Firebase provisioning operation failed; inspect its private receipt')
            return operation.get('response', {})
        time.sleep(2)
        operation = cloud.rest('GET', 'https://firebase.googleapis.com/v1beta1/' + operation['name'])
    raise RuntimeError('Firebase provisioning is still running; rerun to inspect the result')


def prepare(cloud, receipt):
    if cloud.project != PROJECT:
        raise ValueError('This helper targets the existing AI Coach project')
    cloud.command('services', 'enable', 'firebase.googleapis.com', 'identitytoolkit.googleapis.com', 'securetoken.googleapis.com')
    base = 'https://firebase.googleapis.com/v1beta1/projects/' + cloud.project
    if cloud.rest('GET', base, allow_missing=True) is None:
        wait_operation(cloud, cloud.rest('POST', base + ':addFirebase', {}))
    apps = cloud.rest('GET', base + '/webApps').get('apps', [])
    matches = [app for app in apps if app.get('displayName') == APP_NAME]
    if len(matches) > 1:
        raise ValueError('Multiple matching Firebase apps require review')
    app = matches[0] if matches else wait_operation(cloud, cloud.rest('POST', base + '/webApps', {'displayName': APP_NAME}))
    app_id = app['appId']
    support = cloud.command('auth', 'list', '--filter=status:ACTIVE', '--format=value(account)').strip()
    if not support or '\n' in support or '@' not in support:
        raise ValueError('A single signed-in Google account is required')
    provider_url = 'https://identitytoolkit.googleapis.com/admin/v2/projects/' + cloud.project + '/defaultSupportedIdpConfigs/google.com'
    provider = cloud.rest('GET', provider_url, allow_missing=True)
    if not provider or not provider.get('enabled'):
        op = cloud.rest('POST', 'https://firebase.googleapis.com/v1alpha/firebase:provisionFirebaseApp', {
            'parent': 'projects/' + cloud.project, 'appNamespace': app_id, 'webInput': {},
            'firebaseAuthInput': {'googleSigninProviderMode': 'PROVIDER_ENABLED',
                'googleSigninProviderConfig': {'publicDisplayName': 'AI Coach', 'customerSupportEmail': support}}})
        save(ROOT / '.local/verification/firebase-auth-operation.json', op)
        wait_operation(cloud, op)
    config_url = 'https://identitytoolkit.googleapis.com/admin/v2/projects/' + cloud.project + '/config'
    current = cloud.rest('GET', config_url)
    domains = sorted(set(current.get('authorizedDomains', [])) | {ORIGIN.removeprefix('https://'), 'localhost'})
    if domains != sorted(current.get('authorizedDomains', [])):
        cloud.rest('PATCH', config_url + '?updateMask=authorizedDomains', {'authorizedDomains': domains})
    public = cloud.rest('GET', base + '/webApps/' + app_id + '/config')
    save(receipt, {key: public[key] for key in ('apiKey', 'authDomain', 'projectId', 'appId')})
    return {'project': cloud.project, 'firebase_configured': True, 'owner_login_required': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', type=Path, default=ROOT / '.local/firebase-web.json')
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(Cloud(PROJECT), args.receipt)))
    except Exception:
        raise SystemExit('Firebase setup did not complete. Inspect project permissions or the private operation receipt; no credentials were printed.') from None


if __name__ == '__main__':
    main()
