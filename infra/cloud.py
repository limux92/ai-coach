"""Small, standard-library-only gcloud/REST helpers; never print credentials."""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request


class Cloud:
    def __init__(self, project: str):
        self.project = project
        self.executable = os.environ.get("GCLOUD_BIN", "gcloud")

    def command(self, *args: str, input_text: str | None = None,
                allow_missing: bool = False, capture: bool = True):
        result = subprocess.run(
            [self.executable, *args, f"--project={self.project}", "--quiet"],
            input=input_text, text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None, check=False,
        )
        if result.returncode:
            error = result.stderr or "gcloud command failed"
            missing = any(s in error for s in (
                "NOT_FOUND", "not found", "does not exist", "No URLs matched",
            ))
            # Cloud Run uses this exact message instead of NOT_FOUND when a
            # service has never been deployed. Match only the requested describe
            # target, so permission, network and unrelated errors still raise.
            if args[:3] == ("run", "services", "describe") and len(args) > 3:
                not_deployed = f"ERROR: (gcloud.run.services.describe) Cannot find service [{args[3]}]"
                missing = missing or error.strip() in (not_deployed, not_deployed + ".")
            if allow_missing and missing:
                return None
            raise RuntimeError(error.strip())
        return result.stdout or ""

    def json(self, *args: str, allow_missing: bool = False):
        output = self.command(*args, "--format=json", allow_missing=allow_missing)
        return None if output is None else json.loads(output or "null")

    def rest(self, method: str, url: str, body=None, allow_missing: bool = False):
        token = self.command("auth", "print-access-token").strip()
        request = urllib.request.Request(url, method=method, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Goog-User-Project": self.project,
        }, data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and allow_missing:
                return None
            raise RuntimeError(f"Cloud API {method} failed ({exc.code}): "
                               f"{exc.read().decode()}") from None
