#!/usr/bin/env bash
set -euo pipefail

# Configure Raspberry Pi as an NTP server for the directly connected Wi-Fi network.
if [[ "$(id -u)" -ne 0 ]]; then
    echo "Spust tento skript jako root: sudo $0" >&2
    exit 1
fi

if ! command -v ip >/dev/null 2>&1; then
    echo "Chybi prikaz ip (balicek iproute2)." >&2
    exit 1
fi

WIFI_INTERFACE="${WIFI_INTERFACE:-wlan0}"
WIFI_CIDR="${WIFI_CIDR:-}"

if [[ -z "$WIFI_CIDR" ]]; then
    WIFI_CIDR="$(ip -4 route show dev "$WIFI_INTERFACE" proto kernel scope link | awk 'NR == 1 { print $1 }')"
fi

if [[ -z "$WIFI_CIDR" ]]; then
    echo "Nelze zjistit IPv4 subnet rozhrani $WIFI_INTERFACE." >&2
    echo "Pouzij napr.: sudo WIFI_INTERFACE=wlan0 WIFI_CIDR=192.168.1.0/24 $0" >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y chrony

CHRONY_CONF="/etc/chrony/chrony.conf"
BACKUP="${CHRONY_CONF}.kapfela.$(date +%Y%m%d%H%M%S).bak"
cp -a "$CHRONY_CONF" "$BACKUP"

if ! grep -q '^# KAPFELA NTP server$' "$CHRONY_CONF"; then
    cat >> "$CHRONY_CONF" <<EOF

# KAPFELA NTP server
allow $WIFI_CIDR
makestep 1.0 3
rtcsync
EOF
fi

systemctl enable --now chrony
systemctl restart chrony

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
    ufw allow from "$WIFI_CIDR" to any port 123 proto udp comment 'KAPFELA local NTP'
fi

PI_IP="$(ip -4 addr show dev "$WIFI_INTERFACE" | awk '/inet / { sub(/\/.*/, "", $2); print $2; exit }')"
echo "NTP server je aktivni."
echo "Rozhrani: $WIFI_INTERFACE"
echo "Adresa Raspberry Pi: ${PI_IP:-nezjistena}"
echo "Povoleny subnet: $WIFI_CIDR"
echo "Zalohovana konfigurace: $BACKUP"
chronyc tracking