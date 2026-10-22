#!/usr/bin/env bash
# Build a Loopback release:
#   dist/loopback-v<version>.tar.gz   git archive of HEAD, prefix loopback-v<version>/
#   dist/install.sh, dist/install.ps1 standalone one-line installers (release assets)
#   dist/SHA256SUMS                   checksums of every file above
#
# Fails when runtime/version.py, packaging/npm/package.json and CHANGELOG.md
# disagree on the version. Usage: scripts/make-release.sh [--allow-dirty]
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
ALLOW_DIRTY=0
[ "${1:-}" = "--allow-dirty" ] && ALLOW_DIRTY=1

die(){ printf '✗ %s\n' "$*" >&2; exit 1; }
ok(){ printf '✓ %s\n' "$*"; }

PY_VERSION="$(sed -n 's/^VERSION *= *"\([^"]*\)".*/\1/p' runtime/version.py | head -n 1)"
NPM_VERSION="$(sed -n 's/^ *"version" *: *"\([^"]*\)".*/\1/p' packaging/npm/package.json | head -n 1)"
CHANGELOG_VERSION="$(sed -n 's/^## *v\{0,1\}\([0-9][0-9.]*[0-9A-Za-z.-]*\).*/\1/p' CHANGELOG.md | head -n 1)"

[ -n "$PY_VERSION" ] || die "cannot read VERSION from runtime/version.py"
[ "$PY_VERSION" = "$NPM_VERSION" ] || die "version mismatch: runtime/version.py=$PY_VERSION packaging/npm/package.json=$NPM_VERSION"
[ "$PY_VERSION" = "$CHANGELOG_VERSION" ] || die "version mismatch: runtime/version.py=$PY_VERSION, newest CHANGELOG.md entry=$CHANGELOG_VERSION"
ok "version $PY_VERSION (version.py, package.json, CHANGELOG.md agree)"

command -v git >/dev/null 2>&1 || die "git is required"
git rev-parse --verify HEAD >/dev/null 2>&1 || die "not a git checkout with commits"
if [ "$ALLOW_DIRTY" != 1 ] && [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  die "working tree has uncommitted changes (commit them, or pass --allow-dirty to archive HEAD anyway)"
fi

TAG="v$PY_VERSION"
NAME="loopback-$TAG"
mkdir -p dist
rm -f "dist/$NAME.tar.gz" dist/SHA256SUMS

# git archive honours .gitattributes export-ignore; node_modules/.git are never in HEAD's tree,
# but exclude them explicitly in case they were committed by mistake.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git archive --format=tar --prefix="$NAME/" HEAD | tar -xf - -C "$TMP"
find "$TMP/$NAME" \( -name node_modules -o -name .git -o -name __pycache__ \) -prune -exec rm -rf {} + 2>/dev/null || true
[ -f "$TMP/$NAME/runtime/cli.py" ] || die "archive is missing runtime/cli.py"
# Reproducible-ish tarball: sorted names, fixed owner, mtime of HEAD.
MTIME="$(git log -1 --format=%ct HEAD)"
if tar --version 2>/dev/null | grep -q 'GNU tar'; then
  tar --sort=name --owner=0 --group=0 --numeric-owner --mtime="@$MTIME" -C "$TMP" -cf - "$NAME" | gzip -n -9 > "dist/$NAME.tar.gz"
else
  tar -C "$TMP" -cf - "$NAME" | gzip -n -9 > "dist/$NAME.tar.gz"
fi
cp "$TMP/$NAME/install.sh" dist/install.sh
cp "$TMP/$NAME/install.ps1" dist/install.ps1
chmod 755 dist/install.sh

(
  cd dist
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$NAME.tar.gz" install.sh install.ps1 > SHA256SUMS
  else
    shasum -a 256 "$NAME.tar.gz" install.sh install.ps1 > SHA256SUMS
  fi
)
ok "dist/$NAME.tar.gz ($(wc -c < "dist/$NAME.tar.gz" | tr -d ' ') bytes)"
ok "dist/SHA256SUMS"
cat dist/SHA256SUMS
echo
echo "Publish: gh release create $TAG dist/$NAME.tar.gz dist/install.sh dist/install.ps1 dist/SHA256SUMS --notes-file <notes>"
echo "npm:     (cd packaging/npm && npm publish)"
