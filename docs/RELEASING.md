# Releasing

This is for maintainers cutting a new Loopback release; it is not part of
installing or running Loopback yourself.

## Before tagging

Versions must agree across four places, enforced by CI:

```bash
python3 scripts/check_versions.py
python3 scripts/check_versions.py --expect 4.0.1   # also check against a tag
```

- `runtime/version.py`'s `VERSION`
- `packaging/npm/package.json`'s `version`
- `pyproject.toml`'s `version`
- a `## <version>` heading at the top of `CHANGELOG.md`

Add the new version's `## X.Y.Z — YYYY-MM-DD` section to `CHANGELOG.md`
before tagging — the release workflow extracts release notes directly from
it (a regex anchored on that heading), so a missing or mismatched heading
produces an empty or failed release description.

## CI gates

`.github/workflows/ci.yml` runs on every push/PR:

- **test** — `python -m compileall runtime` and the full unittest suite on
  Linux/macOS/Windows × Python 3.11/3.13.
- **lint** — ruff, bandit (medium+ severity), `pip-audit --strict`,
  shellcheck on the shell installers/scripts, a Node syntax check on the
  dashboard JS and the npm wrapper, `check_versions.py`, a grep for
  maintainer-leaked strings across the whole tree, and
  `scripts/check_skills.py`.
- **powershell** — parses `install.ps1`/`bin/loopback.ps1` with
  PowerShell's own parser.
- **install-linux-distros** — a real install (as a non-root `alice`
  account) on Debian, Ubuntu, Fedora, Alpine, Arch, and openSUSE
  Tumbleweed.
- **install-macos**, **install-windows** — smoke install + `doctor`.

A tag push only triggers the release workflow — it does not re-run or
depend on CI passing on the tag commit itself, so **tag only a commit whose
CI run on its branch was green.**

## Tagging and the release workflow

```bash
git tag v4.0.1
git push origin v4.0.1
```

`.github/workflows/release.yml`:

1. **verify** — `check_versions.py --expect <tag version>`, then installs
   dependencies and runs the full test suite again against the tagged
   commit specifically.
2. **release** (needs verify) — `scripts/make-release.sh` builds the
   release tarball and `SHA256SUMS`, copies `install.sh`/`install.ps1` into
   `dist/` and appends their checksums to `SHA256SUMS`, extracts this
   version's notes out of `CHANGELOG.md`, and publishes a GitHub release
   (marked prerelease automatically if the tag contains a `-`, e.g.
   `v4.1.0-rc1`) attaching the tarball, `SHA256SUMS`, `install.sh`, and
   `install.ps1`.
3. Finally, **only on a non-prerelease tag**, and only if the `NPM_TOKEN`
   secret is configured, `npm publish --access public --provenance`
   publishes the npm wrapper package. If `NPM_TOKEN` isn't configured, this
   step prints a message and exits cleanly rather than failing the release
   — the GitHub release with tarball and checksums is still considered a
   complete release either way.

## Why this is safe to install unattended

The npm wrapper (`npx loopback-mcp install`) and `install.sh`/`install.ps1`
only ever fetch the tarball and `SHA256SUMS` over HTTPS, **verify the
SHA-256 before extracting anything**, and extract with the system `tar` —
there is no execution of unverified downloaded content at any point in that
chain. See [packaging/npm/README.md](../packaging/npm/README.md) for the
wrapper's own documentation of this.

## See also

- [UPGRADE.md](UPGRADE.md) — what happens on the receiving end of a
  release.
- [MIGRATION-v4.md](MIGRATION-v4.md) — the last time this needed a written
  migration guide; use it as a template if a future release needs one too.
