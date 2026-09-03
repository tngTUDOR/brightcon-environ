#!/usr/bin/env python3
"""Post a correctly signed GitHub push payload at a locally running service.

Exercises the whole path -- signature check, ref filter, diff, rebuild --
without GitHub or a tunnel being involved.

    scripts/fake-webhook.py --repo /tmp/environ-demo
    scripts/fake-webhook.py --event ping

Only the standard library is used, so it runs with any python3.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

NULL_SHA = "0" * 40
DEFAULT_URL = "http://127.0.0.1:8787/hooks/github"


def git(repo: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def post(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, str]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise SystemExit(f"cannot reach {url}: {exc.reason}") from exc


def get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument(
        "--event", default="push", choices=["push", "ping", "pull_request"]
    )
    parser.add_argument(
        "--repo", default=None, help="local clone used to derive commit SHAs"
    )
    parser.add_argument("--ref", default="refs/heads/main")
    parser.add_argument("--before", default=None)
    parser.add_argument("--after", default=None)
    parser.add_argument(
        "--first-push",
        action="store_true",
        help="send the null before-SHA, forcing a full scan",
    )
    parser.add_argument(
        "--secret",
        default=os.environ.get("GITHUB_WEBHOOK_SECRET"),
        help="defaults to $GITHUB_WEBHOOK_SECRET",
    )
    parser.add_argument(
        "--bad-signature", action="store_true", help="sign with a wrong secret"
    )
    parser.add_argument(
        "--watch", action="store_true", help="poll the job until it finishes"
    )
    args = parser.parse_args()

    if not args.secret:
        parser.error("no secret: pass --secret or set GITHUB_WEBHOOK_SECRET")

    before, after = args.before, args.after
    if args.event == "push" and (before is None or after is None):
        if args.repo:
            after = after or git(args.repo, "rev-parse", "HEAD")
            if before is None:
                parent = subprocess.run(
                    ["git", "-C", args.repo, "rev-parse", "HEAD~1"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                before = parent.stdout.strip() if parent.returncode == 0 else NULL_SHA
        else:
            before, after = before or NULL_SHA, after or NULL_SHA
    if args.first_push:
        before = NULL_SHA

    payload = {
        "ref": args.ref,
        "before": before,
        "after": after,
        "deleted": False,
        "repository": {"full_name": "local/demo"},
        "pusher": {"name": "local"},
        "head_commit": {"id": after},
    }
    if args.event == "ping":
        payload = {"zen": "Keep it logically awesome.", "hook_id": 1}

    body = json.dumps(payload).encode("utf-8")
    secret = (args.secret + "-wrong") if args.bad_signature else args.secret
    signature = (
        "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    )

    status, text = post(
        args.url,
        body,
        {
            "Content-Type": "application/json",
            "X-GitHub-Event": args.event,
            "X-GitHub-Delivery": f"local-{int(time.time())}",
            "X-Hub-Signature-256": signature,
        },
    )
    print(f"HTTP {status} {text}")

    if not args.watch or status != 202:
        return 0 if 200 <= status < 300 else 1

    job_id = json.loads(text).get("job")
    base = args.url.rsplit("/hooks/", 1)[0]
    print(f"watching job {job_id} ...")
    seen = 0
    while True:
        _, detail = get(f"{base}/jobs/{job_id}")
        job = json.loads(detail)
        for line in job.get("log", [])[seen:]:
            print(f"  {line}")
        seen = len(job.get("log", []))
        if job["status"] in {"succeeded", "failed"}:
            print(f"job {job_id}: {job['status']}")
            return 0 if job["status"] == "succeeded" else 1
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
