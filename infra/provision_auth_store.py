#!/usr/bin/env python3
"""Create only the isolated OAuth state store and its restricted runtime grant."""
import json
from pathlib import Path

from cloud import Cloud
from deploy_chat import PROJECT, REGION, RUNTIME, auth_condition


def provision(cloud):
    if cloud.project != PROJECT:
        raise ValueError('Unexpected project')
    database = cloud.json('firestore', 'databases', 'describe', '--database=ai-coach-auth', allow_missing=True)
    if database is None:
        cloud.command('firestore', 'databases', 'create', '--database=ai-coach-auth',
                      '--location=' + REGION, '--type=firestore-native', '--delete-protection')
    elif database.get('type') != 'FIRESTORE_NATIVE' or database.get('locationId') != REGION:
        raise ValueError('Existing auth database has incompatible configuration')
    if cloud.json('iam', 'service-accounts', 'describe', RUNTIME, allow_missing=True) is None:
        cloud.command('iam', 'service-accounts', 'create', 'ai-coach-chat', '--display-name=AI Coach chat gateway')
    policy = cloud.json('projects', 'get-iam-policy', PROJECT)
    member = 'serviceAccount:' + RUNTIME
    grants = [b for b in policy.get('bindings', []) if member in b.get('members', [])]
    if any(b.get('role') != 'roles/datastore.user' or b.get('condition', {}).get('expression') != auth_condition() for b in grants):
        raise ValueError('Unexpected runtime IAM grant; review it before continuing')
    if not grants:
        cloud.command('projects', 'add-iam-policy-binding', PROJECT, '--member=' + member,
                      '--role=roles/datastore.user', '--condition=expression=' + auth_condition() + ',title=ai-coach-auth-only')
    # No browser can access OAuth records, even after Firebase sign-in.
    base = 'https://firebaserules.googleapis.com/v1/projects/' + PROJECT
    rules = (Path(__file__).parent / 'firestore.rules').read_text()
    ruleset = cloud.rest('POST', base + '/rulesets', {'source': {'files': [{'name': 'firestore.rules', 'content': rules}]}})
    name = 'projects/' + PROJECT + '/releases/cloud.firestore/ai-coach-auth'
    release = cloud.rest('GET', 'https://firebaserules.googleapis.com/v1/' + name, allow_missing=True)
    body = {'name': name, 'rulesetName': ruleset['name']}
    if release:
        cloud.rest('PATCH', 'https://firebaserules.googleapis.com/v1/' + name, {'release': body})
    else:
        cloud.rest('POST', base + '/releases', body)
    for collection in ('clients', 'pending', 'consents', 'codes', 'access', 'refresh', 'grants'):
        cloud.command('firestore', 'fields', 'ttls', 'update', 'delete_after', '--database=ai-coach-auth',
                      '--collection-group=' + collection, '--enable-ttl', '--async')
    return {'auth_database': 'ai-coach-auth', 'training_database_unchanged': True}


if __name__ == '__main__':
    try:
        print(json.dumps(provision(Cloud(PROJECT))))
    except Exception:
        raise SystemExit('OAuth state provisioning failed; inspect cloud permissions without logging credentials.') from None
