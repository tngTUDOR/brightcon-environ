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


@pytest.fixture
def queue(config: Config) -> IdleQueue:
    return IdleQueue(config)


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


def test_a_signed_push_to_main_is_queued(client: TestClient, queue: IdleQueue):
    response = deliver(client, push())
    assert response.status_code == 202

    job_id = response.json()["job"]
    job = queue.get(job_id)
    assert job is not None
    assert (job.trigger, job.before, job.after) == ("webhook", "a" * 40, "b" * 40)


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
    response = deliver(client, push(), event="pull_request")
    assert response.status_code == 200
    assert "pull_request" in response.json()["ignored"]
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
    assert (job.trigger, job.names, job.force) == ("manual", ["demo"], True)


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
