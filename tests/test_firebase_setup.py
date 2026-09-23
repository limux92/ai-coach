"""Firebase migration setup uses exact project/domain boundaries and private receipts."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'infra'))
import configure_firebase as setup
import provision_auth_store as store


class FirebaseCloud:
    project = setup.PROJECT
    def __init__(self):
        self.writes = []
        self.enabled = True
        self.apps = [{'displayName': setup.APP_NAME, 'appId': 'test-app'}]
    def command(self, *args):
        if args[:2] == ('auth', 'list'):
            return 'owner@example.test'
        assert args[:2] == ('services', 'enable')
        return ''
    def rest(self, method, url, body=None, **kwargs):
        if method != 'GET':
            self.writes.append((method, url, body))
            return {}
        if url.endswith('/webApps'):
            return {'apps': self.apps}
        if url.endswith('/webApps/test-app/config'):
            return {'apiKey': 'public-config-key', 'authDomain': self.project + '.firebaseapp.com',
                    'projectId': self.project, 'appId': 'test-app', 'unneeded': 'not-copied'}
        if url.endswith('/defaultSupportedIdpConfigs/google.com'):
            return {'enabled': self.enabled}
        if url.endswith('/config'):
            return {'authorizedDomains': ['existing.example']}
        return {'projectId': self.project}


def test_existing_firebase_app_reused_and_other_domains_preserved(tmp_path):
    cloud = FirebaseCloud()
    receipt = tmp_path / 'web.json'
    assert setup.prepare(cloud, receipt)['owner_login_required'] is True
    assert set(json.loads(receipt.read_text())) == {'apiKey', 'authDomain', 'projectId', 'appId'}
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert len(cloud.writes) == 1
    method, url, data = cloud.writes[0]
    assert method == 'PATCH' and url.endswith('?updateMask=authorizedDomains')
    assert set(data['authorizedDomains']) == {'existing.example', 'localhost', setup.ORIGIN.removeprefix('https://')}


def test_wrong_project_and_ambiguous_apps_stop_before_mutations(tmp_path):
    cloud = FirebaseCloud()
    cloud.project = 'another-project'
    with pytest.raises(ValueError):
        setup.prepare(cloud, tmp_path / 'web.json')
    cloud.project = setup.PROJECT
    cloud.apps *= 2
    with pytest.raises(ValueError):
        setup.prepare(cloud, tmp_path / 'web.json')
    assert cloud.writes == []


def test_auth_store_rejects_training_database_access():
    class Cloud:
        project = store.PROJECT
        def json(self, *args, **kwargs):
            if args[0] == 'firestore':
                return {'type': 'FIRESTORE_NATIVE', 'locationId': store.REGION}
            if args[0] == 'iam':
                return {'exists': True}
            return {'bindings': [{'role': 'roles/datastore.user', 'members': ['serviceAccount:' + store.RUNTIME]}]}
        def command(self, *args):
            pytest.fail('Unexpected cloud mutation')
    with pytest.raises(ValueError, match='Unexpected runtime IAM grant'):
        store.provision(Cloud())
