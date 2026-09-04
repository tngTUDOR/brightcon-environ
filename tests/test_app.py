from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from brightcon_environ.app import create_app
from brightcon_environ.config import Config
from brightcon_environ.jobs import EnvRecord, JobQueue, StateStore
from brightcon_environ.security import SIGNATURE_HEADER, sign

SECRET = "webhook-secret"
TOKEN = "admin-token"


class IdleQueue(JobQueue):
    """Accepts jobs and records them, but never runs the worker thread."""

    def start(self) -> None:
        return

    def stop(self, timeout: float = 5) -> None:
        return


class RecordingChecks:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.completed: list[dict] = []
        self._next_id = 100

    def create(self, *, head_sha, job_id, title, summary=""):
        self._next_id += 1
        self.created.append(
            {
                "head_sha": head_sha,
                "job_id": job_id,
                "title": title,
                "summary": summary,
                "id": self._next_id,
            }
        )
        return self._next_id

    def complete(self, check_run_id, *, conclusion, title, summary, text):
        self.completed.append(
            {
                "id": check_run_id,
                "conclusion": conclusion,
                "title": title,
                "summary": summary,
                "text": text,
            }
        )


@pytest.fixture
def checks() -> RecordingChecks:
    return RecordingChecks()


@pytest.fixture
def queue(config: Config, checks: RecordingChecks) -> IdleQueue:
    return IdleQueue(config, checks=checks)


@pytest.fixture
def client(config: Config, queue: IdleQueue, monkeypatch) -> TestClient:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("ENVIRON_ADMIN_TOKEN", TOKEN)
    with TestClient(create_app(config, queue)) as client:
        yield client


def push(ref: str = "refs/heads/main", **overrides) -> bytes:
    payload = {
        "ref": ref,
        "before": "a" * 40,
        "after": "b" * 40,
        "deleted": False,
        **overrides,
    }
    return json.dumps(payload).encode("utf-8")


def pull_request(
    *,
    action: str = "synchronize",
    base_ref: str = "main",
    head_sha: str = "c" * 40,
    number: int = 12,
    base_sha: str = "a" * 40,
) -> bytes:
    payload = {
        "action": action,
        "pull_request": {
            "number": number,
            "base": {"ref": base_ref, "sha": base_sha},
            "head": {"sha": head_sha, "ref": "feature"},
        },
    }
    return json.dumps(payload).encode("utf-8")


def deliver(client: TestClient, body: bytes, event: str = "push", secret: str = SECRET):
    return client.post(
        "/hooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
            SIGNATURE_HEADER: sign(body, secret),
        },
    )


def test_healthz(client: TestClient):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_a_signed_push_to_main_is_queued(
    client: TestClient, queue: IdleQueue, checks: RecordingChecks
):
    response = deliver(client, push())
    assert response.status_code == 202

    job_id = response.json()["job"]
    job = queue.get(job_id)
    assert job is not None
    assert (job.trigger, job.mode, job.before, job.after) == (
        "webhook",
        "apply",
        "a" * 40,
        "b" * 40,
    )
    assert job.head_sha == "b" * 40
    assert job.check_run_id == checks.created[0]["id"]
    assert checks.created[0]["head_sha"] == "b" * 40


def test_a_pull_request_into_main_queues_validate(
    client: TestClient, queue: IdleQueue, checks: RecordingChecks
):
    response = deliver(client, pull_request(), event="pull_request")
    assert response.status_code == 202
    body = response.json()
    assert body["mode"] == "validate"
    assert body["pr"] == 12

    job = queue.get(body["job"])
    assert job is not None
    assert job.mode == "validate"
    assert job.pr_number == 12
    assert job.head_sha == "c" * 40
    assert job.after == "c" * 40
    assert job.before == "a" * 40
    assert checks.created[0]["title"].startswith("Validating")


def test_pull_request_with_wrong_base_is_ignored(client: TestClient, queue: IdleQueue):
    response = deliver(client, pull_request(base_ref="develop"), event="pull_request")
    assert response.status_code == 200
    assert "base" in response.json()["ignored"]
    assert queue.recent() == []


def test_pull_request_closed_is_ignored(client: TestClient, queue: IdleQueue):
    response = deliver(client, pull_request(action="closed"), event="pull_request")
    assert response.status_code == 200
    assert queue.recent() == []


