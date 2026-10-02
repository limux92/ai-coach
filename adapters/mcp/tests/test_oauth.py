"""End-to-end offline OAuth: real Firebase signatures plus the SDK's PKCE checks."""
import base64
import hashlib
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.testclient import TestClient

from ai_coach_mcp.oauth import COOKIE
from ai_coach_mcp.oauth_store import digest
from test_adapter import settings, key, setup, firebase_token, rpc

VERIFIER = 'v' * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip('=')


@pytest.fixture
def flow(settings, key):
    app, tokens, _ = setup(settings, key, lambda r: pytest.fail('No backend calls expected'))
    with TestClient(app, base_url=settings.oauth_issuer) as client:
        yield client, app.state.oauth, settings, key


def register(client, settings, **changes):
    body = {'client_name': 'Test chat', 'redirect_uris': list(settings.oauth_redirect_uris),
        'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code'],
        'token_endpoint_auth_method': 'none', 'scope': 'coach:read', **changes}
    return client.post('/register', json=body)


def start(flow, *, changes=None):
    client, provider, settings, key = flow
    registration = register(client, settings)
    assert registration.status_code == 201, registration.text
    client_id = registration.json()['client_id']
    params = {'client_id': client_id, 'redirect_uri': settings.oauth_redirect_uris[0],
        'response_type': 'code', 'code_challenge': CHALLENGE, 'code_challenge_method': 'S256',
        'scope': 'coach:read', 'resource': settings.public_url, 'state': 'client-csrf-state', **(changes or {})}
    result = client.get('/authorize', params=params, follow_redirects=False)
    return client_id, result


def consent(flow):
    client, provider, settings, key = flow
    client_id, result = start(flow)
    assert result.status_code == 302
    result = client.get(result.headers['location'], follow_redirects=False)
    assert result.status_code == 302
    query = urlsplit(result.headers['location']).query
    endpoint = '/oauth/consent?' + query
    assert client.get(endpoint).json()['redirect_host'] == 'chatgpt.com'
    return client_id, endpoint


def approve(flow, client_id, endpoint):
    client, provider, settings, key = flow
    result = client.post(endpoint, headers={'Authorization': 'Bearer ' + firebase_token(key, settings),
                                          'Origin': settings.oauth_issuer})
    assert result.status_code == 200, result.text
    query = parse_qs(urlsplit(result.json()['redirect']).query)
    assert query['state'] == ['client-csrf-state']
    assert query['iss'] == [settings.oauth_issuer]
    return {'client_id': client_id, 'grant_type': 'authorization_code', 'code': query['code'][0],
            'code_verifier': VERIFIER, 'redirect_uri': settings.oauth_redirect_uris[0], 'resource': settings.public_url}


def exchange(flow):
    client_id, endpoint = consent(flow)
    data = approve(flow, client_id, endpoint)
    response = flow[0].post('/token', data=data)
    assert response.status_code == 200, response.text
    return data, response.json()


def test_full_pkce_flow_rotation_and_replay_revoke_family(flow):
    client, provider, settings, key = flow
    data, tokens = exchange(flow)
    assert rpc(client, tokens['access_token']).status_code == 200
    assert client.post('/token', data=data).status_code == 400
    refresh = {'client_id': data['client_id'], 'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token']}
    result = client.post('/token', data=refresh)
    assert result.status_code == 200
    rotated = result.json()
    assert rotated['refresh_token'] != tokens['refresh_token']
    assert rpc(client, rotated['access_token']).status_code == 200
    assert client.post('/token', data=refresh).status_code == 400
    assert rpc(client, rotated['access_token']).status_code == 401
    assert rpc(client, tokens['access_token']).status_code == 401
    assert client.post('/token', data={**refresh, 'refresh_token': rotated['refresh_token']}).status_code == 400
    # Bearer values are never persisted in the database.
    assert tokens['access_token'] not in str(provider.store.rows)
    assert tokens['refresh_token'] not in str(provider.store.rows)


@pytest.mark.parametrize('changes', [{'code_verifier': 'wrong'}, {'redirect_uri': 'https://evil.example/cb'},
    {'client_id': 'unknown'}, {'resource': 'https://other.example/mcp'}])
def test_code_binding_cannot_be_changed(flow, changes):
    client_id, endpoint = consent(flow)
    data = approve(flow, client_id, endpoint)
    assert flow[0].post('/token', data={**data, **changes}).status_code in (400, 401)
    assert flow[0].post('/token', data=data).status_code == 200


