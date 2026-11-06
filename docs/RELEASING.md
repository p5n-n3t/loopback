# Releasing

This is for maintainers cutting a new Loopback release; it is not part of
installing or running Loopback yourself.

## Before tagging

Check that the version agrees across four places:

```bash
python3 scripts/check_versions.py
python3 scripts/check_versions.py --expect 4.0.1   # also check against a tag
```

- `runtime/version.py`'s `VERSION`
- `packaging/npm/package.json`'s `version`
- `pyproject.toml`'s `version`
- a `## <version>` heading at the top of `CHANGELOG.md`

Add the new version's `## X.Y.Z — YYYY-MM-DD` section to `CHANGELOG.md`
before tagging so it can be used as the release notes.

## Build, verify, and publish

The current CI workflow runs compile checks, Bash syntax checks, unit tests,
and a plugin-package smoke check on Ubuntu with Python 3.11 and 3.13. Wait
for CI on the exact commit you intend to release. Native macOS and Windows
install tests are separate from this CI matrix.

From a clean checkout of that commit:

```bash
python3 scripts/check_versions.py --expect 4.0.1
bash scripts/make-release.sh
(cd dist && sha256sum -c SHA256SUMS)
```

Smoke-test the archive with a separate config directory, launcher directory,
and listener port. The release assets are the tarball, checksum manifest,
Bash installer, and PowerShell installer. Publish them together using
`gh release create`, with `--target` set to the tested commit and a
`--notes-file` containing the changelog for that version.

The release installers fetch and verify the archive before extraction.
Review the standalone installer itself before running it. npm publication
is a separate distribution step.

## See also

- [UPGRADE.md](UPGRADE.md) — what happens on the receiving end of a
  release.
- [MIGRATION-v4.md](MIGRATION-v4.md) — the last time this needed a written
  migration guide; use it as a template if a future release needs one too.
