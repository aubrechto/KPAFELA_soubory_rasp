#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Spust skript jako root: sudo $0" >&2
    exit 1
fi

if command -v nmcli >/dev/null 2>&1; then
    nmcli connection down kapfela-ap || true
else
    systemctl stop hostapd dnsmasq
fi

echo "KAPFELA AP zastaven."