@pytest.mark.parametrize('changes', [{'sub': 'different-user'}, {'iss': 'https://evil.example'},
    {'email_verified': False}, {'exp': 1}])
def test_consent_rejects_other_users_and_invalid_tokens(flow, changes):
    client, _, settings, key = flow
    _, endpoint = consent(flow)
    response = client.post(endpoint, headers={'Origin': settings.oauth_issuer,
        'Authorization': 'Bearer ' + firebase_token(key, settings, **changes)})
    assert response.status_code == 401


def test_consent_requires_browser_binding_origin_and_one_time_use(flow):
    client, _, settings, key = flow
    client_id, endpoint = consent(flow)
    headers = {'Authorization': 'Bearer ' + firebase_token(key, settings), 'Origin': settings.oauth_issuer}
    assert client.post(endpoint, headers={**headers, 'Origin': 'https://evil.example'}).status_code == 403
    cookie = client.cookies.get(COOKIE)
    client.cookies.clear()
    assert client.post(endpoint, headers=headers).status_code == 400
    client.cookies.set(COOKIE, cookie)
    approve(flow, client_id, endpoint)
    assert client.post(endpoint, headers=headers).status_code == 400


@pytest.mark.parametrize('changes', [{'redirect_uris': ['https://evil.example/cb']}, {'scope': 'coach:write'},
    {'grant_types': ['client_credentials']}, {'redirect_uris': ['https://chatgpt.com/connector_platform_oauth_redirect?evil=1']}])
def test_registration_restricted_to_exact_approved_clients(flow, changes):
    assert register(flow[0], flow[2], **changes).status_code == 400


def test_metadata_and_revocation(flow):
    client, provider, settings, _ = flow
    metadata = client.get('/.well-known/oauth-authorization-server').json()
    assert metadata['issuer'] == settings.oauth_issuer
    assert metadata['authorization_response_iss_parameter_supported'] is True
    assert metadata['code_challenge_methods_supported'] == ['S256']
    data, tokens = exchange(flow)
    response = client.post('/revoke', data={'client_id': data['client_id'], 'token': tokens['refresh_token'], 'token_type_hint': 'refresh_token'})
    assert response.status_code == 200
    assert rpc(client, tokens['access_token']).status_code == 401


@pytest.mark.parametrize('changes', [
    {'scope': 'coach:write'}, {'response_type': 'token'},
    {'code_challenge': 'invalid'}, {'code_challenge_method': 'plain'},
])
def test_authorization_error_callbacks_identify_exact_issuer(flow, changes):
    _, response = start(flow, changes=changes)
    assert response.status_code == 302
    target = urlsplit(response.headers['location'])
    allowed = urlsplit(flow[2].oauth_redirect_uris[0])
    assert (target.scheme, target.netloc, target.path) == (allowed.scheme, allowed.netloc, allowed.path)
    query = parse_qs(target.query)
    assert 'error' in query and 'code' not in query
    assert query['iss'] == [flow[2].oauth_issuer]
    assert query['state'] == ['client-csrf-state']
    assert response.headers['cache-control'] == 'no-store'


def test_unregistered_callback_never_receives_authorization_error(flow):
    _, response = start(flow, changes={'redirect_uri': 'https://evil.example/cb'})
    assert response.status_code == 400
    assert 'location' not in response.headers


def test_limits_and_duplicates(flow):
    client = flow[0]
    assert client.post('/token', content='x' * 32769).status_code == 413
    assert client.get('/authorize?client_id=a&client_id=b').status_code == 400
    assert client.post('/token', data='client_id=a&client_id=b', headers={'Content-Type': 'application/x-www-form-urlencoded'}).status_code == 400
    for _ in range(20):
        register(client, flow[2])
    assert register(client, flow[2]).status_code == 429


def test_expired_code_and_expired_session_denied(flow):
    client, provider, settings, _ = flow
    client_id, endpoint = consent(flow)
    data = approve(flow, client_id, endpoint)
    provider.store.rows['codes', digest(data['code'])]['expires_at'] = time.time() - 1
    assert client.post('/token', data=data).status_code == 400
    _, tokens = exchange(flow)
    provider.store.rows['access', digest(tokens['access_token'])]['expires_at'] = time.time() - 1
    assert rpc(client, tokens['access_token']).status_code == 401
