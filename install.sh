#!/usr/bin/env bash
set -Eeuo pipefail

export PATH="$HOME/.local/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="${LOOPBACK_CONFIG_DIR:-$HOME/.config/loopback}"
BIN_DIR="$HOME/.local/bin"
CLI="$BIN_DIR/loopback"
VENV="$CFG/venv"

YELLOW='\033[38;5;220m'
BLACK='\033[38;5;232m'
CYAN='\033[38;5;45m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

banner(){
  printf "${YELLOW}${BOLD}"
  cat <<'EOF'
██╗      ██████╗  ██████╗ ██████╗ ██████╗  █████╗  ██████╗██╗  ██╗
██║     ██╔═══██╗██╔═══██╗██╔══██╗██╔══██╗██╔══██╗██╔════╝██║ ██╔╝
██║     ██║   ██║██║   ██║██████╔╝██████╔╝███████║██║     █████╔╝
██║     ██║   ██║██║   ██║██╔═══╝ ██╔══██╗██╔══██║██║     ██╔═██╗
███████╗╚██████╔╝╚██████╔╝██║     ██████╔╝██║  ██║╚██████╗██║  ██╗
╚══════╝ ╚═════╝  ╚═════╝ ╚═╝     ╚═════╝ ╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝
EOF
  printf "${RESET}${DIM}reverse MCP bridge installer${RESET}\n\n"
}

die(){ printf '✗ %s\n' "$*" >&2; exit 1; }
ok(){ printf '✓ %s\n' "$*"; }
have(){ command -v "$1" >/dev/null 2>&1; }

NONINTERACTIVE=0
INGRESS=""
HOSTNAME_VALUE=""
PORT=2026
CLOUDFLARE_TOKEN_FILE=""
CLOUDFLARE_TUNNEL_ID_ARG=""
AUTOSTART=1

while (($#)); do
  case "$1" in
    --non-interactive) NONINTERACTIVE=1; shift ;;
    --ingress) INGRESS="${2:?cloudflare|tailscale|local}"; shift 2 ;;
    --host) HOSTNAME_VALUE="${2:?hostname required}"; shift 2 ;;
    --port) PORT="${2:?port required}"; shift 2 ;;
    --cloudflare-token-file) CLOUDFLARE_TOKEN_FILE="${2:?token file required}"; shift 2 ;;
    --cloudflare-tunnel-id) CLOUDFLARE_TUNNEL_ID_ARG="${2:?tunnel id required}"; shift 2 ;;
    --no-autostart) AUTOSTART=0; shift ;;
    -h|--help)
      echo "Usage: ./install.sh [--non-interactive --ingress cloudflare|tailscale|local --host FQDN] [--port 2026] [--cloudflare-token-file PATH --cloudflare-tunnel-id UUID]"
      exit 0
      ;;
    *) die "unknown installer option: $1" ;;
  esac
done

banner

OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
  Linux|Darwin) ;;
  *) die "Native $OS installation is not packaged yet. Windows users should use WSL for this release." ;;
esac

have python3 || die "Python 3.11+ is required"
python3 - <<'PY' || die "Python 3.11+ is required"
import sys
raise SystemExit(0 if sys.version_info >= (3,11) else 1)
PY

if [[ -z "$INGRESS" ]]; then
  if [[ "$NONINTERACTIVE" == 1 ]]; then
    INGRESS=local
  else
    printf "${CYAN}Choose ingress${RESET}\n"
    echo "  1) Cloudflare Tunnel   custom domain / best for generic web agents"
    echo "  2) Tailscale Funnel    public *.ts.net URL"
    echo "  3) Local only          no public ingress"
    printf "> "
    read -r choice
    case "$choice" in
      1) INGRESS=cloudflare ;;
      2) INGRESS=tailscale ;;
      3) INGRESS=local ;;
      *) die "invalid choice" ;;
    esac
  fi
fi

install_cloudflared(){
  have cloudflared && return 0
  printf "Installing cloudflared...\n"
  if [[ "$OS" == Darwin ]]; then
    have brew || die "Homebrew is required to auto-install cloudflared on macOS"
    brew install cloudflared
    return
  fi

  local machine
  case "$ARCH" in
    x86_64|amd64) machine=amd64 ;;
    aarch64|arm64) machine=arm64 ;;
    *) die "Unsupported architecture for automatic cloudflared install: $ARCH" ;;
  esac
  mkdir -p "$BIN_DIR"
  have curl || die "curl is required to download cloudflared"
  curl -fL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$machine" -o "$BIN_DIR/cloudflared"
  chmod 755 "$BIN_DIR/cloudflared"
  export PATH="$BIN_DIR:$PATH"
}

install_tailscale(){
  have tailscale && return 0
  if [[ "$OS" == Darwin ]]; then
    have brew || die "Homebrew is required to auto-install Tailscale on macOS"
    brew install --cask tailscale
    return
  fi
  have curl || die "curl is required to install Tailscale"
  curl -fsSL https://tailscale.com/install.sh | sh
}

mkdir -p "$CFG" "$BIN_DIR"
chmod 700 "$CFG"

