#!/usr/bin/env bash
# Loopback installer for Linux (any distro) and macOS.
#
#   ./install.sh [options]                       from a checkout or extracted release
#   curl -fsSL https://github.com/p5n-n3t/loopback/releases/latest/download/install.sh | bash -s -- [options]
#
# This script only prepares prerequisites (Python 3.11+ with venv, curl, tar and
# - only when requested - cloudflared / tailscale). Everything else is done by
# the single cross-platform implementation: python3 runtime/cli.py install.
#
# Options (passed through to 'cli.py install'):
#   --non-interactive  --ingress local|tailscale|cloudflare|both|ngrok  --host FQDN
#   --port N  --tailscale-mode serve|funnel  --cloudflare-token-file PATH
#   --cloudflare-tunnel-id ID  --no-service  --no-autostart  --source DIR
#   --ngrok-config PATH  --settings-file PATH  --yes  --allow-root
# Environment:
#   LOOPBACK_VERSION   release tag for the one-line install (default: latest)
#   LOOPBACK_CONFIG_DIR  install location (default ~/.config/loopback)
#   LOOPBACK_INSTALL_YES=1  same as --yes (CI)
set -Eeuo pipefail

REPO="p5n-n3t/loopback"
SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
ROOT=""
if [ -n "$SCRIPT_PATH" ] && [ -f "$SCRIPT_PATH" ]; then
  ROOT="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
fi

if [ -t 1 ]; then
  YELLOW=$'\033[38;5;220m'; CYAN=$'\033[38;5;45m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
else
  YELLOW=""; CYAN=""; DIM=""; BOLD=""; RESET=""
fi

banner(){
  printf '%s%s' "$YELLOW" "$BOLD"
  cat <<'EOF'
██╗      ██████╗  ██████╗ ██████╗ ██████╗  █████╗  ██████╗██╗  ██╗
██║     ██╔═══██╗██╔═══██╗██╔══██╗██╔══██╗██╔══██╗██╔════╝██║ ██╔╝
██║     ██║   ██║██║   ██║██████╔╝██████╔╝███████║██║     █████╔╝
██║     ██║   ██║██║   ██║██╔═══╝ ██╔══██╗██╔══██║██║     ██╔═██╗
███████╗╚██████╔╝╚██████╔╝██║     ██████╔╝██║  ██║╚██████╗██║  ██╗
╚══════╝ ╚═════╝  ╚═════╝ ╚═╝     ╚═════╝ ╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝
EOF
  printf '%s%sMCP control plane installer%s\n\n' "$RESET" "$DIM" "$RESET"
}

die(){ printf '✗ %s\n' "$*" >&2; exit 1; }
ok(){ printf '✓ %s\n' "$*"; }
warn(){ printf '! %s\n' "$*" >&2; }
info(){ printf '%s%s%s\n' "$CYAN" "$*" "$RESET"; }
have(){ command -v "$1" >/dev/null 2>&1; }

# ------------------------------------------------------------------ arguments
ARGS=("$@")
YES=0; [ "${LOOPBACK_INSTALL_YES:-0}" = 1 ] && YES=1; NONINTERACTIVE=0; ALLOW_ROOT=0; INGRESS=""; HAS_SOURCE=0
i=0
while [ $i -lt ${#ARGS[@]} ]; do
  a="${ARGS[$i]}"
  case "$a" in
    -y|--yes) YES=1 ;;
    --non-interactive) NONINTERACTIVE=1 ;;
    --allow-root) ALLOW_ROOT=1 ;;
    --ingress) i=$((i+1)); INGRESS="${ARGS[$i]:-}" ;;
    --ingress=*) INGRESS="${a#*=}" ;;
    --source|--source=*) HAS_SOURCE=1 ;;
    -h|--help)
      sed -n '2,19p' "$SCRIPT_PATH" 2>/dev/null | sed 's/^# \{0,1\}//' || true
      exit 0 ;;
  esac
  i=$((i+1))
done

