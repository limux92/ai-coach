"""Real RSA signatures and adversarial Firebase claims; only Google HTTP is mocked."""
import asyncio
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from ai_coach_mcp.auth import FIREBASE_JWKS_URL, OwnerTokenVerifier
from test_adapter import settings, key, firebase_token


def verifier(settings, key, status=200):
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid='test-key', use='sig', alg='RS256')
    calls = []
    def fetch(request):
        calls.append(request)
        assert str(request.url) == FIREBASE_JWKS_URL
        return httpx.Response(status, json={'keys': [jwk]})
    return OwnerTokenVerifier(settings, httpx.AsyncClient(transport=httpx.MockTransport(fetch))), calls


@pytest.mark.parametrize('changes', [
    {'iss': 'https://attacker.example/'}, {'aud': 'another-project'}, {'aud': ['test-project']},
    {'sub': 'someone-else'}, {'sub': ''}, {'exp': 1}, {'iat': int(time.time()) + 600},
    {'auth_time': int(time.time()) + 600}, {'auth_time': 0}, {'auth_time': True},
    {'iat': '123'}, {'exp': '9999999999'}, {'email_verified': False}, {'email_verified': 'true'},
    {'firebase': {'sign_in_provider': 'anonymous'}}, {'firebase': {}}, {'firebase': None},
])
def test_invalid_signed_claims_are_denied(settings, key, changes):
    auth, _ = verifier(settings, key)
    assert asyncio.run(auth.verify_token(firebase_token(key, settings, **changes))) is None


def test_signed_owner_passes_and_keys_are_cached(settings, key):
    auth, calls = verifier(settings, key)
    async def run():
        for _ in range(3):
            result = await auth.verify_token(firebase_token(key, settings))
            assert result.subject == settings.owner_subject
            assert result.scopes == ['coach:read']
    asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize('field', ['iss', 'aud', 'sub', 'exp', 'iat', 'auth_time', 'email_verified', 'firebase'])
def test_missing_required_claims_fail(settings, key, field):
    claims = jwt.decode(firebase_token(key, settings), options={'verify_signature': False})
    claims.pop(field)
    signed = jwt.encode(claims, key, algorithm='RS256', headers={'kid': 'test-key'})
    auth, _ = verifier(settings, key)
    assert asyncio.run(auth.verify_token(signed)) is None


def test_wrong_signature_unsigned_and_algorithm_confusion_denied(settings, key):
    auth, _ = verifier(settings, key)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = jwt.decode(firebase_token(key, settings), options={'verify_signature': False})
    values = [firebase_token(other, settings), jwt.encode(claims, '', algorithm='none'),
              jwt.encode(claims, 'fake-shared-secret', algorithm='HS256', headers={'kid': 'test-key'}),
              json.dumps(claims), 'x' * 16385]
    for token in values:
        assert asyncio.run(auth.verify_token(token)) is None


def test_key_outage_fails_closed_without_retry_storm(settings, key):
    auth, calls = verifier(settings, key, status=503)
    async def run():
        for _ in range(4):
            assert await auth.verify_token(firebase_token(key, settings)) is None
    asyncio.run(run())
    assert len(calls) == 1
