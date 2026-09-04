from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import pytest

from brightcon_environ.github_checks import (
    GitHubChecksClient,
    InstallationTokenSource,
    NullChecksClient,
    checks_client_from_config,
    parse_github_repo,
)
from brightcon_environ.jobs import Job

# Placeholder PEM body; JWT signing is mocked in tests that mint tokens.
_TEST_PEM = "-----BEGIN PRIVATE KEY-----\nTEST\n-----END PRIVATE KEY-----\n"


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


def _clear_app_env(monkeypatch) -> None:
    for name in (
        "GITHUB_APP_ID",
        "GITHUB_APP_INSTALLATION_ID",
        "GITHUB_APP_PRIVATE_KEY_FILE",
        "GITHUB_CHECKS_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


def test_checks_client_from_config_without_app(config, monkeypatch):
    _clear_app_env(monkeypatch)
    assert isinstance(checks_client_from_config(config), NullChecksClient)


def test_checks_client_from_config_with_non_github_url(
    config, monkeypatch, tmp_path: Path
):
    key = tmp_path / "app.pem"
    key.write_text(_TEST_PEM, encoding="utf-8")
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "2")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_FILE", str(key))
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


def test_checks_client_from_config_with_missing_key_file(config, monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "2")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_FILE", "/no/such/app.pem")
    config = config.__class__(
        repo=config.repo.__class__(
            url="https://github.com/acme/defs.git",
            branch=config.repo.branch,
            path=config.repo.path,
        ),
        paths=config.paths,
        tools=config.tools,
        server=config.server,
        defaults=config.defaults,
    )
    assert isinstance(checks_client_from_config(config), NullChecksClient)


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_installation_token_is_cached(monkeypatch):
    calls: list[str] = []

    def fake_urlopen(request, timeout=30):
        calls.append(request.full_url)
        return FakeResponse(
            {
                "token": "ghs_cached",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        )

    monkeypatch.setattr(
        "brightcon_environ.github_checks.urllib.request.urlopen", fake_urlopen
    )
    monkeypatch.setattr(
        InstallationTokenSource,
        "_app_jwt",
        lambda self: "fake.jwt.token",
    )
    source = InstallationTokenSource(
        app_id="1", installation_id="2", private_key_pem=_TEST_PEM
    )
    assert source.get_token() == "ghs_cached"
    assert source.get_token() == "ghs_cached"
    assert len(calls) == 1


def test_github_client_create_and_complete(monkeypatch):
    calls: list[tuple[str, str, dict | None]] = []

    def fake_urlopen(request, timeout=30):
        payload = (
            json.loads(request.data.decode()) if request.data is not None else None
        )
        calls.append((request.get_method(), request.full_url, payload))
        if "/access_tokens" in request.full_url:
            return FakeResponse(
                {"token": "ghs_test", "expires_at": "2099-01-01T00:00:00Z"}
            )
        if request.get_method() == "POST":
            return FakeResponse({"id": 42})
        return FakeResponse({})

    monkeypatch.setattr(
        "brightcon_environ.github_checks.urllib.request.urlopen", fake_urlopen
    )
    monkeypatch.setattr(
        InstallationTokenSource,
        "_app_jwt",
        lambda self: "fake.jwt.token",
    )
    tokens = InstallationTokenSource(
        app_id="1", installation_id="2", private_key_pem=_TEST_PEM
    )
    client = GitHubChecksClient(tokens=tokens, owner="acme", repo="defs")
    assert client.create(head_sha="abc123", job_id="job1", title="Rebuilding…") == 42
    client.complete(
        42, conclusion="success", title="ok", summary="sum", text="log line"
    )
    assert any("/access_tokens" in url for _, url, _ in calls)
    check_posts = [c for c in calls if c[0] == "POST" and "/check-runs" in c[1]]
    assert check_posts[0][2]["head_sha"] == "abc123"
    assert check_posts[0][2]["name"] == "environ"
    patches = [c for c in calls if c[0] == "PATCH"]
    assert patches[0][2]["conclusion"] == "success"


def test_github_client_swallows_http_errors(monkeypatch):
    def boom(request, timeout=30):
        raise HTTPError(
            request.full_url, 403, "forbidden", hdrs=None, fp=BytesIO(b"nope")
        )

    monkeypatch.setattr("brightcon_environ.github_checks.urllib.request.urlopen", boom)
    monkeypatch.setattr(
        InstallationTokenSource,
        "_app_jwt",
        lambda self: "fake.jwt.token",
    )
    tokens = InstallationTokenSource(
        app_id="1", installation_id="2", private_key_pem=_TEST_PEM
    )
    client = GitHubChecksClient(tokens=tokens, owner="acme", repo="defs")
    assert client.create(head_sha="abc", job_id="j", title="t") is None


def test_job_summary_includes_mode_and_check_id():
    job = Job(mode="validate", check_run_id=7, pr_number=3)
    summary = job.summary()
    assert summary["mode"] == "validate"
    assert summary["check_run_id"] == 7
    assert summary["pr_number"] == 3