ask(){ # ask "question" -> 0 for yes
  [ "$YES" = 1 ] && return 0
  [ "$NONINTERACTIVE" = 1 ] && return 1
  [ -r /dev/tty ] || return 1
  local ans
  printf '%s [y/N] ' "$1" > /dev/tty
  read -r ans < /dev/tty || return 1
  case "$ans" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

banner

# ------------------------------------------------------------------ identity
if [ "$(id -u)" = 0 ]; then
  if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != root ]; then
    echo "✗ install.sh was started with sudo (SUDO_USER=$SUDO_USER)." >&2
    echo "  Loopback runs as your normal account and installs into your home directory." >&2
    echo "  Rerun it WITHOUT sudo; it asks for sudo itself only to install missing packages." >&2
    exit 2
  fi
  if [ "$ALLOW_ROOT" != 1 ]; then
    echo "✗ refusing to install as root. Run as the account that should own Loopback," >&2
    echo "  or pass --allow-root (e.g. single-account containers)." >&2
    exit 2
  fi
  SUDO=""
elif have sudo; then
  SUDO="sudo"
elif have doas; then
  SUDO="doas"
else
  SUDO=""
fi

# ------------------------------------------------------------------ platform
OS="$(uname -s)"
ARCH="$(uname -m)"
DISTRO=""; DISTRO_LIKE=""
case "$OS" in
  Linux)
    if [ -r /etc/os-release ]; then
      DISTRO="$(. /etc/os-release && printf '%s' "${ID:-}")"
      DISTRO_LIKE="$(. /etc/os-release && printf '%s' "${ID_LIKE:-}")"
    fi ;;
  Darwin) DISTRO="macos" ;;
  *) die "unsupported OS: $OS (Windows: use install.ps1 in PowerShell)" ;;
esac
PKG=""
for pm in apt-get dnf yum zypper pacman apk brew; do
  if have "$pm"; then PKG="$pm"; break; fi
done
ok "platform: $OS/$ARCH ${DISTRO:+($DISTRO${DISTRO_LIKE:+ like $DISTRO_LIKE})} package manager: ${PKG:-none}"

pkg_install(){ # pkg_install pkg... (uses the detected package manager, asks first)
  local cmd=""
  case "$PKG" in
    apt-get) cmd="$SUDO apt-get update -qq && $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y $*" ;;
    dnf) cmd="$SUDO dnf install -y $*" ;;
    yum) cmd="$SUDO yum install -y $*" ;;
    zypper) cmd="$SUDO zypper --non-interactive install $*" ;;
    pacman) cmd="$SUDO pacman -S --needed --noconfirm $*" ;;
    apk) cmd="$SUDO apk add --no-cache $*" ;;
    brew) cmd="brew install $*" ;;
    *) return 1 ;;
  esac
  if [ -z "$SUDO" ] && [ "$(id -u)" != 0 ] && [ "$PKG" != brew ]; then
    warn "no sudo/doas available; run as root: ${cmd}"
    return 1
  fi
  if ask "Run: ${cmd# } ?"; then
    sh -c "$cmd"
  else
    warn "skipped. Install manually: ${cmd# }"
    return 1
  fi
}

# ------------------------------------------------------------------ python
python_ok(){ # python_ok BIN -> 0 when >= 3.11 with venv + ensurepip
  "$1" - <<'PY' >/dev/null 2>&1
import sys
if sys.version_info < (3, 11):
    raise SystemExit(1)
import venv, ensurepip  # noqa: F401
PY
}

find_python(){
  local c
  for c in python3.13 python3.12 python3.11 python3; do
    if have "$c" && python_ok "$(command -v "$c")"; then command -v "$c"; return 0; fi
  done
  if [ "$OS" = Darwin ] && have brew; then
    for c in "$(brew --prefix 2>/dev/null)/bin/python3.12" "$(brew --prefix 2>/dev/null)/bin/python3.13"; do
      if [ -x "$c" ] && python_ok "$c"; then printf '%s\n' "$c"; return 0; fi
    done
  fi
  return 1
}