def test_push_without_github_app_still_queues(config: Config, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    for name in (
        "GITHUB_APP_ID",
        "GITHUB_APP_INSTALLATION_ID",
        "GITHUB_APP_PRIVATE_KEY_FILE",
        "GITHUB_CHECKS_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    queue = IdleQueue(config)  # real NullChecksClient from config
    with TestClient(create_app(config, queue)) as client:
        response = deliver(client, push())
    assert response.status_code == 202
    job = queue.get(response.json()["job"])
    assert job is not None
    assert job.check_run_id is None


def test_an_unsigned_push_is_rejected(client: TestClient, queue: IdleQueue):
    response = client.post(
        "/hooks/github", content=push(), headers={"X-GitHub-Event": "push"}
    )
    assert response.status_code == 401
    assert queue.recent() == []


def test_a_badly_signed_push_is_rejected(client: TestClient, queue: IdleQueue):
    response = deliver(client, push(), secret="not-the-secret")
    assert response.status_code == 401
    assert queue.recent() == []


def test_a_tampered_body_is_rejected(client: TestClient):
    body = push()
    response = client.post(
        "/hooks/github",
        content=push(ref="refs/heads/evil"),
        headers={"X-GitHub-Event": "push", SIGNATURE_HEADER: sign(body, SECRET)},
    )
    assert response.status_code == 401


def test_the_hook_fails_closed_without_a_secret(
    config: Config, queue: IdleQueue, monkeypatch
):
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    with TestClient(create_app(config, queue)) as client:
        assert deliver(client, push()).status_code == 503


def test_ping_is_answered(client: TestClient, queue: IdleQueue):
    response = deliver(client, json.dumps({"zen": "hi"}).encode(), event="ping")
    assert response.status_code == 200
    assert response.json() == {"pong": True}
    assert queue.recent() == []


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (push(ref="refs/heads/topic"), "ref"),
        (push(deleted=True), "deleted"),
    ],
)
def test_pushes_we_do_not_care_about_are_ignored(
    client: TestClient, queue: IdleQueue, body: bytes, reason: str
):
    response = deliver(client, body)
    assert response.status_code == 200
    assert reason in response.json()["ignored"]
    assert queue.recent() == []


def test_other_events_are_ignored(client: TestClient, queue: IdleQueue):
    response = deliver(client, push(), event="issues")
    assert response.status_code == 200
    assert "issues" in response.json()["ignored"]
    assert queue.recent() == []


def test_malformed_json_is_a_bad_request(client: TestClient):
    assert deliver(client, b"{not json").status_code == 400


def test_rebuild_requires_the_admin_token(client: TestClient, queue: IdleQueue):
    assert client.post("/rebuild", json={}).status_code == 401
    assert (
        client.post(
            "/rebuild", json={}, headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    assert queue.recent() == []


def test_rebuild_queues_a_manual_job(client: TestClient, queue: IdleQueue):
    response = client.post(
        "/rebuild",
        json={"names": ["demo"], "force": True},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 202
    job = queue.get(response.json()["job"])
    assert job is not None
    assert (job.trigger, job.mode, job.names, job.force) == (
        "manual",
        "apply",
        ["demo"],
        True,
    )
    assert job.check_run_id is None  # no head_sha on manual rebuilds


def test_rebuild_rejects_an_unsafe_name(client: TestClient):
    response = client.post(
        "/rebuild",
        json={"names": ["../escape"]},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 400


def test_jobs_endpoints(client: TestClient):
    job_id = deliver(client, push()).json()["job"]

    listing = client.get("/jobs")
    assert [job["id"] for job in listing.json()["jobs"]] == [job_id]

    detail = client.get(f"/jobs/{job_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "queued"
    assert detail.json()["mode"] == "apply"

    assert client.get("/jobs/nope").status_code == 404


def test_environments_reports_state_and_disk(client: TestClient, config: Config):
    state = StateStore(config.paths.state_file)
    state.put(
        EnvRecord(
            name="demo",
            backend="venv",
            definition="requirements-demo.txt",
            display_name="Demo",
            built_at="2026-09-01T00:00:00+00:00",
        )
    )
    state.save()
    (config.paths.env_root / "stray" / "bin").mkdir(parents=True)
    (config.paths.env_root / "stray" / "bin" / "python").touch()

    body = client.get("/environments").json()
    entry = body["environments"][0]
    assert entry["name"] == "demo"
    assert entry["present"] is False
    assert entry["kernel"] is False
    assert body["untracked"] == ["stray"]
