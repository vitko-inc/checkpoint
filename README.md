# Vitko Runners checkpoint

Put `vitko-inc/checkpoint@v1` right after your job's setup steps. On [Vitko Runners](https://runners.vitko.inc), runs of your default branch save the setup done before it, and later jobs of the repository start with that setup already in place. Their setup steps still run, but find the work done and finish quickly. On any other runner the step does nothing, so the same workflow runs everywhere.

```yaml
jobs:
  test:
    runs-on: vitko-ubuntu-24.04
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-node@v4
        with:
          node-version: 22
      - run: npm ci
      - uses: vitko-inc/checkpoint@v1   # everything above is saved
      - run: npm test
```

## What is saved

The runner's state at the checkpoint, **outside your workspace**: installed tools and toolchains, package manager caches and installed packages in the runner's home (npm, pnpm, yarn, pip, uv, cargo, go, maven, gradle and others), and Docker images.

Your workspace is not saved. `actions/checkout` fetches it fresh in every job anyway, so anything your build needs again should live outside it. Most package managers already work this way.

## When it is saved

- Only runs of the repository's **default branch**: pushes, manual runs and scheduled runs. Pull requests, including ones from forks, never save anything. They start from the setup saved from the default branch, and their own setup steps install whatever their changes need.
- After a new commit lands on the default branch, the next run of that commit saves a fresh setup. Until then, jobs start from the previous one, which only means a little more work in their setup steps.
- Without this step, nothing changes: jobs start from a fresh runner, as before.
- In every job the step logs which saved setup that job started with: its commit and when it was saved.

## Secrets

Nothing secret is saved.

- **Steps before the checkpoint must not use secrets**, other than `GITHUB_TOKEN`. That covers the workflow's and the job's `env` as well. If they do, nothing is saved: the step lists what it found in the job summary, and the job continues normally. Move steps that need secrets, such as registry logins or deploy keys, after the checkpoint.
- Before anything is saved, the runner removes common credential files (Docker, npm, yarn, pip, `.netrc`, git credential helpers and headers, the GitHub, AWS, Google Cloud and Azure CLIs, kubectl, and private SSH keys) and the job's own runner state.
- If anything that looks like a credential is still found, nothing is saved.
- `GITHUB_TOKEN` stops working when the job ends.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `token` | `${{ github.token }}` | Used only to read this run's workflow file when the repository isn't checked out, for the secrets check. |
| `timeout-seconds` | `120` | How long to wait for the runner to save the setup before going on without it. |

The step never fails your job.

## License

Apache-2.0
