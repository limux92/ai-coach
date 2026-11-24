"""MCP OAuth authorization-code/PKCE service with explicit Firebase owner consent.

The SDK owns protocol validation; this provider owns identity binding, durable
single-use codes, scoped opaque tokens and refresh rotation/replay revocation.
"""
import re
import secrets
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizeError,
    RefreshToken, RegistrationError, TokenError, construct_redirect_uri)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from mcp.server.auth.middleware.client_auth import ClientAuthenticator, AuthenticationError
from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.routes import build_metadata
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

from .oauth_store import digest

COOKIE = "__Host-coach-consent"
ACCESS_SECONDS = 900
GRANT_SECONDS = 30 * 86400
SAFE_TOKEN = re.compile(r"[A-Za-z0-9_-]{43,128}")


class OAuthProvider:
    def __init__(self, settings, store, firebase):
        self.settings, self.store, self.firebase = settings, store, firebase

    async def get_client(self, client_id):
        if not isinstance(client_id, str) or len(client_id) > 200:
            return None
        row = await self.store.get("clients", client_id)
        return OAuthClientInformationFull.model_validate(row["client"]) if row else None

    async def register_client(self, client_info):
        redirects = [str(uri) for uri in client_info.redirect_uris or []]
        if not redirects or any(uri not in self.settings.oauth_redirect_uris for uri in redirects):
            raise RegistrationError("invalid_redirect_uri", "Use an operator-approved exact callback URL")
        if (set(client_info.grant_types) - {"authorization_code", "refresh_token"}
                or set(client_info.response_types) != {"code"}
                or client_info.scope != self.settings.read_scope
                or len(client_info.client_name or "") > 120):
            raise RegistrationError("invalid_client_metadata", "Only read-only authorization-code clients are supported")
        await self.store.put("clients", client_info.client_id,
            {"client": client_info.model_dump(mode="json"), "expires_at": time.time() + 365 * 86400})

    async def authorize(self, client, params):
        if params.resource not in (None, self.settings.public_url):
            raise AuthorizeError("invalid_target", "Unknown resource")
        if params.scopes != [self.settings.read_scope]:
            raise AuthorizeError("invalid_scope", "coach:read is required")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.code_challenge):
            raise AuthorizeError("invalid_request", "A valid S256 challenge is required")
        if params.state is not None and len(params.state) > 2048:
            raise AuthorizeError("invalid_request", "State is too long")
        pending = secrets.token_urlsafe(32)
        await self.store.put("pending", pending, {"client_id": client.client_id,
            "params": params.model_dump(mode="json"), "expires_at": time.time() + 600})
        return self.settings.oauth_issuer + "/oauth/start?" + urlencode({"request": pending})

    async def load_authorization_code(self, client, authorization_code):
        if not SAFE_TOKEN.fullmatch(authorization_code):
            return None
        row = await self.store.get("codes", authorization_code)
        if not row or row.get("used") or row["client_id"] != client.client_id:
            return None
        return AuthorizationCode(code=authorization_code, **{k: v for k, v in row.items()
            if k in AuthorizationCode.model_fields and k != "code"})

    def _tokens(self, client_id, subject, grant, deadline):
        now = int(time.time())
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        common = {"client_id": client_id, "subject": subject, "scopes": [self.settings.read_scope],
                  "resource": self.settings.public_url, "grant": grant}
        records = [("access", access, {**common, "expires_at": min(now + ACCESS_SECONDS, deadline)}),
                   ("refresh", refresh, {**common, "expires_at": deadline})]
        result = OAuthToken(access_token=access, token_type="Bearer", expires_in=min(ACCESS_SECONDS, deadline - now),
                            refresh_token=refresh, scope=self.settings.read_scope)
        return result, records

    async def exchange_authorization_code(self, client, authorization_code):
        if authorization_code.subject != self.settings.owner_subject:
            raise TokenError("invalid_grant", "Owner authorization required")
        grant = secrets.token_urlsafe(32)
        deadline = int(time.time()) + GRANT_SECONDS
        result, records = self._tokens(client.client_id, authorization_code.subject, grant, deadline)
        records.append(("grants", grant, {"expires_at": deadline, "revoked": False}))
        if not await self.store.transition("codes", authorization_code.code, records):
            raise TokenError("invalid_grant", "Authorization code already used or expired")
        return result

    async def load_refresh_token(self, client, refresh_token):
        if not SAFE_TOKEN.fullmatch(refresh_token):
            return None
        row = await self.store.get("refresh", refresh_token)
        if not row or row["client_id"] != client.client_id or row["subject"] != self.settings.owner_subject:
            return None
        return RefreshToken(token=refresh_token, **{k: v for k, v in row.items() if k in RefreshToken.model_fields})

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        row = await self.store.get("refresh", refresh_token.token)
        if not row or scopes != [self.settings.read_scope] or row["client_id"] != client.client_id:
            raise TokenError("invalid_grant", "Invalid refresh grant")
        result, records = self._tokens(client.client_id, row["subject"], row["grant"], row["expires_at"])
        if not await self.store.transition("refresh", refresh_token.token, records, revoke_replay=True):
            raise TokenError("invalid_grant", "Refresh grant expired, revoked or reused")
        return result

    async def load_access_token(self, token):
        if not isinstance(token, str) or not SAFE_TOKEN.fullmatch(token):
            return None
        row = await self.store.get("access", token)
        if (not row or row["subject"] != self.settings.owner_subject
                or row["resource"] != self.settings.public_url or row["scopes"] != [self.settings.read_scope]):
            return None
        grant = await self.store.get("grants", row["grant"])
        if not grant or grant.get("revoked"):
            return None
        return AccessToken(token=token, claims={"iss": self.settings.oauth_issuer},
            **{k: v for k, v in row.items() if k in AccessToken.model_fields})

    async def revoke_token(self, token):
        kind = "refresh" if isinstance(token, RefreshToken) else "access"
        row = await self.store.get(kind, token.token)
        if row:
            await self.store.revoke(row["grant"])

    async def exchange_identity_assertion(self, client, params):
        raise TokenError("unsupported_grant_type", "Identity assertion grants are not supported")

    def routes(self):
        async def metadata(request):
            # RFC 9207 enables ChatGPT's stable, exactly allowlisted callback.
            # Success responses already include iss in the consent handler below.
            value = build_metadata(AnyHttpUrl(self.settings.oauth_issuer), None,
                ClientRegistrationOptions(enabled=True, valid_scopes=[self.settings.read_scope],
                                          default_scopes=[self.settings.read_scope]),
                RevocationOptions(enabled=True)).model_dump(mode="json", exclude_none=True)
            # AnyHttpUrl can append a slash; RFC 9207 requires exact issuer equality.
            return JSONResponse({**value, "issuer": self.settings.oauth_issuer,
                                 "authorization_response_iss_parameter_supported": True})

        async def authorize(request):
            # Keep SDK client/redirect/PKCE validation. Its error redirects need
            # the same issuer identification as successful consent responses.
            response = await AuthorizationHandler(self).handle(request)
            if response.status_code == 302 and "location" in response.headers:
                target = urlsplit(response.headers["location"])
                query = parse_qsl(target.query, keep_blank_values=True)
                if any(name == "error" for name, _ in query):
                    query = [(name, value) for name, value in query if name != "iss"]
                    query.append(("iss", self.settings.oauth_issuer))
                    response.headers["location"] = target._replace(query=urlencode(query)).geturl()
            return response

        async def revoke(request):
            # SDK 2.2.0 requires client_secret even for public clients in its
            # revocation form. Authenticate with the SDK, then apply RFC 7009.
            try:
                client = await ClientAuthenticator(self).authenticate_request(request)
            except AuthenticationError:
                return JSONResponse({"error": "invalid_client"}, status_code=401)
            form = await request.form()
            value = form.get("token")
            if not isinstance(value, str) or len(value) > 16384:
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            token = await self.load_access_token(value) or await self.load_refresh_token(client, value)
            if token and token.client_id == client.client_id:
                await self.revoke_token(token)
            return JSONResponse({})

        async def start(request):
            pending = request.query_params.get("request", "")
            if not SAFE_TOKEN.fullmatch(pending):
                return failure()
            row = await self.store.get("pending", pending)
            if not row:
                return failure()
            consent, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            record = {**row, "csrf": digest(csrf)}
            if not await self.store.transition("pending", pending, [("consents", consent, record)]):
                return failure()
            response = RedirectResponse("/dashboard/connect?" + urlencode({"request": consent}), status_code=302)
            response.set_cookie(COOKIE, csrf, max_age=600, secure=True, httponly=True, samesite="lax", path="/")
            return response

        async def consent(request):
            consent_id = request.query_params.get("request", "")
            if not SAFE_TOKEN.fullmatch(consent_id):
                return failure()
            row = await self.store.get("consents", consent_id)
            if (not row or row.get("used") or not secrets.compare_digest(row["csrf"], digest(request.cookies.get(COOKIE, "")))):
                return failure()
            if request.method == "GET":
                client = await self.get_client(row["client_id"])
                if not client:
                    return failure()
                return JSONResponse({"client_name": client.client_name or "Chat client", "scope": self.settings.read_scope,
                                     "redirect_host": urlsplit(row["params"]["redirect_uri"]).netloc})
            if request.headers.get("origin") != self.settings.oauth_issuer:
                return failure(403)
            match = re.fullmatch(r"Bearer ([^\s,]{1,16384})", request.headers.get("authorization", ""))
            access = await self.firebase.verify_token(match[1]) if match else None
            if access is None:
                return failure(401)
            params = row["params"]
            code = secrets.token_urlsafe(32)
            record = {"client_id": row["client_id"], "scopes": [self.settings.read_scope],
                "expires_at": time.time() + 60, "code_challenge": params["code_challenge"],
                "redirect_uri": params["redirect_uri"], "redirect_uri_provided_explicitly": params["redirect_uri_provided_explicitly"],
                "resource": self.settings.public_url, "subject": access.subject}
            if not await self.store.transition("consents", consent_id, [("codes", code, record)]):
                return failure()
            response = JSONResponse({"redirect": construct_redirect_uri(params["redirect_uri"],
                code=code, state=params["state"], iss=self.settings.oauth_issuer)})
            response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
            return response
        return [Route("/.well-known/oauth-authorization-server", metadata),
                Route("/authorize", authorize, methods=["GET", "POST"]),
                Route("/oauth/start", start), Route("/oauth/consent", consent, methods=["GET", "POST"]),
                Route("/revoke", revoke, methods=["POST"])]


def failure(status=400):
    return JSONResponse({"error": "Login request is invalid, expired or unauthorized. Start again from your chat client."}, status_code=status)
