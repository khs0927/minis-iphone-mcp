#!/bin/sh
# Install the Minis iPhone MCP bridge on an iPhone running Alpine (iSH / a-Shell).
#
#   sh scripts/install_iphone.sh              # install + start both roles
#   sh scripts/install_iphone.sh --no-start   # install only
#
set -e

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
STATE_DIR=${MINIS_BRIDGE_STATE_DIR:-/var/minis/shared/claude-bridge}
BIN_DIR=${MINIS_BRIDGE_BIN_DIR:-/usr/local/bin}
PY=$(command -v python3 || command -v python)

echo "== Minis iPhone MCP bridge installer =="
echo "repo   : $REPO_DIR"
echo "state  : $STATE_DIR"
echo "python : $($PY -V 2>&1)"

[ -n "$PY" ] || { echo "!! python3 not found"; exit 1; }

mkdir -p "$STATE_DIR" "$BIN_DIR"

# --- package -----------------------------------------------------------------
if command -v pip >/dev/null 2>&1; then
  "$PY" -m pip install --no-deps -e "$REPO_DIR" >/dev/null 2>&1 \
    || echo "   (editable install skipped - running from the source tree)"
fi

# --- CLI wrapper -------------------------------------------------------------
cat > "$BIN_DIR/minis-bridge" <<EOF
#!/bin/sh
exec $PY -m minis_bridge.cli "\$@"
EOF
chmod +x "$BIN_DIR/minis-bridge"

# --- state -------------------------------------------------------------------
if [ ! -f "$STATE_DIR/token" ]; then
  "$PY" - <<EOF
import sys
sys.path.insert(0, "$REPO_DIR")
from minis_bridge.config import CONFIG
print("token:", CONFIG.token())
EOF
fi

# --- start -------------------------------------------------------------------
if [ "$1" != "--no-start" ]; then
  minis-bridge start --role private
  minis-bridge start --role public
  minis-bridge status
  echo
  echo "Tunnel the PUBLIC port (8901) and register the URL as an MCP connector:"
  echo "  cloudflared tunnel --protocol http2 --url http://127.0.0.1:8901"
fi

echo
echo "Done.  'minis-bridge selftest' verifies the installation."
