from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from brightcon_environ.github_checks import (
    GitHubChecksClient,
    NullChecksClient,
    checks_client_from_config,
    parse_github_repo,
)
from brightcon_environ.jobs import Job


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/acme/defs.git", ("acme", "defs")),
        ("https://github.com/acme/defs", ("acme", "defs")),
        ("git@github.com:acme/defs.git", ("acme", "defs")),
        ("git@github.com:acme/defs", ("acme", "defs")),
        ("https://gitlab.com/acme/defs.git", None),
        ("", None),
    ],
)
def test_parse_github_repo(url, expected):
    assert parse_github_repo(url) == expected


def test_null_client_is_a_noop():
    client = NullChecksClient()
    assert client.create(head_sha="abc", job_id="j1", title="t") is None
    client.complete(1, conclusion="success", title="t", summary="s", text="x")


def test_checks_client_from_config_without_token(config, monkeypatch):
    monkeypatch.delenv("GITHUB_CHECKS_TOKEN", raising=False)
    assert isinstance(checks_client_from_config(config), NullChecksClient)


def test_checks_client_from_config_with_non_github_url(config, monkeypatch):
    monkeypatch.setenv("GITHUB_CHECKS_TOKEN", "tok")
    config = config.__class__(
        repo=config.repo.__class__(
            url="https://example.invalid/x/y.git",
            branch=config.repo.branch,
            path=config.repo.path,
        ),
        paths=config.paths,
        tools=config.tools,
        server=config.server,
        defaults=config.defaults,
    )
    assert isinstance(checks_client_from_config(config), NullChecksClient)


def test_github_client_create_and_complete(monkeypatch):
    calls: list[tuple[str, str, dict]] = []

    class FakeResponse:
        def __init__(self, payload: dict):
            self._payload = json.dumps(payload).encode()

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=30):
        payload = json.loads(request.data.decode())
        calls.append((request.get_method(), request.full_url, payload))
        if request.get_method() == "POST":
            return FakeResponse({"id": 42})
        return FakeResponse({})

    monkeypatch.setattr(
        "brightcon_environ.github_checks.urllib.request.urlopen", fake_urlopen
    )
    client = GitHubChecksClient(token="tok", owner="acme", repo="defs")
    assert client.create(head_sha="abc123", job_id="job1", title="Rebuilding…") == 42
    client.complete(
        42, conclusion="success", title="ok", summary="sum", text="log line"
    )
    assert calls[0][0] == "POST"
    assert calls[0][2]["head_sha"] == "abc123"
    assert calls[0][2]["name"] == "environ"
    assert calls[1][0] == "PATCH"
    assert calls[1][2]["conclusion"] == "success"


def test_github_client_swallows_http_errors(monkeypatch):
    def boom(request, timeout=30):
        raise HTTPError(
            request.full_url, 403, "forbidden", hdrs=None, fp=BytesIO(b"nope")
        )

    monkeypatch.setattr("brightcon_environ.github_checks.urllib.request.urlopen", boom)
    client = GitHubChecksClient(token="tok", owner="acme", repo="defs")
    assert client.create(head_sha="abc", job_id="j", title="t") is None


def test_job_summary_includes_mode_and_check_id():
    job = Job(mode="validate", check_run_id=7, pr_number=3)
    summary = job.summary()
    assert summary["mode"] == "validate"
    assert summary["check_run_id"] == 7
    assert summary["pr_number"] == 3
