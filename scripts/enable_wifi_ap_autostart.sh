#!/usr/bin/env bash
set -euo pipefail

# Enable automatic start of the KAPFELA Wi-Fi access point after boot.
if [[ "$(id -u)" -ne 0 ]]; then
    echo "Spust tento skript jako root: sudo $0" >&2
    exit 1
fi

if command -v nmcli >/dev/null 2>&1 && nmcli connection show kapfela-ap >/dev/null 2>&1; then
    # NetworkManager: let the kapfela-ap connection come up automatically.
    nmcli connection modify kapfela-ap connection.autoconnect yes
    nmcli connection up kapfela-ap || true
    echo "AP autostart zapnuty (NetworkManager: kapfela-ap autoconnect)."
elif command -v dhcpcd >/dev/null 2>&1; then
    # Legacy backend: start hostapd + dnsmasq at boot.
    systemctl unmask hostapd >/dev/null 2>&1 || true
    systemctl enable dnsmasq hostapd
    systemctl restart dnsmasq hostapd
    echo "AP autostart zapnuty (hostapd + dnsmasq)."
else
    echo "Nenalezeno nmcli ani dhcpcd; AP je treba spoustet rucne." >&2
    exit 1
fi
