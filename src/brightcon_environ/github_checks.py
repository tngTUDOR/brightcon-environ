"""Report job outcomes as GitHub Check Runs on the definitions repository.

Check Runs write access requires a GitHub App. When App ID, installation ID and
private key path are all set, the service mints short-lived installation tokens.
Otherwise every operation is a no-op so existing deployments keep working.
API failures never fail a build.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import jwt

from .config import Config

logger = logging.getLogger("brightcon_environ.github_checks")

CHECK_NAME = "environ"
API_BASE = "https://api.github.com"
# GitHub caps ``output.text``; stay safely under the documented 65535 limit.
MAX_OUTPUT_TEXT = 60_000
# Refresh the installation token this many seconds before GitHub's expires_at.
TOKEN_REFRESH_SKEW_SECONDS = 60
# App JWTs should be short-lived (GitHub allows at most 10 minutes).
APP_JWT_LIFETIME_SECONDS = 9 * 60

_HTTPS_RE = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)
_SSH_RE = re.compile(r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$")


def parse_github_repo(url: str) -> tuple[str, str] | None:
    """Return ``(owner, repo)`` for a github.com remote, else ``None``."""
    url = (url or "").strip()
    if not url:
        return None
    for pattern in (_HTTPS_RE, _SSH_RE):
        if match := pattern.fullmatch(url):
            return match.group("owner"), match.group("repo")
    return None


class ChecksClient(Protocol):
    def create(
        self,
        *,
        head_sha: str,
        job_id: str,
        title: str,
        summary: str = "",
    ) -> int | None: ...

    def complete(
        self,
        check_run_id: int,
        *,
        conclusion: str,
        title: str,
        summary: str,
        text: str,
    ) -> None: ...


class NullChecksClient:
    """No-op client used when Checks are not configured."""

    def create(
        self,
        *,
        head_sha: str,
        job_id: str,
        title: str,
        summary: str = "",
    ) -> int | None:
        return None

    def complete(
        self,
        check_run_id: int,
        *,
        conclusion: str,
        title: str,
        summary: str,
        text: str,
    ) -> None:
        return None


def _github_request(
    method: str,
    url: str,
    *,
    token: str,
    payload: dict | None = None,
) -> dict | None:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "brightcon-environ",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        logger.warning(
            "GitHub API %s %s failed: HTTP %s %s", method, url, exc.code, detail
        )
        return None
    except OSError as exc:
        logger.warning("GitHub API %s %s failed: %s", method, url, exc)
        return None
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("GitHub API returned non-JSON for %s %s", method, url)
        return None
    return data if isinstance(data, dict) else None


@dataclass
class InstallationTokenSource:
    """Mint and cache GitHub App installation access tokens."""

    app_id: str
    installation_id: str
    private_key_pem: str
    api_base: str = API_BASE
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _token: str | None = field(default=None, repr=False)
    _expires_at: float = field(default=0.0, repr=False)

    def get_token(self) -> str | None:
        with self._lock:
            now = time.time()
            if self._token and now < self._expires_at - TOKEN_REFRESH_SKEW_SECONDS:
                return self._token
            minted = self._mint()
            if minted is None:
                return None
            token, expires_at = minted
            self._token = token
            self._expires_at = expires_at
            return token

    def _app_jwt(self) -> str:
        now = datetime.now(UTC)
        payload = {
            "iat": int(now.timestamp()) - 60,
            "exp": int((now + timedelta(seconds=APP_JWT_LIFETIME_SECONDS)).timestamp()),
            "iss": self.app_id,
        }
        return jwt.encode(payload, self.private_key_pem, algorithm="RS256")

    def _mint(self) -> tuple[str, float] | None:
        url = f"{self.api_base}/app/installations/{self.installation_id}/access_tokens"
        data = _github_request("POST", url, token=self._app_jwt(), payload={})
        if data is None:
            return None
        token = data.get("token")
        expires_at_raw = data.get("expires_at")
        if not isinstance(token, str) or not token:
            logger.warning("GitHub installation token response missing token")
            return None
        expires_at = time.time() + 3600
        if isinstance(expires_at_raw, str):
            try:
                expires_at = datetime.fromisoformat(
                    expires_at_raw.replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                logger.warning(
                    "could not parse installation token expires_at %r", expires_at_raw
                )
        return token, expires_at


@dataclass
class GitHubChecksClient:
    tokens: InstallationTokenSource
    owner: str
    repo: str
    api_base: str = API_BASE

    def create(
        self,
        *,
        head_sha: str,
        job_id: str,
        title: str,
        summary: str = "",
    ) -> int | None:
        payload = {
            "name": CHECK_NAME,
            "head_sha": head_sha,
            "status": "in_progress",
            "external_id": job_id,
            "output": {
                "title": title,
                "summary": summary or title,
            },
        }
        data = self._request("POST", "/check-runs", payload)
        if data is None:
            return None
        check_id = data.get("id")
        return int(check_id) if isinstance(check_id, int) else None

    def complete(
        self,
        check_run_id: int,
        *,
        conclusion: str,
        title: str,
        summary: str,
        text: str,
    ) -> None:
        if len(text) > MAX_OUTPUT_TEXT:
            text = "…(truncated)\n" + text[-MAX_OUTPUT_TEXT:]
        self._request(
            "PATCH",
            f"/check-runs/{check_run_id}",
            {
                "status": "completed",
                "conclusion": conclusion,
                "output": {
                    "title": title,
                    "summary": summary,
                    "text": text,
                },
            },
        )

    def _request(self, method: str, path: str, payload: dict) -> dict | None:
        token = self.tokens.get_token()
        if token is None:
            return None
        url = f"{self.api_base}/repos/{self.owner}/{self.repo}{path}"
        return _github_request(method, url, token=token, payload=payload)


def _load_private_key(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("cannot read GitHub App private key %s: %s", path, exc)
        return None


def checks_client_from_config(config: Config) -> ChecksClient:
    app_id = config.github_app_id
    installation_id = config.github_app_installation_id
    key_file = config.github_app_private_key_file
    if not app_id or not installation_id or key_file is None:
        return NullChecksClient()

    parsed = parse_github_repo(config.repo.url)
    if parsed is None:
        logger.info(
            "GitHub App credentials are set but repo.url is not a github.com remote; "
            "Check Runs disabled"
        )
        return NullChecksClient()

    pem = _load_private_key(key_file)
    if pem is None:
        return NullChecksClient()

    owner, repo = parsed
    tokens = InstallationTokenSource(
        app_id=app_id,
        installation_id=installation_id,
        private_key_pem=pem,
    )
    return GitHubChecksClient(tokens=tokens, owner=owner, repo=repo)