PY="$(find_python || true)"
if [ -z "$PY" ]; then
  warn "Python 3.11+ with venv/ensurepip was not found."
  case "$PKG" in
    apt-get) pkg_install python3 python3-venv || true ;;
    dnf) pkg_install python3 python3-libs || true ;;
    yum) pkg_install python3.11 || true ;;
    zypper) pkg_install python311 || pkg_install python3 || true ;;
    pacman) pkg_install python || true ;;
    apk) pkg_install python3 py3-virtualenv || true ;;
    brew) pkg_install python@3.12 || true ;;
    *) [ "$OS" = Darwin ] && warn "install Homebrew (https://brew.sh) then: brew install python@3.12" ;;
  esac
  PY="$(find_python || true)"
  if [ -z "$PY" ]; then
    case "$PKG" in
      apt-get) hint="sudo apt-get install -y python3 python3-venv  (Debian/Ubuntu; Python 3.11+ needed, e.g. Debian 12 / Ubuntu 24.04)" ;;
      dnf|yum) hint="sudo dnf install -y python3 python3-libs  (Fedora)" ;;
      zypper) hint="sudo zypper install python311  (openSUSE)" ;;
      pacman) hint="sudo pacman -S python  (Arch)" ;;
      apk) hint="sudo apk add python3 py3-virtualenv  (Alpine)" ;;
      brew) hint="brew install python@3.12  (macOS)" ;;
      *) hint="install Python 3.11+ including the venv module" ;;
    esac
    die "Python 3.11+ with venv is required. ${hint}"
  fi
fi
ok "python: $PY ($("$PY" -c 'import platform;print(platform.python_version())'))"

for tool in curl tar; do
  if ! have "$tool"; then
    pkg_install "$tool" || die "$tool is required"
  fi
done
ok "curl + tar available"

sha256_check(){ # sha256_check SUMSFILE NAME (in current dir)
  local line
  line="$(grep " \*\{0,1\}$2\$" "$1" | head -n 1 || true)"
  [ -n "$line" ] || { warn "no checksum for $2 in $1"; return 1; }
  if have sha256sum; then
    printf '%s\n' "$line" | sha256sum -c - >/dev/null
  elif have shasum; then
    printf '%s\n' "$line" | shasum -a 256 -c - >/dev/null
  else
    warn "no sha256sum/shasum available"; return 1
  fi
}

# ------------------------------------------------------------------ release download (one-line install)
TMPDIR_LB=""
cleanup(){ [ -n "$TMPDIR_LB" ] && rm -rf "$TMPDIR_LB"; return 0; }
trap cleanup EXIT

if [ -z "$ROOT" ] || [ ! -f "$ROOT/runtime/cli.py" ]; then
  TAG="${LOOPBACK_VERSION:-}"
  if [ -z "$TAG" ]; then
    TAG="$(curl -fsSL -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/$REPO/releases/latest" \
      | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)"
    [ -n "$TAG" ] || die "could not determine the latest release (set LOOPBACK_VERSION=4.0.0)"
  fi
  case "$TAG" in v*) ;; *) TAG="v$TAG" ;; esac
  TMPDIR_LB="$(mktemp -d 2>/dev/null || mktemp -d -t loopback)"
  base="https://github.com/$REPO/releases/download/$TAG"
  info "Downloading Loopback $TAG..."
  ( cd "$TMPDIR_LB" \
    && curl -fsSL -o "loopback-$TAG.tar.gz" "$base/loopback-$TAG.tar.gz" \
    && curl -fsSL -o SHA256SUMS "$base/SHA256SUMS" ) || die "download failed from $base"
  ( cd "$TMPDIR_LB" && sha256_check SHA256SUMS "loopback-$TAG.tar.gz" ) || die "checksum verification FAILED for loopback-$TAG.tar.gz"
  ok "verified loopback-$TAG.tar.gz (sha256)"
  tar -xzf "$TMPDIR_LB/loopback-$TAG.tar.gz" -C "$TMPDIR_LB"
  ROOT="$TMPDIR_LB/loopback-$TAG"
  [ -f "$ROOT/runtime/cli.py" ] || die "release archive is missing runtime/cli.py"
fi

# ------------------------------------------------------------------ optional ingress binaries
cf_arch(){
  case "$ARCH" in
    x86_64|amd64) echo amd64 ;;
    aarch64|arm64) echo arm64 ;;
    armv7l|armv6l|arm) echo arm ;;
    i386|i686) echo 386 ;;
    *) echo "" ;;
  esac
}

