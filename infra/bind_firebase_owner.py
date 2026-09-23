#!/usr/bin/env python3
"""Verify a real owner Google login on loopback and save a token-free UID receipt.

Run with the adapter environment. Tokens remain in memory and never enter the
receipt, console, URL, or Git. The owner email must match the signed-in operator.
"""
import argparse
import asyncio
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import secrets
import sys
import time
import webbrowser

import httpx
import jwt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'adapters/mcp/src'))
from ai_coach_mcp.auth import OwnerTokenVerifier
from ai_coach_mcp.config import Settings
from cloud import Cloud
from configure_firebase import PROJECT, ORIGIN, save


def bind_claims(token, expected_email, settings):
    # The unverified subject is used only to construct the verifier. Both the
    # signature and independently configured owner email must then match.
    claims = jwt.decode(token, options={'verify_signature': False})
    candidate = replace(settings, owner_subject=claims.get('sub', ''))
    async def verify():
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
            return await OwnerTokenVerifier(candidate, client).verify_token(token)
    if asyncio.run(verify()) is None or claims.get('email', '').casefold() != expected_email.casefold():
        raise ValueError('Owner login did not match')
    if time.time() - claims['auth_time'] > 300:
        raise ValueError('A recent interactive login is required')
    return candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--web-config', type=Path, default=ROOT / '.local/firebase-web.json')
    parser.add_argument('--output', type=Path, default=ROOT / '.local/firebase-auth.json')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--callback', action='append', default=[])
    parser.add_argument('--verify-live', action='store_true', help='Verify the deployed flow and revoke test tokens; do not change the owner receipt')
    args = parser.parse_args()
    config = json.loads(args.web_config.read_text())
    email = Cloud(PROJECT).command('auth', 'list', '--filter=status:ACTIVE', '--format=value(account)').strip()
    if not email or '\n' in email:
        raise SystemExit('A single signed-in operator is required')
    settings = Settings(backend_url='https://ai-coach-sync-wws5xmx2wa-lz.a.run.app',
        backend_allowed_host='ai-coach-sync-wws5xmx2wa-lz.a.run.app', public_url=ORIGIN + '/mcp',
        firebase_project_id=config['projectId'], firebase_api_key=config['apiKey'], owner_subject='pending-owner-binding',
        oauth_redirect_uris=tuple(args.callback or ['https://chatgpt.com/connector_platform_oauth_redirect']))
    nonce = secrets.token_urlsafe(32)
    origin = f'http://localhost:{args.port}'
    finished = False
    # Escaping '<' keeps public configuration from ever breaking out of script.
    public_json = json.dumps(config).replace('<', '\\u003c')
    page = '''<!doctype html><meta charset="utf-8"><title>AI Coach owner sign-in</title>
<style>body{font:18px system-ui;max-width:620px;margin:12vh auto;padding:30px}button{font:inherit;padding:14px}p{line-height:1.5}</style>
<h1>Set up your AI Coach login</h1><p>Sign in with your Google account to link Firebase to your existing private training space.</p>
<button id="login">Continue with Google</button><p id="status"></p>
<script type="module">
import {initializeApp} from 'https://www.gstatic.com/firebasejs/12.19.0/firebase-app.js';
import {getAuth,GoogleAuthProvider,signInWithPopup,inMemoryPersistence,setPersistence,signOut} from 'https://www.gstatic.com/firebasejs/12.19.0/firebase-auth.js';
const auth=getAuth(initializeApp(CONFIG)); await setPersistence(auth,inMemoryPersistence);
document.querySelector('#login').onclick=async()=>{
 const button=document.querySelector('#login');button.disabled=true;
 try {const provider=new GoogleAuthProvider();provider.setCustomParameters({prompt:'select_account'});
 const {user}=await signInWithPopup(auth,provider);
 const result=await fetch('/bind',{method:'POST',headers:{Authorization:'Bearer '+await user.getIdToken(),'X-Setup-Nonce':NONCE}});
 if(!result.ok)throw new Error();
 await signOut(auth);document.querySelector('#status').textContent='Owner verified. You can close this page and return to Codex.';
 }catch{document.querySelector('#status').textContent='Sign-in was not accepted. Use the same Google account as the project CLI login and try again.';button.disabled=false;}
};</script>'''.replace('CONFIG', public_json).replace('NONCE', json.dumps(nonce))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def reply(self, code, body, content_type='text/plain'):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Frame-Options', 'DENY')
            self.end_headers()
            self.wfile.write(body.encode())
        def do_GET(self):
            if self.path != '/' + nonce or self.headers.get('Host') != f'localhost:{args.port}':
                return self.reply(404, 'Not found')
            self.reply(200, page, 'text/html; charset=utf-8')
        def do_POST(self):
            nonlocal finished
            if (self.path != '/bind' or self.headers.get('Origin') != origin
                    or self.headers.get('Host') != f'localhost:{args.port}'
                    or not secrets.compare_digest(self.headers.get('X-Setup-Nonce', ''), nonce)):
                return self.reply(403, 'Denied')
            try:
                header = self.headers.get('Authorization', '')
                if not header.startswith('Bearer ') or len(header) > 16400:
                    raise ValueError('Invalid bearer')
                owner = bind_claims(header[7:], email, settings)
                if args.verify_live:
                    from deploy_chat import load_settings
                    from verify_firebase_live import verify
                    deployed = load_settings(args.output)
                    if deployed.owner_subject != owner.owner_subject:
                        raise ValueError('Existing owner binding did not match')
                    verify(header[7:], deployed, ROOT / '.local/verification/firebase-live.json')
                else:
                    save(args.output, {'firebase_project_id': owner.firebase_project_id, 'firebase_api_key': owner.firebase_api_key,
                        'owner_subject': owner.owner_subject, 'oauth_redirect_uris': owner.oauth_redirect_uris})
            except Exception:
                return self.reply(401, 'Owner verification failed')
            self.reply(200, 'Owner verified')
            finished = True
    with HTTPServer(('127.0.0.1', args.port), Handler) as server:
        server.timeout = 1
        webbrowser.open(origin + '/' + nonce)
        print('Google sign-in page opened. Waiting for verified owner login.', flush=True)
        deadline = time.monotonic() + 900
        while not finished and time.monotonic() < deadline:
            server.handle_request()
    if not finished:
        raise SystemExit('Owner sign-in timed out; rerun when ready.')
    print('Live Firebase/OAuth verification passed; test grants revoked.' if args.verify_live else 'Verified Firebase owner receipt saved; no tokens were persisted.')


if __name__ == '__main__':
    main()
