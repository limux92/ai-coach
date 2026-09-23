"""Deployment-only configuration; callers never choose identities or hosts."""
import json
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
    firebase_project_id: str
    firebase_api_key: str
    owner_subject: str
    oauth_redirect_uris: tuple[str, ...]
    auth_database: str = "ai-coach-auth"
    read_scope: str = "coach:read"

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
        if not isinstance(self.firebase_project_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", self.firebase_project_id):
            raise ValueError("FIREBASE_PROJECT_ID must be a valid project ID")
        if not isinstance(self.firebase_api_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", self.firebase_api_key):
            raise ValueError("FIREBASE_API_KEY is required")
        if (not isinstance(self.owner_subject, str) or not 1 <= len(self.owner_subject) <= 128
                or any(char.isspace() for char in self.owner_subject)):
            raise ValueError("FIREBASE_OWNER_UID is required")
        if self.auth_database != "ai-coach-auth":
            raise ValueError("OAuth sessions must use the separate ai-coach-auth database")
        if not isinstance(self.oauth_redirect_uris, (list, tuple)) or not 1 <= len(self.oauth_redirect_uris) <= 10:
            raise ValueError("Configure exact OAuth redirect URLs")
        for uri in self.oauth_redirect_uris:
            https_url(uri, "OAUTH_REDIRECT_URIS")
            if '*' in uri:
                raise ValueError("OAuth redirects cannot contain wildcards")
        object.__setattr__(self, "oauth_redirect_uris", tuple(self.oauth_redirect_uris))
        if self.read_scope != "coach:read":
            raise ValueError("This adapter supports only coach:read")

    @property
    def oauth_issuer(self):
        return self.public_url.removesuffix("/mcp")

    @property
    def firebase_issuer(self):
        return f"https://securetoken.google.com/{self.firebase_project_id}"

    @classmethod
    def from_env(cls):
        return cls(oauth_redirect_uris=json.loads(os.environ["OAUTH_REDIRECT_URIS"]),
                   auth_database=os.environ.get("AUTH_FIRESTORE_DATABASE", "ai-coach-auth"),
                   **{field: os.environ[env] for field, env in {
            "backend_url": "BACKEND_URL", "backend_allowed_host": "BACKEND_ALLOWED_HOST",
            "public_url": "MCP_PUBLIC_URL", "firebase_project_id": "FIREBASE_PROJECT_ID",
            "firebase_api_key": "FIREBASE_API_KEY", "owner_subject": "FIREBASE_OWNER_UID",
        }.items()})
