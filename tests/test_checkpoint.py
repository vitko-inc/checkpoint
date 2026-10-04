"""Checks for the secrets rule (no runner host needed)."""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("checkpoint", os.path.join(HERE, "..", "checkpoint.py"))
checkpoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checkpoint)

CLEAN = """
on: push
jobs:
  test:
    runs-on: vitko-runners
    env:
      CI: "true"
    steps:
      - uses: actions/checkout@v5
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
      - run: npm ci
      - uses: vitko-inc/checkpoint@v1
      - run: npm publish
        env:
          NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}
"""

BEFORE = CLEAN.replace("      - run: npm ci\n", "      - run: npm ci\n        env:\n          NPM_TOKEN: ${{ secrets.NPM_TOKEN }}\n")
JOB_ENV = CLEAN.replace('      CI: "true"', "      DEPLOY_KEY: ${{ secrets.DEPLOY_KEY }}")
ALL = CLEAN.replace("      - run: npm ci\n", "      - run: echo '${{ toJSON(secrets) }}' > /dev/null\n")
WORKFLOW_ENV = "env:\n  KEY: ${{ secrets['KEY'] }}\n" + CLEAN


# Reusable workflows: GitHub names the caller's file in GITHUB_WORKFLOW_REF and the called job in
# GITHUB_JOB. Shapes of common jobs that use only the job's own token before the step.
CALLER = """
on: push
jobs:
  build_rust:
    uses: ./.github/workflows/rust.yml
    with:
      checkpoint: true
  node_tests:
    uses: ./.github/workflows/node.yml
    with:
      runs_on: ${{ vars.LABEL }}
  image:
    uses: ./.github/workflows/image.yml
"""
RUST = """
on: workflow_call
jobs:
  build:
    runs-on: x
    env:
      target: x86_64-unknown-linux-gnu
    steps:
      - uses: actions/checkout@v5
        with:
          repository: someone/project
          persist-credentials: false
      - uses: dtolnay/rust-toolchain@stable
      - uses: vitko-inc/checkpoint@v1
      - run: cargo test
"""
NODE = """
on: workflow_call
jobs:
  test:
    runs-on: x
    env:
      GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
    steps:
      - uses: actions/checkout@v5
        with:
          token: ${{ github.token }}
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with:
          token: ${{ secrets['GITHUB_TOKEN'] }}
      - run: gh release download
        env:
          GH_TOKEN: ${{ github.token }}
      - uses: vitko-inc/checkpoint@v1
      - run: npm publish
        env:
          NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}
"""
IMAGE = """
on: workflow_call
jobs:
  build-image:
    runs-on: x
    steps:
      - uses: actions/checkout@v5
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: vitko-inc/checkpoint@v1
"""


def reader(files):
    return lambda repo, path, ref: files.get(path)


def check(name, ok, expected_ok):
    if ok != expected_ok:
        print(f"FAIL: {name}: ok={ok}, expected {expected_ok}")
        sys.exit(1)


