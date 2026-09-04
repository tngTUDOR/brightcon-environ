"""The REST API that listens for GitHub webhooks."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import __version__
from .builders import list_environments
from .config import Config, load_config
from .discovery import NAME_RE
from .git_repo import NULL_SHA
from .github_checks import ChecksClient
from .jobs import Job, JobQueue, StateStore
from .kernels import list_kernels
from .security import MAX_BODY_BYTES, SIGNATURE_HEADER, verify_signature, verify_token

logger = logging.getLogger("brightcon_environ")

_PR_ACTIONS = frozenset({"opened", "synchronize", "reopened"})


class RebuildRequest(BaseModel):
    names: list[str] | None = Field(
        default=None, description="Environments to rebuild; omit to rebuild everything."
    )
    force: bool = Field(
        default=False, description="Rebuild even if the definition is unchanged."
    )


def create_app(
    config: Config | None = None,
    queue: JobQueue | None = None,
    *,
    checks: ChecksClient | None = None,
) -> FastAPI:
    config = config or load_config()
    queue = queue or JobQueue(config, checks=checks)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        queue.start()
        yield
        queue.stop()

    app = FastAPI(
        title="brightcon-environ",
        version=__version__,
        summary="Rebuild JupyterHub environments from GitHub push webhooks",
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.queue = queue

    async def _read_body(request: Request) -> bytes:
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="payload too large")
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="payload too large")
        return body

    @app.get("/healthz")
    def healthz() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "branch": config.repo.branch,
            "repo": config.repo.url or None,
            "env_root": str(config.paths.env_root),
            "queued": queue.pending,
        }

    @app.post("/hooks/github", status_code=202)
    async def github_hook(
        request: Request,
        response: Response,
        x_github_event: str = Header(default=""),
        x_github_delivery: str = Header(default=""),
    ) -> dict:
        secret = config.webhook_secret
        if not secret:
            # Fail closed: an unauthenticated hook would let anyone run builds.
            raise HTTPException(
                status_code=503, detail="GITHUB_WEBHOOK_SECRET is not configured"
            )

        body = await _read_body(request)
        if not verify_signature(body, request.headers.get(SIGNATURE_HEADER), secret):
            logger.warning(
                "rejected delivery %s: bad signature", x_github_delivery or "?"
            )
            raise HTTPException(status_code=401, detail="invalid signature")

        if x_github_event == "ping":
            response.status_code = 200
            return {"pong": True}

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="payload must be a JSON object")

        if x_github_event == "push":
            return _handle_push(payload, queue, config, response, x_github_delivery)
        if x_github_event == "pull_request":
            return _handle_pull_request(
                payload, queue, config, response, x_github_delivery
            )

        response.status_code = 200
        return {"ignored": f"event {x_github_event!r} is not handled"}

    @app.post("/rebuild", status_code=202)
    def rebuild(
        payload: RebuildRequest,
        authorization: str | None = Header(default=None),
    ) -> dict:
        token = config.admin_token
        if not token:
            raise HTTPException(
                status_code=503, detail="ENVIRON_ADMIN_TOKEN is not configured"
            )
        if not verify_token(authorization, token):
            raise HTTPException(status_code=401, detail="invalid token")

        for name in payload.names or []:
            if not NAME_RE.fullmatch(name):
                raise HTTPException(
                    status_code=400, detail=f"invalid environment name {name!r}"
                )

        job = queue.submit(
            Job(
                trigger="manual",
                mode="apply",
                names=payload.names,
                force=payload.force,
            )
        )
        return {"job": job.id, "status": str(job.status)}

    @app.get("/jobs")
    def jobs(limit: int = 20) -> dict:
        return {"jobs": [job.summary() for job in queue.recent(limit)]}

    @app.get("/jobs/{job_id}")
    def job_detail(job_id: str) -> dict:
        job = queue.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"no job {job_id!r}")
        return {**job.summary(), "log": queue.tail(job_id)}

    @app.get("/environments")
    def environments() -> dict:
        state = StateStore(config.paths.state_file)
        on_disk = set(list_environments(config))
        kernels = set(list_kernels(config.paths))
        entries = []
        for name, record in sorted(state.environments.items()):
            entries.append(
                {
                    "name": name,
                    "backend": record.backend,
                    "definition": record.definition,
                    "display_name": record.display_name,
                    "built_at": record.built_at,
                    "commit": record.commit,
                    "duration_seconds": record.duration_seconds,
                    "present": name in on_disk,
                    "kernel": name in kernels,
                }
            )
        return {
            "environments": entries,
            "untracked": sorted(on_disk - set(state.environments)),
        }

    return app


def _handle_push(
    payload: dict,
    queue: JobQueue,
    config: Config,
    response: Response,
    delivery: str,
) -> dict:
    ref = payload.get("ref")
    if ref != config.repo.ref:
        response.status_code = 200
        return {"ignored": f"ref {ref!r} is not {config.repo.ref!r}"}

    if payload.get("deleted"):
        response.status_code = 200
        return {"ignored": "branch was deleted"}

    after = payload.get("after")
    if not after or after == NULL_SHA:
        response.status_code = 200
        return {"ignored": "null after SHA"}

    job = queue.submit(
        Job(
            trigger="webhook",
            mode="apply",
            before=payload.get("before"),
            after=after,
            head_sha=after,
        )
    )
    logger.info(
        "queued apply job %s for delivery %s (%s..%s)",
        job.id,
        delivery or "?",
        job.before,
        job.after,
    )
    return {"job": job.id, "status": str(job.status), "mode": job.mode}


def _handle_pull_request(
    payload: dict,
    queue: JobQueue,
    config: Config,
    response: Response,
    delivery: str,
) -> dict:
    action = payload.get("action")
    if action not in _PR_ACTIONS:
        response.status_code = 200
        return {"ignored": f"pull_request action {action!r} is not handled"}

    pr = payload.get("pull_request")
    if not isinstance(pr, dict):
        raise HTTPException(status_code=400, detail="missing pull_request object")

    base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    base_ref = base.get("ref")
    if base_ref != config.repo.branch:
        response.status_code = 200
        return {
            "ignored": (f"pull_request base {base_ref!r} is not {config.repo.branch!r}")
        }

    head_sha = head.get("sha")
    if not isinstance(head_sha, str) or not head_sha or head_sha == NULL_SHA:
        raise HTTPException(status_code=400, detail="missing pull_request.head.sha")

    number = pr.get("number")
    if not isinstance(number, int):
        raise HTTPException(status_code=400, detail="missing pull_request.number")

    base_sha = base.get("sha") if isinstance(base.get("sha"), str) else None

    job = queue.submit(
        Job(
            trigger="webhook",
            mode="validate",
            before=base_sha,
            after=head_sha,
            head_sha=head_sha,
            pr_number=number,
            force=True,
        )
    )
    logger.info(
        "queued validate job %s for delivery %s (PR #%s %s)",
        job.id,
        delivery or "?",
        number,
        head_sha,
    )
    return {
        "job": job.id,
        "status": str(job.status),
        "mode": job.mode,
        "pr": number,
    }
