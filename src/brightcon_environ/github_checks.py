"""Report job outcomes as GitHub Check Runs on the definitions repository.

When ``GITHUB_CHECKS_TOKEN`` is unset, all operations are no-ops so existing
deployments keep working without Checks. API failures never fail a build.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from .config import Config

logger = logging.getLogger("brightcon_environ.github_checks")

CHECK_NAME = "environ"
API_BASE = "https://api.github.com"
# GitHub caps ``output.text``; stay safely under the documented 65535 limit.
MAX_OUTPUT_TEXT = 60_000

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


@dataclass
class GitHubChecksClient:
    token: str
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
        url = f"{self.api_base}/repos/{self.owner}/{self.repo}{path}"
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "brightcon-environ",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            logger.warning(
                "GitHub Checks %s %s failed: HTTP %s %s",
                method,
                path,
                exc.code,
                detail,
            )
            return None
        except OSError as exc:
            logger.warning("GitHub Checks %s %s failed: %s", method, path, exc)
            return None
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("GitHub Checks returned non-JSON for %s %s", method, path)
            return None
        return data if isinstance(data, dict) else None


def checks_client_from_config(config: Config) -> ChecksClient:
    token = config.checks_token
    if not token:
        return NullChecksClient()
    parsed = parse_github_repo(config.repo.url)
    if parsed is None:
        logger.info(
            "GITHUB_CHECKS_TOKEN is set but repo.url is not a github.com remote; "
            "Check Runs disabled"
        )
        return NullChecksClient()
    owner, repo = parsed
    return GitHubChecksClient(token=token, owner=owner, repo=repo)
