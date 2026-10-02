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

# Any secret other than GITHUB_TOKEN, in dot or bracket form, and the whole secrets context.
SECRET_REF = re.compile(
    r"secrets\s*(\.\s*(?!GITHUB_TOKEN\b)[A-Za-z_][A-Za-z0-9_]*|\[\s*(?!['\"]GITHUB_TOKEN['\"]\s*\])[^\]]*\])"
    r"|toJSON\(\s*secrets\s*\)", re.I)
MAX_DEPTH = 3


def read_file(repo: str, path: str, ref: str) -> str | None:
    """A workflow file of `repo` at `ref`: through the API with the step's token, else from the
    checked-out repository when it is that repository."""
    token = os.environ.get("INPUT_TOKEN", "")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    if token:
        request = urllib.request.Request(f"{api}/repos/{repo}/contents/{path}?ref={ref}", headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github.raw+json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.read().decode()
        except (OSError, ValueError):
            pass
    workspace = os.environ.get("GITHUB_WORKSPACE", "")
    if repo == os.environ.get("GITHUB_REPOSITORY") and workspace and os.path.isdir(os.path.join(workspace, ".git")):
        try:
            return subprocess.run(["git", "-C", workspace, "show", f"{ref}:{path}"], check=True,
                                  capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def secret_refs(where: str, value) -> list[str]:
    return [f"{where}: {m.group(0)}" for m in SECRET_REF.finditer(json.dumps(value))]


def called_workflow(uses: str, repo: str, ref: str) -> tuple[str, str, str] | None:
    """(repo, path, ref) of a reusable workflow a job calls, or None."""
    uses = uses.strip()
    if uses.startswith("./"):
        return repo, uses[2:], ref
    match = re.fullmatch(r"([\w.-]+/[\w.-]+)/(\.github/workflows/[^@]+\.ya?ml)@(.+)", uses)
    return (match.group(1), match.group(2), match.group(3)) if match else None


def check_job(read, repo: str, path: str, ref: str, job_id: str, depth: int = 0) -> tuple[str, list[str]]:
    """("ok" | "secrets" | "unverified", findings) for job `job_id` as run from workflow `path`.

    The job is either in that workflow, or in a reusable workflow it calls (GitHub names the
    caller's file in GITHUB_WORKFLOW_REF and the called job in GITHUB_JOB). Several called
    workflows may have a job of that name: all of them are checked, and every one must pass.
    """
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return "unverified", ["no YAML parser on this runner"]
    text = read(repo, path, ref)
    if text is None:
        return "unverified", [f"could not read {path}"]
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return "unverified", [f"could not parse {path}"]
    jobs = doc.get("jobs") or {}
    job = jobs.get(job_id)
    if isinstance(job, dict) and "steps" in job:
        findings = secret_refs("workflow env", doc.get("env")) + secret_refs("job env", job.get("env"))
        for index, step in enumerate(job.get("steps") or []):
            if isinstance(step, dict) and CHECKPOINT_USES.match(str(step.get("uses", ""))):
                break
            name = (step or {}).get("name") or (step or {}).get("uses") or f"step {index + 1}"
            findings += secret_refs(f"step {name!r}", step)
        return ("secrets" if findings else "ok"), findings
    if depth >= MAX_DEPTH:
        return "unverified", [f"job {job_id!r} not found"]
    verdicts = []
    for caller_id, caller in jobs.items():
        if not isinstance(caller, dict) or not isinstance(caller.get("uses"), str):
            continue
        target = called_workflow(caller["uses"], repo, ref)
        if target is None:
            continue
        status, findings = check_job(read, *target, job_id, depth + 1)
        if status == "unverified" and any("not found" in f for f in findings):
            continue  # this called workflow has no such job
        # Inputs are passed in plain: a secret given as an input is a secret before the step.
        passed = secret_refs(f"job {caller_id!r} with", caller.get("with"))
        verdicts.append(("secrets" if passed else status, passed + findings))
    if not verdicts:
        return "unverified", [f"job {job_id!r} not found in {path} or the workflows it calls"]
    for wanted in ("secrets", "unverified"):
        hit = [f for status, found in verdicts if status == wanted for f in found]
        if hit:
            return wanted, hit
    return "ok", []


def secrets_before_checkpoint(text: str, job_id: str) -> tuple[bool, list[str]]:
    """(ok, findings) for a single workflow's text (kept for tests and simple callers)."""
    status, findings = check_job(lambda *_: text, "", "workflow.yml", "", job_id)
    return status == "ok", findings


def this_job() -> tuple[str, list[str]]:
    """The secrets rule for the running job."""
    ref = os.environ.get("GITHUB_WORKFLOW_REF", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    sha = os.environ.get("GITHUB_WORKFLOW_SHA") or os.environ.get("GITHUB_SHA", "")
    if not ref.startswith(repo + "/") or "@" not in ref or not sha:
        return "unverified", ["this run's workflow is not known"]
    path = ref[len(repo) + 1:].split("@", 1)[0]
    return check_job(read_file, repo, path, sha, os.environ.get("GITHUB_JOB", ""))


def started_with(saved) -> str:
    """What this job started from: the saved setup of the runner it was copied from, as the runner
    host recorded it for that runner, or a fresh runner."""
    if not isinstance(saved, dict) or not saved.get("commit"):
        return "no saved setup: this job started from a fresh runner"
    at = f", saved {saved['capturedAt']}" if saved.get("capturedAt") else ""
    return f"this job started with the setup saved from {str(saved['commit'])[:12]}{at}"


# ---- the exchange ----------------------------------------------------------------------------------

def main() -> int:
    sock = connect()
    if sock is None:
        if os.environ.get("RUNNER_NAME", "").startswith("vitko-"):
            log("this runner started without a saved setup and cannot save one: nothing to do")
        else:
            log("not running on Vitko Runners: nothing to do")
        return 0
    timeout = float(os.environ.get("INPUT_TIMEOUT_SECONDS") or 120)
    status, findings = this_job()
    ok = status == "ok"
    try:
        sock.settimeout(timeout)
        lines = Lines(sock)
        lines.send({"type": "hello", "protocol": PROTOCOL, "secrets": {"ok": ok, "reason": status, "findings": findings[:20]}})
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
        log(started_with(reply.get("parent")))
        if reply.get("reason"):
            log(f"not saved now: {reply['reason']}")
        if status == "secrets":
            summary("**Checkpoint:** nothing was saved, because secrets are used before this step:\n\n"
                    + "\n".join(f"- {finding}" for finding in findings[:20])
                    + "\n\nMove steps that need secrets after the checkpoint step.")
        elif status == "unverified":
            summary("**Checkpoint:** nothing was saved, because this job's workflow could not be checked for "
                    "secrets before this step:\n\n" + "\n".join(f"- {finding}" for finding in findings[:20]))
    elif kind == "failed":
        log(f"the setup could not be saved ({reply.get('reason', 'no reason given')}); the job goes on")
    else:
        log("unexpected answer from the runner host; the job goes on")
    return 0


if __name__ == "__main__":
    sys.exit(main())