printf "${CYAN}Preparing Python runtime...${RESET}\n"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip wheel >/dev/null
"$VENV/bin/pip" install -r "$ROOT/requirements.txt"

cp "$ROOT/runtime/server.py" "$CFG/server.py"
cp "$ROOT/bin/loopback" "$CLI"
chmod 755 "$CLI"

if [[ ! -s "$CFG/token" ]]; then
  umask 077
  python3 - <<'PY' > "$CFG/token"
import secrets
print(secrets.token_hex(32))
PY
  chmod 600 "$CFG/token"
fi

PUBLIC_HOST=localhost
CLOUDFLARE_TUNNEL_ID=""

case "$INGRESS" in
  cloudflare)
    install_cloudflared
    if [[ -n "$CLOUDFLARE_TOKEN_FILE" ]]; then
      [[ -s "$CLOUDFLARE_TOKEN_FILE" ]] || die "Cloudflare token file does not exist or is empty"
      [[ -n "$CLOUDFLARE_TUNNEL_ID_ARG" ]] || die "--cloudflare-tunnel-id is required with --cloudflare-token-file"
      [[ -n "$HOSTNAME_VALUE" ]] || die "--host is required with --cloudflare-token-file"
      [[ "$HOSTNAME_VALUE" == *.* ]] || die "A full hostname is required"
      install -m 600 "$CLOUDFLARE_TOKEN_FILE" "$CFG/cloudflared.token"
      CLOUDFLARE_TUNNEL_ID="$CLOUDFLARE_TUNNEL_ID_ARG"
      PUBLIC_HOST="$HOSTNAME_VALUE"
    else
      echo
      printf "${CYAN}Cloudflare authentication${RESET}\n"
      cloudflared tunnel login

      if [[ -z "$HOSTNAME_VALUE" ]]; then
        printf "Public hostname (example: loopback.example.com): "
        read -r HOSTNAME_VALUE
      fi
      [[ "$HOSTNAME_VALUE" == *.* ]] || die "A full hostname is required"

      TUNNEL_NAME="loopback-$(hostname | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9.-')"
      cloudflared tunnel create "$TUNNEL_NAME" || true

      CLOUDFLARE_TUNNEL_ID="$(cloudflared tunnel list --output json | python3 -c 'import json,sys; n=sys.argv[1]; a=json.load(sys.stdin); print(next((x["id"] for x in a if x.get("name")==n), ""))' "$TUNNEL_NAME")"
      [[ -n "$CLOUDFLARE_TUNNEL_ID" ]] || die "Could not resolve Cloudflare tunnel ID"

      CRED="$HOME/.cloudflared/$CLOUDFLARE_TUNNEL_ID.json"
      [[ -f "$CRED" ]] || die "Cloudflare tunnel credentials were not created"

      cat > "$CFG/cloudflared.yml" <<EOF
tunnel: $CLOUDFLARE_TUNNEL_ID
credentials-file: $CRED
ingress:
  - hostname: $HOSTNAME_VALUE
    service: http://127.0.0.1:$PORT
  - service: http_status:404
EOF
      cloudflared tunnel route dns "$CLOUDFLARE_TUNNEL_ID" "$HOSTNAME_VALUE"
      PUBLIC_HOST="$HOSTNAME_VALUE"
    fi
    ;;

  tailscale)
    install_tailscale
    if ! tailscale status >/dev/null 2>&1; then
      echo "Opening Tailscale authentication..."
      tailscale up
    fi
    PUBLIC_HOST="$(tailscale status --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("Self",{}).get("DNSName","").rstrip("."))')"
    [[ -n "$PUBLIC_HOST" ]] || die "Could not determine Tailscale DNS name"
    ;;

  local) ;;
  *) die "Unsupported ingress: $INGRESS" ;;
esac

cat > "$CFG/config.env" <<EOF
LOOPBACK_PORT=$PORT
LOOPBACK_PUBLIC_HOST=$PUBLIC_HOST
LOOPBACK_TIMEOUT=120
LOOPBACK_MAX_OUTPUT_BYTES=1048576
LOOPBACK_STARTUP_TIMEOUT=15
LOOPBACK_ACCESS_MODE=standard
LOOPBACK_INGRESS=$INGRESS
LOOPBACK_CLOUDFLARE_TUNNEL_ID=$CLOUDFLARE_TUNNEL_ID
LOOPBACK_CLOUDFLARE_TOKEN_FILE=$CFG/cloudflared.token
LOOPBACK_TAILSCALE_HTTPS_PORT=443
EOF
chmod 600 "$CFG/config.env"
printf '%s\n' "$PUBLIC_HOST" > "$CFG/public_host"

echo
if [[ "$AUTOSTART" == 1 ]] && command -v systemctl >/dev/null 2>&1; then
  "$CLI" autostart on
else
  "$CLI" up
fi

echo
printf "${YELLOW}${BOLD}Installed.${RESET}\n"
printf "Command:  %s\n" "$CLI"
printf "Endpoint: "
"$CLI" url
printf "Profile:  standard\n"
printf "\nUse ${CYAN}loopback doctor${RESET} for diagnostics.\n"
