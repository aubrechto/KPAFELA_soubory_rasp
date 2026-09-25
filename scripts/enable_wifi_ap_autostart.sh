#!/usr/bin/env bash
set -euo pipefail

# Enable automatic start of the KAPFELA Wi-Fi access point after boot.
if [[ "$(id -u)" -ne 0 ]]; then
    echo "Spust tento skript jako root: sudo $0" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
START_SCRIPT="$SCRIPT_DIR/start_wifi_ap.sh"

if command -v nmcli >/dev/null 2>&1 && nmcli connection show kapfela-ap >/dev/null 2>&1; then
    # NetworkManager: let the kapfela-ap connection come up automatically and
    # make sure it always wins over any other Wi-Fi profile used in the
    # meantime (e.g. when the AP was stopped to reach a normal network).
    nmcli connection modify kapfela-ap connection.autoconnect yes
    nmcli connection modify kapfela-ap connection.autoconnect-priority 100
    nmcli connection up kapfela-ap || true

    # Belt and braces: force kapfela-ap up on every boot regardless of which
    # network profile was last active, so a reboot always brings the AP back
    # even after using stop_wifi_ap.sh to join a normal network.
    cat > /etc/systemd/system/kapfela-ap.service <<EOF
[Unit]
Description=KAPFELA Wi-Fi AP (force autostart)
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/bin/sleep 3
ExecStart=$START_SCRIPT

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now kapfela-ap.service
    echo "AP autostart zapnuty (NetworkManager: kapfela-ap autoconnect + kapfela-ap.service)."
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

