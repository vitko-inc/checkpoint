#!/usr/bin/env python3
"""Vitko Runners checkpoint step.

On Vitko Runners: tells the runner host that this job's setup is done. When this job is a run of
the repository's default branch (push, manual or scheduled), the host saves the setup at this
point, and later jobs of the repository start with it. Otherwise the host only answers which
saved setup this job started from.

Anywhere else: does nothing.

The step never fails the job: anything unexpected is reported and the job goes on.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import urllib.request

PROTOCOL = "vitko-checkpoint-v1"
HOST_CID = 2
HOST_PORT = 5207
CONNECT_TIMEOUT_S = 3
MAX_LINE = 1 << 16
SECRET_REF = re.compile(r"secrets\s*(\.\s*(?!GITHUB_TOKEN\b)[A-Za-z_][A-Za-z0-9_]*|\[)|toJSON\(\s*secrets\s*\)", re.I)
CHECKPOINT_USES = re.compile(r"^vitko-inc/checkpoint(@|$)", re.I)


def log(message: str) -> None:
    print(f"checkpoint: {message}", flush=True)


def summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as out:
                out.write(text + "\n")
        except OSError:
            pass


class Lines:
    def __init__(self, sock: socket.socket) -> None:
        self.sock, self.buf = sock, b""

    def send(self, obj: dict) -> None:
        self.sock.sendall((json.dumps(obj, separators=(",", ":")) + "\n").encode())

    def recv(self) -> dict | None:
        while b"\n" not in self.buf:
            if len(self.buf) > MAX_LINE:
                raise ValueError("oversized message from the runner host")
            chunk = self.sock.recv(4096)
            if not chunk:
                return None
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line)


def connect() -> socket.socket | None:
    """The runner host's local channel, or None when this is not a Vitko runner."""
    if not hasattr(socket, "AF_VSOCK"):
        return None
    sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_TIMEOUT_S)
    try:
        sock.connect((HOST_CID, HOST_PORT))
    except OSError:
        sock.close()
        return None
    return sock


# ---- the secrets rule ----------------------------------------------------------------------------

def workflow_text() -> str | None:
    """This run's workflow file at this run's commit."""
    ref = os.environ.get("GITHUB_WORKFLOW_REF", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    sha = os.environ.get("GITHUB_WORKFLOW_SHA") or os.environ.get("GITHUB_SHA", "")
    prefix = repo + "/"
    if not ref.startswith(prefix) or "@" not in ref or not sha:
        return None
    path = ref[len(prefix):].split("@", 1)[0]
    workspace = os.environ.get("GITHUB_WORKSPACE", "")
    if workspace and os.path.isdir(os.path.join(workspace, ".git")):
        try:
            return subprocess.run(["git", "-C", workspace, "show", f"{sha}:{path}"], check=True,
                                  capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            pass
    token = os.environ.get("INPUT_TOKEN", "")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    if not token:
        return None
    request = urllib.request.Request(f"{api}/repos/{repo}/contents/{path}?ref={sha}", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github.raw+json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode()
    except (OSError, ValueError):
        return None


def secrets_before_checkpoint(text: str, job_id: str) -> tuple[bool, list[str]]:
    """(ok, findings): whether the workflow's env, this job's env, or any step before this one
    references a secret other than GITHUB_TOKEN."""
    try:
        import yaml  # noqa: PLC0415  (present on GitHub-hosted and Vitko runner images)
    except ImportError:
        yaml = None
    if yaml is None:
        # No parser: look at everything up to the first checkpoint step, conservatively.
        head = re.split(r"uses:\s*['\"]?vitko-inc/checkpoint", text, maxsplit=1, flags=re.I)[0]
        found = sorted({m.group(0) for m in SECRET_REF.finditer(head)})
        return (not found, [f"before this step: {name}" for name in found])
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return False, ["the workflow file could not be read"]
    job = (doc.get("jobs") or {}).get(job_id)
    if not isinstance(job, dict) or "steps" not in job:
        return False, [f"job {job_id!r} not found in the workflow"]
    findings = []

    def check(where: str, value) -> None:
        for match in SECRET_REF.finditer(json.dumps(value)):
            findings.append(f"{where}: {match.group(0)}")

    check("workflow env", doc.get("env"))
    check("job env", job.get("env"))
    for index, step in enumerate(job.get("steps") or []):
        if isinstance(step, dict) and CHECKPOINT_USES.match(str(step.get("uses", ""))):
            break
        name = (step or {}).get("name") or (step or {}).get("uses") or f"step {index + 1}"
        check(f"step {name!r}", step)
    return (not findings, findings)


# ---- the exchange ----------------------------------------------------------------------------------

def main() -> int:
    sock = connect()
    if sock is None:
        log("not running on Vitko Runners: nothing to do")
        return 0
    timeout = float(os.environ.get("INPUT_TIMEOUT_SECONDS") or 120)
    text = workflow_text()
    if text is None:
        ok, findings = False, ["the workflow file could not be read"]
    else:
        ok, findings = secrets_before_checkpoint(text, os.environ.get("GITHUB_JOB", ""))
    try:
        sock.settimeout(timeout)
        lines = Lines(sock)
        lines.send({"type": "hello", "protocol": PROTOCOL, "secrets": {"ok": ok, "findings": findings[:20]}})
        reply = lines.recv() or {}
        if reply.get("type") == "sync":
            os.sync()
            lines.send({"type": "synced"})
            reply = lines.recv() or {}
    except (OSError, ValueError) as error:
        log(f"the runner host did not answer ({error}); the job goes on")
        return 0
    finally:
        sock.close()
    kind = reply.get("type")
    if kind == "captured":
        commit = str(reply.get("commit", ""))[:12]
        log(f"setup saved from {commit}; later jobs of this repository start with it")
        summary(f"**Checkpoint:** setup saved from `{commit}`. Later jobs of this repository start with it.")
    elif kind == "noted":
        saved = reply.get("parent") or {}
        if saved.get("commit"):
            log(f"this job started with the setup saved from {str(saved['commit'])[:12]}")
        else:
            log("no saved setup yet: this job started from a fresh runner")
        if reply.get("reason"):
            log(f"not saved now: {reply['reason']}")
        if not ok:
            summary("**Checkpoint:** nothing was saved, because secrets are used before this step:\n\n"
                    + "\n".join(f"- {finding}" for finding in findings[:20])
                    + "\n\nMove steps that need secrets after the checkpoint step.")
    elif kind == "failed":
        log(f"the setup could not be saved ({reply.get('reason', 'no reason given')}); the job goes on")
    else:
        log("unexpected answer from the runner host; the job goes on")
    return 0


if __name__ == "__main__":
    sys.exit(main())
