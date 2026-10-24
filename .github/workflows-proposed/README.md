# Proposed GitHub Actions workflows

These two workflows replace `.github/workflows/ci.yml` and add a tag-driven
release pipeline. They live here instead of in `.github/workflows/` because the
automation that produced this branch pushes with a GitHub App credential that
lacks the `workflows` permission, so it cannot create or modify workflow files.

To activate them, a maintainer runs on the merged tree:

```bash
git mv .github/workflows-proposed/ci.yml .github/workflows/ci.yml
git mv .github/workflows-proposed/release.yml .github/workflows/release.yml
git commit -m "ci: adopt v4 test matrix and release workflow"
```

`ci.yml` adds a Linux/macOS/Windows × Python 3.11/3.13 test matrix, lint and
static analysis (ruff, bandit, pip-audit, shellcheck), installer smoke runs in
Debian, Ubuntu, Fedora, Alpine, Arch and openSUSE containers plus macOS and
Windows, the npm wrapper, a maintainer-path guard, and release artifacts.
`release.yml` builds `loopback-vX.Y.Z.tar.gz` + `SHA256SUMS`, publishes a
GitHub release from the CHANGELOG section, and publishes the npm wrapper when
`NPM_TOKEN` is configured. See [docs/RELEASING.md](../../docs/RELEASING.md).
