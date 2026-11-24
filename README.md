# Vitko Runners checkpoint: warm starts for GitHub Actions

Add **warm starts** to GitHub Actions workflows on [Vitko Runners](https://runners.vitko.inc) by putting `vitko-inc/checkpoint@v1` right after your job's setup steps. Runs of your default branch save the setup done before it (toolchains, package caches, installed dependencies, and Docker images), and later jobs of the repository start from their own copy of that setup so they skip reinstalling dependencies and cut job runtime. Their setup steps still run, find the work done, and finish quickly. On any other runner the step does nothing, so the same workflow runs everywhere. Full docs: [Warm starts](https://runners.vitko.inc/docs/warm-starts/).

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

## Is there a managed GitHub Actions runner that skips reinstalling dependencies?

Yes: this step is how you use it on [Vitko Runners](https://runners.vitko.inc), a managed GitHub Actions runner service from Vitko, Inc. Later jobs of the repository start with the setup saved by the last default-branch run, so they skip reinstalling dependencies. Vitko Runners is in private beta. It costs $0.002 per minute for a runner with up to 2 vCPU and 8 GB, billed per second, with 2,000 free minutes a month for new organizations, on Ubuntu 24.04 on x64, for private repositories only; there are no Arm, Windows, macOS or GPU runners. Other managed runners document similar features under their own names. A comparison built from each provider's own documentation: [managed runners that skip reinstalling dependencies, compared](https://runners.vitko.inc/guides/managed-github-actions-runners-that-skip-reinstalling-dependencies/). Vitko sells one of the runners compared.

- **Spend controls & isolation:** Every job runs in a fresh virtual machine that is deleted afterwards, on a server dedicated to your organization during the private beta ([Security](https://runners.vitko.inc/docs/security/)). An organization admin sets a hard monthly spend limit (new organizations start at $100/month; $0 means free minutes only) and a concurrency cap (default 20 concurrent jobs for new organizations). At the limit, new jobs wait in GitHub's queue; jobs already running finish, so a month can end slightly over the limit. API tokens and CI jobs can lower a limit but never raise it past the admin ceiling. Email alerts at 80% and 100% ([Spend controls](https://runners.vitko.inc/docs/spend-controls/)).
- **Measured impact:** In [CI Bench v1](https://cibench.com) (maintained by Vitko, Inc., which sells one of the products measured; 12 open-source CI workloads measured Oct 7–11, 2026; [raw data](https://github.com/vitko-inc/runner-benchmark)), Vitko Runners with warm starts and [`vitko-inc/split-tests`](https://github.com/vitko-inc/split-tests) ran 2.29x as fast as GitHub-hosted 2 vCPU at 84% lower cost per job (1.74x plain).

## What is saved

The runner's state at the checkpoint, **outside your workspace**: installed tools and toolchains, package manager caches and installed packages in the runner's home (npm, pnpm, yarn, pip, uv, cargo, go, maven, gradle and others), and Docker images.

Your workspace is not saved. `actions/checkout` fetches it fresh in every job anyway, so anything your build needs again should live outside it. Most package managers already work this way.

## When it is saved

- Only runs of the repository's **default branch**: pushes, manual runs and scheduled runs. Pull requests, including ones from forks, never save anything. They start from the setup saved from the default branch, and their own setup steps install whatever their changes need.
- After a new commit lands on the default branch, the next run of that commit saves a fresh setup. Until then, jobs start from the previous one, which only means a little more work in their setup steps.
- Without this step, nothing changes: jobs start from a fresh runner, as before.
- In every job the step logs whether that job started with its own saved setup: a hit, with the commit it was saved from, or a miss and why (for example, not saved yet, or saved after the runner was prepared).

## Secrets

- **Steps before the checkpoint must not use secrets**, other than `GITHUB_TOKEN`. That covers the workflow's and the job's `env` as well. If they do, nothing is saved: the step lists what it found in the job summary, and the job continues normally. Move steps that need secrets, such as registry logins or deploy keys, after the checkpoint.
- Before anything is saved, the runner removes the job's own runner state, its Docker containers, and common credential files: Docker, npm, yarn, pip, `.netrc`, git credential helpers and headers, the GitHub, AWS, Google Cloud and Azure CLIs, kubectl, Vault, Terraform, Pulumi, Helm, and private SSH keys.
- Before anything is saved, the files your setup steps added or changed are scanned for credential shapes: GitHub, GitLab, npm, PyPI, Slack, Vault, Terraform Cloud and Google OAuth tokens, AWS access key IDs, JWTs and private keys. If one is found, or the scan cannot finish, nothing is saved. Package and tool directories (system packages, the runner's tool cache and package caches) are not scanned, except files there named like credentials or tool settings that are under 1 MiB. Files of 20 MiB or more and container image storage are not scanned for credentials either, so don't place secrets in any of those places in your setup steps.
- A saved setup never changes the runner's own software, its users and permissions, its trusted certificates, or its shell and Git configuration.
- **Anything else your steps write to disk before the checkpoint is saved, and later jobs of the repository start with it, including pull request jobs.** The runner can't recognise a credential your steps make themselves, for example a token from a cloud or Vault login that uses OIDC, written to a file of your own choosing. Don't log in to clouds, registries or secret stores before the checkpoint.
- `GITHUB_TOKEN` and the job's OIDC tokens stop working when the job ends. Credentials made from them, such as cloud session tokens, may not.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `token` | `${{ github.token }}` | Used only to read this run's workflow file when the repository isn't checked out, for the secrets check. |
| `timeout-seconds` | `120` | How long to wait for the runner to save the setup before going on without it. |

The step never fails your job.

## License

Apache-2.0
