"""Deployment-only configuration. No tool accepts hosts or credentials."""
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


def https_url(value: str, field: str, *, origin_only: bool = False) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.port not in (None, 443)
            or any(char.isspace() for char in value)
            or (origin_only and parsed.path not in ("", "/"))):
        raise ValueError(f"{field} must be a canonical HTTPS URL")
    return value.rstrip("/") if origin_only else value


@dataclass(frozen=True)
class Settings:
    backend_url: str
    backend_allowed_host: str
    public_url: str
    oauth_issuer: str
    oauth_jwks_url: str
    owner_subject: str
    read_scope: str = "coach:read"
    dashboard_client_id: str = ""

    def __post_init__(self):
        backend = https_url(self.backend_url, "BACKEND_URL", origin_only=True)
        host = urlsplit(backend).hostname
        if (not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.(?:[a-z0-9-]+\.)?run\.app", host or "")
                or host != self.backend_allowed_host):
            raise ValueError("BACKEND_URL must match the configured Cloud Run hostname exactly")
        object.__setattr__(self, "backend_url", backend)
        https_url(self.public_url, "MCP_PUBLIC_URL")
        if urlsplit(self.public_url).path != "/mcp":
            raise ValueError("MCP_PUBLIC_URL must end with /mcp")
        https_url(self.oauth_issuer, "OAUTH_ISSUER")
        https_url(self.oauth_jwks_url, "OAUTH_JWKS_URL")
        if urlsplit(self.oauth_issuer).netloc != urlsplit(self.oauth_jwks_url).netloc:
            raise ValueError("JWKS URL must use the configured issuer's HTTPS origin")
        if not self.owner_subject or len(self.owner_subject) > 256:
            raise ValueError("OAUTH_OWNER_SUBJECT is required")
        if not isinstance(self.dashboard_client_id, str) or (self.dashboard_client_id and not re.fullmatch(r"[a-zA-Z0-9_-]{1,200}", self.dashboard_client_id)):
            raise ValueError("DASHBOARD_CLIENT_ID must be a public OAuth client identifier")
        if self.read_scope != "coach:read":
            raise ValueError("This adapter supports only coach:read")

    @classmethod
    def from_env(cls):
        # Missing auth settings fail startup, never fall back to anonymous access.
        return cls(dashboard_client_id=os.environ.get("DASHBOARD_CLIENT_ID", ""), **{field: os.environ[env] for field, env in {
            "backend_url": "BACKEND_URL", "backend_allowed_host": "BACKEND_ALLOWED_HOST",
            "public_url": "MCP_PUBLIC_URL", "oauth_issuer": "OAUTH_ISSUER",
            "oauth_jwks_url": "OAUTH_JWKS_URL", "owner_subject": "OAUTH_OWNER_SUBJECT",
        }.items()})
