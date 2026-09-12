#!/usr/bin/env bash
set -euo pipefail

# Configure Raspberry Pi as a private Wi-Fi network for the ESP devices.
if [[ "$(id -u)" -ne 0 ]]; then
    echo "Spust skript jako root: sudo $0" >&2
    exit 1
fi

WIFI_INTERFACE="${WIFI_INTERFACE:-wlan0}"
AP_SSID="${AP_SSID:-KAPFELA-ESP}"
AP_PASSWORD="${AP_PASSWORD:-kapfela-esp-1234}"
AP_IP="${AP_IP:-192.168.50.1}"
AP_CIDR="${AP_CIDR:-192.168.50.0/24}"
DHCP_START="${DHCP_START:-192.168.50.50}"
DHCP_END="${DHCP_END:-192.168.50.150}"
AP_BAND="${AP_BAND:-bg}"
AP_CHANNEL="${AP_CHANNEL:-6}"
AP_COUNTRY="${AP_COUNTRY:-US}"

if [[ ${#AP_PASSWORD} -lt 8 ]]; then
    echo "AP_PASSWORD musi mit alespon 8 znaku." >&2
    exit 1
fi
if ! command -v ip >/dev/null 2>&1; then
    echo "Chybi prikaz ip (balicek iproute2)." >&2
    exit 1
fi

if command -v rfkill >/dev/null 2>&1; then
    rfkill unblock wifi || true
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y hostapd dnsmasq mosquitto mosquitto-clients

mkdir -p /etc/kapfela-backup
STAMP="$(date +%Y%m%d%H%M%S)"
for file in /etc/dnsmasq.conf /etc/hostapd/hostapd.conf; do
    if [[ -f "$file" ]]; then
        cp -a "$file" "/etc/kapfela-backup/$(basename "$file").$STAMP.bak"
    fi
done

cat > /etc/hostapd/hostapd.conf <<EOF
interface=$WIFI_INTERFACE
driver=nl80211
ssid=$AP_SSID
country_code=$AP_COUNTRY
hw_mode=g
channel=$AP_CHANNEL
ieee80211d=0
ieee80211n=0
ieee80211ac=0
wmm_enabled=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=$AP_PASSWORD
wpa_key_mgmt=WPA-PSK
wpa_pairwise=CCMP
rsn_pairwise=CCMP
EOF

cat > /etc/dnsmasq.d/kapfela-ap.conf <<EOF
interface=$WIFI_INTERFACE
bind-interfaces
port=53
dhcp-range=$DHCP_START,$DHCP_END,255.255.255.0,12h
dhcp-option=3,$AP_IP
dhcp-option=6,$AP_IP
EOF

# NetworkManager is common on Raspberry Pi OS Bookworm; configure a persistent
# connection when it is available. Older Raspberry Pi OS can use dhcpcd below.
if command -v nmcli >/dev/null 2>&1; then
    AP_BACKEND="NetworkManager"
    nmcli connection delete kapfela-ap >/dev/null 2>&1 || true
    nmcli connection add type wifi ifname "$WIFI_INTERFACE" con-name kapfela-ap \
        ssid "$AP_SSID" wifi-sec.key-mgmt wpa-psk \
        wifi-sec.proto rsn wifi-sec.psk "$AP_PASSWORD" ipv4.method shared \
        ipv4.addresses "$AP_IP/24" ipv6.method disabled connection.autoconnect no
    nmcli connection modify kapfela-ap 802-11-wireless.mode ap \
        802-11-wireless.ssid "$AP_SSID" \
        802-11-wireless.band "$AP_BAND" 802-11-wireless.channel "$AP_CHANNEL" \
        802-11-wireless-security.proto rsn \
        802-11-wireless-security.pairwise ccmp \
        connection.autoconnect no
    nmcli connection up kapfela-ap
    systemctl disable --now hostapd dnsmasq >/dev/null 2>&1 || true
elif command -v dhcpcd >/dev/null 2>&1; then
    AP_BACKEND="hostapd + dnsmasq"
    if ! grep -q '^# KAPFELA AP$' /etc/dhcpcd.conf; then
        cat >> /etc/dhcpcd.conf <<EOF

# KAPFELA AP
interface $WIFI_INTERFACE
static ip_address=$AP_IP/24
nohook wpa_supplicant
EOF
    fi
    ip addr flush dev "$WIFI_INTERFACE"
    ip addr add "$AP_IP/24" dev "$WIFI_INTERFACE"
    ip link set "$WIFI_INTERFACE" up
else
    AP_BACKEND="manual"
    echo "Nenalezeno nmcli ani dhcpcd; nastav $WIFI_INTERFACE na $AP_IP/24 rucne." >&2
fi

cat > /etc/mosquitto/conf.d/kapfela.conf <<EOF
listener 1883 0.0.0.0
allow_anonymous true
EOF

systemctl enable --now mosquitto
if [[ "$AP_BACKEND" == "hostapd + dnsmasq" ]]; then
    systemctl unmask hostapd >/dev/null 2>&1 || true
    systemctl disable dnsmasq hostapd >/dev/null 2>&1 || true
    systemctl start dnsmasq hostapd
    systemctl restart dnsmasq hostapd
fi
systemctl restart mosquitto

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
    ufw allow in on "$WIFI_INTERFACE" to any port 53 comment 'KAPFELA AP DNS'
    ufw allow in on "$WIFI_INTERFACE" to any port 67 proto udp comment 'KAPFELA AP DHCP'
    ufw allow in on "$WIFI_INTERFACE" to any port 1883 proto tcp comment 'KAPFELA AP MQTT'
    ufw allow in on "$WIFI_INTERFACE" to any port 8000 proto tcp comment 'KAPFELA AP dashboard'
fi

echo "KAPFELA AP je aktivni."
echo "SSID: $AP_SSID"
echo "IP Raspberry: $AP_IP"
echo "DHCP: $DHCP_START - $DHCP_END"
echo "MQTT: $AP_IP:1883"
echo "Dashboard: http://$AP_IP:8000"
echo "AP backend: $AP_BACKEND"
echo "Zalohy: /etc/kapfela-backup"
