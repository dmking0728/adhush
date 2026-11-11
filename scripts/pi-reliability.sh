#!/usr/bin/env bash
# Make an always-on Raspberry Pi recover by itself. Safe to run more than once.
#
#   * Wi-Fi power saving off: the Pi Zero W's Wi-Fi can doze off and never
#     come back, leaving the Pi running but unreachable.
#   * Hardware watchdog: if the system freezes, the Pi's watchdog chip
#     restarts it within about 15 seconds.
#   * Wi-Fi check: every 2 minutes, ping the router. After 3 misses in a row,
#     restart Wi-Fi; if it is still down 3 checks later, reboot.
#   * Logs kept across restarts (capped at 50 MB) so you can see what happened.
#
#   sudo ./scripts/pi-reliability.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo ./scripts/pi-reliability.sh" >&2
  exit 1
fi

echo "==> Wi-Fi power saving off"
if systemctl is-active --quiet NetworkManager; then
  mkdir -p /etc/NetworkManager/conf.d
  cat > /etc/NetworkManager/conf.d/adhush-wifi-powersave-off.conf <<'EOF'
# Written by AdHush pi-reliability.sh. 2 = disable power saving.
[connection]
wifi.powersave = 2
EOF
  # Apply now to the active Wi-Fi connection too (takes effect on reconnect).
  while IFS=: read -r name type; do
    [[ $type == 802-11-wireless ]] && nmcli connection modify "$name" 802-11-wireless.powersave 2 || true
  done < <(nmcli -t -f NAME,TYPE connection show)
fi
command -v iw >/dev/null && iw dev wlan0 set power_save off 2>/dev/null || true

echo "==> Hardware watchdog (restart if the system freezes)"
mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/adhush-watchdog.conf <<'EOF'
# Written by AdHush pi-reliability.sh. The Pi's watchdog allows at most ~15 s.
[Manager]
RuntimeWatchdogSec=14
RebootWatchdogSec=2min
EOF

echo "==> Wi-Fi check every 2 minutes"
cat > /usr/local/sbin/adhush-netcheck <<'EOF'
#!/usr/bin/env bash
# Ping the router; restart Wi-Fi after 3 misses, reboot after 6.
STATE=/run/adhush-netcheck.misses
GW="$(ip route | awk '/^default/ {print $3; exit}')"
misses="$(cat "$STATE" 2>/dev/null || echo 0)"
if [[ -n $GW ]] && ping -c 2 -W 3 "$GW" >/dev/null 2>&1; then
  [[ $misses -gt 0 ]] && echo "Router reachable again after $misses missed checks"
  echo 0 > "$STATE"
  exit 0
fi
misses=$((misses + 1))
echo "$misses" > "$STATE"
echo "Router ${GW:-(no default route)} not reachable ($misses missed checks)"
if [[ $misses -ge 6 ]]; then
  echo "Still offline after restarting Wi-Fi: rebooting"
  systemctl reboot
elif [[ $misses -eq 3 ]]; then
  echo "Restarting Wi-Fi"
  if systemctl is-active --quiet NetworkManager; then
    nmcli radio wifi off; sleep 3; nmcli radio wifi on
  else
    ip link set wlan0 down; sleep 3; ip link set wlan0 up
  fi
fi
EOF
chmod 755 /usr/local/sbin/adhush-netcheck
cat > /etc/systemd/system/adhush-netcheck.service <<'EOF'
[Unit]
Description=AdHush: check Wi-Fi, restart it or reboot if it stays down

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/adhush-netcheck
EOF
cat > /etc/systemd/system/adhush-netcheck.timer <<'EOF'
[Unit]
Description=AdHush: check Wi-Fi every 2 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
EOF

echo "==> Keep logs across restarts (max 50 MB)"
mkdir -p /etc/systemd/journald.conf.d /var/log/journal
cat > /etc/systemd/journald.conf.d/zz-adhush-persistent.conf <<'EOF'
# Written by AdHush pi-reliability.sh. Raspberry Pi OS keeps logs in RAM only,
# so a freeze or power cut erases the clues. Keep them, but cap the size.
[Journal]
Storage=persistent
SystemMaxUse=50M
EOF
systemd-tmpfiles --create --prefix /var/log/journal >/dev/null 2>&1 || true

systemctl daemon-reload
systemctl daemon-reexec   # picks up the watchdog setting without a reboot
systemctl restart systemd-journald
systemctl enable --now adhush-netcheck.timer
systemctl reload NetworkManager 2>/dev/null || true

echo "Done. Wi-Fi power save off, watchdog on, Wi-Fi check every 2 min, logs kept."
