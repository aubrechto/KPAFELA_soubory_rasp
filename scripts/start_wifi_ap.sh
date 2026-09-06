#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Spust skript jako root: sudo $0" >&2
    exit 1
fi

if command -v rfkill >/dev/null 2>&1; then
    rfkill unblock wifi || true
fi

if command -v nmcli >/dev/null 2>&1; then
    nmcli radio wifi on
    nmcli connection up kapfela-ap
else
    systemctl start dnsmasq hostapd
fi

echo "KAPFELA AP spusten."
ip -4 address show wlan0