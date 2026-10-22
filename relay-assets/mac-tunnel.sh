#!/bin/sh
# Stable public tunnel for the Minis bridge — RUN THIS ON YOUR MAC.
#
# Why: a Cloudflare quick tunnel on the phone changes its URL every time the
# phone app is suspended, which breaks the connector URL inside the Claude
# app. If the tunnel lives on an always-on Mac, the URL stays the same and the
# Mac simply forwards to the iPhone over the LAN.
#
# The phone's DHCP lease also changes (192.168.123.115 -> 192.168.219.44 was
# observed on 2026-10-22), so this script AUTO-DISCOVERS the phone by
# scanning the local /24 for whoever answers /health on the bridge port.
#
# Usage (on the Mac, same Wi-Fi):
#   1) install cloudflared:  brew install cloudflared
#   2) run:                  sh mac-tunnel.sh
#   3) copy the printed URL into the Claude app connector
#
# The URL then stays valid as long as this script runs on the Mac.
set -e
PORT="${PORT:-8900}"
TOKEN_FILE="${TOKEN_FILE:-/var/minis/shared/claude-bridge/token}"
STATE="$HOME/.minis-phone-ip"

discover_phone() {
  # explicit override wins
  if [ -n "$PHONE_IP" ]; then echo "$PHONE_IP"; return 0; fi
  # last known good
  if [ -f "$STATE" ]; then
    C=$(cat "$STATE")
    if curl -s --max-time 2 "http://$C:$PORT/health" 2>/dev/null | grep -q '"ok"'; then
      echo "$C"; return 0
    fi
  fi
  # scan the local /24 in parallel
  MYIP=$(ipconfig getifaddr en0 2>/dev/null \
      || ipconfig getifaddr en1 2>/dev/null \
      || ifconfig 2>/dev/null | awk '/inet /{print $2; exit}')
  [ -z "$MYIP" ] && return 1
  PREFIX=$(echo "$MYIP" | awk -F. '{print $1"."$2"."$3}')
  FOUND=$(for i in $(seq 1 254); do
            C="$PREFIX.$i"
            if curl -s --max-time 1 "http://$C:$PORT/health" 2>/dev/null | grep -q '"ok"'; then
              echo "$C"
            fi
          done | head -1)
  [ -z "$FOUND" ] && return 1
  echo "$FOUND"
}

echo "== Minis bridge stable tunnel (Mac side) =="
PHONE_IP=$(discover_phone || true)
if [ -z "$PHONE_IP" ]; then
  echo "!! phone bridge not found on this subnet."
  echo "   - check the Mac and the iPhone are on the SAME Wi-Fi"
  echo "   - check iSH 'Local Network' permission is ON (Settings > iSH)"
  echo "   - set it manually:  PHONE_IP=192.168.x.y sh mac-tunnel.sh"
  exit 1
fi
echo "$PHONE_IP" > "$STATE"
echo "phone bridge : http://$PHONE_IP:$PORT  (auto-discovered)"

if ! curl -s --max-time 5 "http://$PHONE_IP:$PORT/health" | grep -q '"ok"'; then
  echo "!! phone bridge not reachable — check that Minis is open and"
  echo "   the iSH 'Local Network' permission is ON (Settings > iSH)."
  exit 1
fi
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "!! cloudflared not found: brew install cloudflared"
  exit 1
fi

LOG="$HOME/minis-tunnel.log"
echo "starting cloudflared (http2) ... log: $LOG"
nohup cloudflared tunnel --protocol http2 --url "http://$PHONE_IP:$PORT" --no-autoupdate \
  >> "$LOG" 2>&1 &
sleep 12
URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOG" | tail -1)
if [ -z "$URL" ]; then
  echo "!! tunnel did not start — see $LOG"; exit 1
fi

TOKEN=""
if [ -f "$TOKEN_FILE" ]; then TOKEN=$(cat "$TOKEN_FILE"); fi
echo
echo "=================================================="
echo " Claude connector URL (paste into the app):"
echo "   $URL/mcp?token=$TOKEN"
echo
echo " Authentication setting : No sign-in"
echo " Transport              : Streamable HTTP (default)"
echo "=================================================="
echo
echo "Keep this terminal open. Stop with: pkill cloudflared"
