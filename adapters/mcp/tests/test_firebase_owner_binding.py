"""Owner receipt requires a recent signed login and an independent email match."""
import importlib.util
import json
from pathlib import Path
import sys
import time

import httpx
import jwt
import pytest

from test_adapter import settings, key, firebase_token

INFRA = Path(__file__).resolve().parents[3] / 'infra'
sys.path.insert(0, str(INFRA))
import bind_firebase_owner as bind


@pytest.fixture
def google_keys(monkeypatch, key):
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid='test-key', use='sig', alg='RS256')
    original = httpx.AsyncClient
    monkeypatch.setattr(bind.httpx, 'AsyncClient', lambda **kwargs: original(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'keys': [jwk]})), **kwargs))


def test_owner_binding_uses_signed_uid_not_email_lookup(settings, key, google_keys):
    result = bind.bind_claims(firebase_token(key, settings, sub='new-firebase-uid', email='owner@example.test'),
                              'owner@example.test', settings)
    assert result.owner_subject == 'new-firebase-uid'


@pytest.mark.parametrize('changes', [{'email': 'other@example.test'}, {'email_verified': False},
    {'auth_time': int(time.time()) - 601}, {'iss': 'https://evil.example'}, {'aud': 'another-project'}])
def test_owner_binding_rejects_wrong_email_stale_or_invalid_identity(settings, key, google_keys, changes):
    with pytest.raises(ValueError):
        bind.bind_claims(firebase_token(key, settings, **{'email': 'owner@example.test', **changes}),
                         'owner@example.test', settings)
