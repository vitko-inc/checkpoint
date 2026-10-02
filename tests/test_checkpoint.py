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
    print("ALL CHECKPOINT CHECKS PASSED")


main()
