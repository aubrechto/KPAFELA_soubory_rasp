#!/usr/bin/env bash
set -u

WIFI_INTERFACE="${WIFI_INTERFACE:-wlan0}"
AP_CONNECTION="${AP_CONNECTION:-kapfela-ap}"
AP_SSID="${AP_SSID:-KAPFELA-ESP}"
AP_IP="${AP_IP:-192.168.50.1}"
AP_CHANNEL="${AP_CHANNEL:-6}"

fail=0
check() {
    if "$@"; then
        printf 'OK: %s\n' "$*"
    else
        printf 'CHYBA: %s\n' "$*" >&2
        fail=1
    fi
}

printf '=== KAPFELA Wi-Fi AP diagnostika ===\n'
printf 'Rozhrani: %s\nSSID: %s\nOcekavana IP: %s\nKanal: %s (2,4 GHz)\n\n' "$WIFI_INTERFACE" "$AP_SSID" "$AP_IP" "$AP_CHANNEL"

check ip link show "$WIFI_INTERFACE"
check rfkill list wifi

if command -v nmcli >/dev/null 2>&1; then
    printf '\n--- NetworkManager ---\n'
    nmcli radio wifi
    nmcli connection show "$AP_CONNECTION" 2>&1 || true
    nmcli connection show --active
    nmcli -f GENERAL.STATE,GENERAL.CONNECTION,802-11-wireless.mode,802-11-wireless.ssid,ipv4.method,ipv4.addresses device show "$WIFI_INTERFACE"
    check nmcli connection show --active | grep -F "$AP_CONNECTION"
else
    printf '\n--- hostapd/dnsmasq ---\n'
    systemctl --no-pager --full status hostapd || fail=1
    systemctl --no-pager --full status dnsmasq || fail=1
    ip -4 address show dev "$WIFI_INTERFACE"
fi

printf '\n--- Sluzby ---\n'
systemctl --no-pager --full status mosquitto || fail=1
ss -ltn '( sport = :1883 or sport = :8000 )' || true

printf '\n--- IP a sousedni zarizeni ---\n'
ip -4 address show dev "$WIFI_INTERFACE"
ip neigh show dev "$WIFI_INTERFACE" || true

if [[ "$fail" -eq 0 ]]; then
    printf '\nAP zakladni kontrola prosla.\n'
else
    printf '\nAP ma chyby. Zkontroluj radky oznacene CHYBA a stav sluzeb vyse.\n' >&2
fi
exit "$fail"
