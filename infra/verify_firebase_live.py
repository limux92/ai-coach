"""Exercise live Firebase dashboard + OAuth PKCE, tools, refresh and revocation.

Called by bind_firebase_owner.py --verify-live. No tokens or training payloads are
written; all newly issued test tokens are revoked before returning.
"""
import base64
import hashlib
import json
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlsplit

import httpx

from configure_firebase import save


def require(condition):
    if not condition:
        raise ValueError('Live authentication verification failed')


def verify(token, settings, report_path):
    origin = settings.oauth_issuer
    with httpx.Client(timeout=45, follow_redirects=False, trust_env=False) as client:
        require(client.get(settings.backend_url + '/v1/status').status_code == 403)
        require(client.get(origin + '/dashboard/api/status').status_code == 401)
        require(client.get(origin + '/dashboard/api/status', headers={'Authorization': 'Bearer ' + token}).status_code == 200)
        require(client.get(origin + '/dashboard/').status_code == 200)
        reg = client.post(origin + '/register', json={'client_name': 'AI Coach migration verification',
            'redirect_uris': [settings.oauth_redirect_uris[0]], 'token_endpoint_auth_method': 'none',
            'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code'], 'scope': 'coach:read'})
        require(reg.status_code == 201)
        client_id = reg.json()['client_id']
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        state = secrets.token_urlsafe(32)
        authorize = client.get(origin + '/authorize', params={'client_id': client_id,
            'redirect_uri': settings.oauth_redirect_uris[0], 'response_type': 'code', 'scope': 'coach:read',
            'code_challenge': challenge, 'code_challenge_method': 'S256', 'state': state, 'resource': settings.public_url})
        require(authorize.status_code == 302 and authorize.headers['location'].startswith(origin + '/oauth/start?'))
        start = client.get(authorize.headers['location'])
        require(start.status_code == 302 and start.headers['location'].startswith('/dashboard/connect?'))
        consent = origin + '/oauth/consent?' + urlsplit(start.headers['location']).query
        require(client.get(consent).status_code == 200)
        approval = client.post(consent, headers={'Authorization': 'Bearer ' + token, 'Origin': origin})
        require(approval.status_code == 200)
        query = parse_qs(urlsplit(approval.json()['redirect']).query)
        require(query['state'] == [state] and query['iss'] == [origin])
        data = {'client_id': client_id, 'grant_type': 'authorization_code', 'code': query['code'][0],
            'code_verifier': verifier, 'redirect_uri': settings.oauth_redirect_uris[0], 'resource': settings.public_url}
        exchange = client.post(origin + '/token', data=data)
        require(exchange.status_code == 200)
        tokens = exchange.json()
        try:
            require(client.post(origin + '/token', data=data).status_code == 400)
            def rpc(access, method, params):
                return client.post(settings.public_url, headers={'Authorization': 'Bearer ' + access,
                    'Accept': 'application/json, text/event-stream', 'Mcp-Protocol-Version': '2025-11-25'},
                    json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
            listed = rpc(tokens['access_token'], 'tools/list', {})
            require(listed.status_code == 200)
            expected = {'get_coach_context', 'get_training_summary', 'list_completed_workouts', 'get_workout_details',
                        'list_planned_workouts', 'list_wellness', 'get_workout_samples'}
            require({tool['name'] for tool in listed.json()['result']['tools']} == expected)
            context = rpc(tokens['access_token'], 'tools/call', {'name': 'get_coach_context', 'arguments': {}})
            require(context.status_code == 200 and context.json()['result'].get('isError') is not True)
            require(isinstance(json.loads(context.json()['result']['content'][0]['text']), dict))
            refresh_data = {'client_id': client_id, 'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token'],
                            'resource': settings.public_url}
            refreshed = client.post(origin + '/token', data=refresh_data)
            require(refreshed.status_code == 200)
            rotated = refreshed.json()
            require(rotated['refresh_token'] != tokens['refresh_token'])
            require(rpc(rotated['access_token'], 'tools/list', {}).status_code == 200)
            require(client.post(origin + '/token', data=refresh_data).status_code == 400)
            require(rpc(rotated['access_token'], 'tools/list', {}).status_code == 401)
            require(rpc(tokens['access_token'], 'tools/list', {}).status_code == 401)
        finally:
            client.post(origin + '/revoke', data={'client_id': client_id, 'token': tokens['refresh_token'], 'token_type_hint': 'refresh_token'})
    report = {'firebase_dashboard_owner_read': True, 'anonymous_backend_denied': True,
        'oauth_pkce_exchange': True, 'seven_tools': True, 'training_context_read': True,
        'single_use_code': True, 'refresh_rotation': True, 'refresh_replay_revokes_grant': True,
        'hosted_chat_reconnection_verified': False}
    save(Path(report_path), report)
    return report