install_cloudflared(){
  have cloudflared && { ok "cloudflared present"; return 0; }
  if [ "$OS" = Darwin ]; then
    have brew && pkg_install cloudflared && return 0
    warn "install cloudflared: brew install cloudflared"; return 1
  fi
  case "$PKG" in
    apt-get)
      if ask "Add Cloudflare's apt repository (pkg.cloudflare.com) and install cloudflared?"; then
        $SUDO mkdir -p -m 0755 /usr/share/keyrings
        curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | $SUDO tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
        echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
          | $SUDO tee /etc/apt/sources.list.d/cloudflared.list >/dev/null
        $SUDO apt-get update -qq && $SUDO apt-get install -y cloudflared && return 0
      fi ;;
    dnf|yum)
      if ask "Add Cloudflare's rpm repository (pkg.cloudflare.com) and install cloudflared?"; then
        curl -fsSL https://pkg.cloudflare.com/cloudflared.repo | $SUDO tee /etc/yum.repos.d/cloudflared.repo >/dev/null
        $SUDO "$PKG" install -y cloudflared && return 0
      fi ;;
    pacman) pkg_install cloudflared && return 0 ;;
  esac
  local arch url dest
  arch="$(cf_arch)"
  [ -n "$arch" ] || { warn "no cloudflared binary for $ARCH; install it manually"; return 1; }
  ask "Download the cloudflared release binary from GitHub into ~/.local/bin?" || { warn "cloudflared not installed"; return 1; }
  url="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$arch"
  dest="$HOME/.local/bin/cloudflared"
  mkdir -p "$HOME/.local/bin"
  curl -fsSL -o "$dest.download" "$url" || { warn "cloudflared download failed"; rm -f "$dest.download"; return 1; }
  if curl -fsSL -o "$dest.sha256" "$url.sha256" 2>/dev/null; then
    expected="$(awk '{print $1}' "$dest.sha256")"
    actual="$( (sha256sum "$dest.download" 2>/dev/null || shasum -a 256 "$dest.download") | awk '{print $1}')"
    rm -f "$dest.sha256"
    if [ "$expected" != "$actual" ]; then
      rm -f "$dest.download"; warn "cloudflared checksum mismatch; not installed"; return 1
    fi
    ok "cloudflared checksum verified"
  else
    warn "cloudflared publishes no .sha256 for this asset; verify it against the release notes:"
    warn "  $( (sha256sum "$dest.download" 2>/dev/null || shasum -a 256 "$dest.download") | awk '{print $1}')  cloudflared-linux-$arch"
  fi
  chmod 755 "$dest.download" && mv -f "$dest.download" "$dest"
  export PATH="$HOME/.local/bin:$PATH"
  ok "cloudflared installed to $dest"
}

install_tailscale(){
  have tailscale && { ok "tailscale present"; return 0; }
  if [ "$OS" = Darwin ]; then
    warn "install Tailscale from https://tailscale.com/download/mac (or: brew install --cask tailscale), then rerun"
    return 1
  fi
  if ask "Install Tailscale with its official script (curl -fsSL https://tailscale.com/install.sh | sh)?"; then
    curl -fsSL https://tailscale.com/install.sh | sh && return 0
  fi
  warn "tailscale not installed; Loopback will be configured local-only until you enable Tailscale in the dashboard"
  return 1
}

case "$INGRESS" in
  cloudflare) install_cloudflared || true ;;
  tailscale) install_tailscale || true ;;
  both) install_cloudflared || true; install_tailscale || true ;;
  ngrok) have ngrok || warn "Install the official ngrok agent and configure its v3 credentials file before public setup" ;;
esac

# ------------------------------------------------------------------ hand over to the Python installer
info "Running the Loopback installer..."
EXTRA=()
[ "$HAS_SOURCE" = 1 ] || EXTRA=(--source "$ROOT")
set +e
# ${arr[@]+"${arr[@]}"} keeps empty arrays safe under 'set -u' on bash 3.2 (macOS).
"$PY" "$ROOT/runtime/cli.py" install ${ARGS[@]+"${ARGS[@]}"} ${EXTRA[@]+"${EXTRA[@]}"}
rc=$?
set -e
exit $rc
