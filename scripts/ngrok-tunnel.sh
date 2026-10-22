#!/bin/sh
# Stable public tunnel for the Minis bridge - RUN ON THE PHONE OR ON THE MAC.
#
# Use this when cloudflared's quick tunnel keeps changing its hostname (every
# phone resume creates a new trycloudflare.com URL, which breaks the claude.ai
# connector).  ngrok's fixed development domain is stable.
#
# Setup once (per device/host):
#   1) sign up at https://dashboard.ngrok.com (free)
#   2) copy your authtoken
#   3) run:   NGROK_AUTHTOKEN=<token> sh ngrok-tunnel.sh
#
# If ngrok is not installed, the script downloads the linux-arm64 static
# build into /var/minis/shared/claude-bridge/bin/ and re-executes.
set -e
NAME=${NGROK_TTL:-minis-bridge}
AUTHTKN=${NGROK_AUTHTOKEN:-}
ORIGIN_PORT=${ORIGIN_PORT:-8901}
STATE_DIR=${MINIS_BRIDGE_STATE_DIR:-/var/minis/shared/claude-bridge}
BIN_DIR=$STATE_DIR/bin
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case "$ARCH" in x86_64) ARCH=amd64;; aarch64) ARCH=arm64;; armv7l) ARCH=arm;; esac

if ! command -v ngrok >/dev/null 2>&1; then
  if [ -x "$BIN_DIR/ngrok" ]; then PATH="$BIN_DIR:$PATH"; fi
fi
if ! command -v ngrok >/dev/null 2>&1; then
  echo "== installing ngrok (static binary) =="
  mkdir -p "$BIN_DIR"
  cd "$BIN_DIR"
  URL="https://bin.equinox.io/c/bNyj2T5f7hD/ngrok-${OS}-arm64.zip"
  [ "$ARCH" = "amd64" ] && URL="https://bin.equinox.io/c/bNyj2T5f7hD/ngrok-${OS}-amd64.zip"
  wget -q "$URL" -O ngrok.zip && unzip -oq ngrok.zip && chmod +x ngrok && rm -f ngrok.zip
  export PATH="$BIN_DIR:$PATH"
fi

if [ -n "$AUTHTKN" ]; then
  ngrok config add-authtoken "$AUTHTKN" >/dev/null
fi
if [ ! -f ~/.ngrok2/ngrok.yml ] && [ ! -f ~/.config/ngrok/ngrok.yml ]; then
  echo "Set NGROK_AUTHTOKEN to proceed."
  exit 1
fi

echo "== ngrok tunnel to 127.0.0.1:$ORIGIN_PORT =="
echo "First run allocates a fixed free development domain for your account."
echo "Run:   ngrok http --domain=<your-assigned>.ngrok-free.app $ORIGIN_PORT"
echo "URL in the console stays the same across restarts."
echo
echo "Then put that URL into claude.ai -> Customize -> Connectors -> 미니스 브리지:"
echo "   <url>/mcp?token=$(cat $STATE_DIR/token)"
echo "and click '다시 연결' / reconnect."
echo
echo "For a fully autonomous re-register on URL change, run scripts/sync_connector.py."
EOF
chmod +x scripts/ngrok-tunnel.sh
echo written