def main():
    ok, findings = checkpoint.secrets_before_checkpoint(CLEAN, "test")
    check("secrets after the checkpoint and GITHUB_TOKEN are fine", ok, True)
    for name, text in [("a step before", BEFORE), ("job env", JOB_ENV), ("toJSON(secrets)", ALL),
                       ("workflow env", WORKFLOW_ENV)]:
        ok, findings = checkpoint.secrets_before_checkpoint(text, "test")
        check(name, ok, False)
        if not findings:
            print(f"FAIL: {name}: no findings")
            sys.exit(1)
    ok, findings = checkpoint.secrets_before_checkpoint(CLEAN, "other-job")
    check("an unknown job refuses", ok, False)
    files = {".github/workflows/caller.yml": CALLER, ".github/workflows/rust.yml": RUST,
             ".github/workflows/node.yml": NODE, ".github/workflows/image.yml": IMAGE}
    for job in ("build", "test", "build-image"):
        status, found = checkpoint.check_job(reader(files), "o/r", ".github/workflows/caller.yml", "sha", job)
        if status != "ok":
            print(f"FAIL: called job {job}: {status} {found}")
            sys.exit(1)
    # A real secret in a called job, or passed in as an input, is still refused.
    leaky = dict(files)
    leaky[".github/workflows/rust.yml"] = RUST.replace("      - uses: dtolnay/rust-toolchain@stable\n",
        "      - uses: dtolnay/rust-toolchain@stable\n        env:\n          KEY: ${{ secrets.DEPLOY_KEY }}\n")
    status, found = checkpoint.check_job(reader(leaky), "o/r", ".github/workflows/caller.yml", "sha", "build")
    if status != "secrets" or not any("DEPLOY_KEY" in f for f in found):
        print(f"FAIL: secret in a called job: {status} {found}")
        sys.exit(1)
    passed = dict(files)
    passed[".github/workflows/caller.yml"] = CALLER.replace("      checkpoint: true", "      key: ${{ secrets.API_KEY }}")
    status, found = checkpoint.check_job(reader(passed), "o/r", ".github/workflows/caller.yml", "sha", "build")
    if status != "secrets":
        print(f"FAIL: secret passed as an input: {status} {found}")
        sys.exit(1)
    bracket = dict(files)
    bracket[".github/workflows/image.yml"] = IMAGE.replace("secrets.GITHUB_TOKEN", "secrets['REGISTRY_PASSWORD']")
    status, _ = checkpoint.check_job(reader(bracket), "o/r", ".github/workflows/caller.yml", "sha", "build-image")
    if status != "secrets":
        print(f"FAIL: a bracketed secret was allowed: {status}")
        sys.exit(1)
    # Not secrets, but not checkable: reported as such, never as secrets.
    for name, broken, job in [("unknown job", files, "nope"),
                              ("unreadable called workflow", {k: v for k, v in files.items() if "node" not in k}, "test")]:
        status, found = checkpoint.check_job(reader(broken), "o/r", ".github/workflows/caller.yml", "sha", job)
        if status != "unverified":
            print(f"FAIL: {name}: {status} {found}")
            sys.exit(1)
    # Two called workflows with a job of the same name: both are checked.
    twin = dict(files)
    twin[".github/workflows/caller.yml"] = CALLER + "  other:\n    uses: ./.github/workflows/other.yml\n"
    twin[".github/workflows/other.yml"] = NODE.replace("secrets.NPM_TOKEN", "x").replace(
        "      - uses: pnpm/action-setup@v4", "      - uses: pnpm/action-setup@v4\n        env:\n          T: ${{ secrets.OTHER }}")
    status, _ = checkpoint.check_job(reader(twin), "o/r", ".github/workflows/caller.yml", "sha", "test")
    if status != "secrets":
        print(f"FAIL: same-named job in another called workflow not checked: {status}")
        sys.exit(1)
    if checkpoint.called_workflow("acme/shared/.github/workflows/ci.yml@v2", "o/r", "sha") != ("acme/shared", ".github/workflows/ci.yml", "v2"):
        print("FAIL: remote reusable workflow")
        sys.exit(1)
    said = checkpoint.started_with({"commit": "0123456789abcdef0123456789abcdef01234567", "capturedAt": "2026-10-02T10:37:42Z"})
    if said != "this job started with the setup saved from 0123456789ab, saved 2026-10-02T10:37:42Z":
        print(f"FAIL: started_with: {said}")
        sys.exit(1)
    for nothing in (None, {}, {"commit": ""}, "x"):
        if "fresh runner" not in checkpoint.started_with(nothing):
            print(f"FAIL: started_with({nothing!r})")
            sys.exit(1)
    hit = {"type": "noted", "parent": {"commit": "0123456789abcdef0123456789abcdef01234567"},
           "setup": {"hit": True, "line": "hit: this job started with its own saved setup, saved from 0123456789ab"}}
    if checkpoint.own_setup(hit) != hit["setup"]["line"]:
        print(f"FAIL: own_setup hit: {checkpoint.own_setup(hit)!r}")
        sys.exit(1)
    for bad in ({}, {"setup": None}, {"setup": {"line": ""}}, {"setup": {"line": "x\n::error::y"}},
                {"setup": {"line": "x" * 401}}, {"setup": {"line": 7}}, "x", None):
        if checkpoint.own_setup(bad) is not None:
            print(f"FAIL: own_setup({bad!r}) should be None")
            sys.exit(1)
    print("ALL CHECKPOINT CHECKS PASSED")


main()
